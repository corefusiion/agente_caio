import json
import os
import socket
from concurrent.futures import ThreadPoolExecutor

import pytest

from caiocore.personalization import ConsentRequired, ProfileStore


@pytest.fixture
def store(tmp_path):
    tmp_path.chmod(0o700)
    return ProfileStore(tmp_path)


def test_defaults_and_opt_in_required(store, tmp_path):
    state = store.view()
    assert not any(state["consents"].values())
    assert state["preferences"] == {}
    with pytest.raises(ConsentRequired):
        store.set_preferences({"team": ["Equipe"]})
    with pytest.raises(ConsentRequired):
        store.set_consent("external_search", True)
    assert not (tmp_path / "preferences.json").exists()


def test_persistence_correction_and_selective_delete(store, tmp_path):
    store.set_consent("memory", True)
    store.set_preferences({"team": ["Equipe A"], "sports": ["Futebol"],
                           "films": ["Comédia"], "series": ["Ficção"],
                           "streaming": ["Plataforma"], "betting": ["Estatísticas"]})
    store.set_consent("external_search", True)
    reopened = ProfileStore(tmp_path)
    assert reopened.view()["preferences"]["team"] == ["Equipe A"]
    assert reopened.view()["consents"] == {"memory": True, "questions": False,
                                           "external_search": True, "proactivity": False}
    reopened.set_preferences({"team": ["Equipe B"], "betting": None})
    assert reopened.view()["preferences"]["team"] == ["Equipe B"]
    assert "betting" not in reopened.view()["preferences"]
    snapshot = reopened.view()
    snapshot["preferences"]["team"].append("alteração externa")
    assert reopened.view()["preferences"]["team"] == ["Equipe B"]
    assert (tmp_path / "preferences.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / ".preferences.lock").stat().st_mode & 0o777 == 0o600


def test_revocation_erases_and_requires_new_opt_in(store, tmp_path):
    store.set_consent("memory", True)
    for purpose in ("questions", "external_search", "proactivity"):
        store.set_consent(purpose, True)
    store.set_preferences({"team": ["Equipe"]})
    store.set_consent("memory", False)
    assert not (tmp_path / "preferences.json").exists()
    assert not any(store.view()["consents"].values())
    store.set_consent("memory", True)
    assert store.view()["preferences"] == {}
    assert not store.view()["consents"]["external_search"]


def test_independent_opt_out_and_clear(store, tmp_path):
    store.set_consent("memory", True)
    store.set_consent("proactivity", True)
    store.set_consent("proactivity", False)
    assert not store.view()["consents"]["proactivity"]
    assert store.view()["consents"]["memory"]
    store.set_preferences({"team": ["Equipe"]})
    leftover = tmp_path / (".preferences-" + "a" * 32 + ".tmp")
    leftover.write_text("dados antigos")
    store.clear()
    assert not leftover.exists()
    assert not (tmp_path / "preferences.json").exists()
    assert store.view()["preferences"] == {}
    store.clear()


@pytest.mark.parametrize("updates", [
    {}, {"password": ["secret"]}, {"team": "Equipe"}, {"team": [""]},
    {"team": [" Equipe"]}, {"team": ["a\nb"]}, {"team": ["x" * 81]},
    {"team": ["a"] * 21}, {"team": ["a", "a"]}, {"team": [1]},
])
def test_invalid_input_is_rejected_without_mutation(store, updates):
    store.set_consent("memory", True)
    store.set_preferences({"team": ["Equipe"]})
    before = store.view()
    with pytest.raises(ValueError):
        store.set_preferences(updates)
    assert store.view() == before


@pytest.mark.parametrize("purpose,granted", [("memory", 1), ("external_search", "yes"),
                                             ("unknown", True), (None, True)])
def test_invalid_consent(store, purpose, granted):
    with pytest.raises(ValueError):
        store.set_consent(purpose, granted)


def test_isolation_is_separate_caller_supplied_directories(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(mode=0o700)
    b.mkdir(mode=0o700)
    first, second = ProfileStore(a), ProfileStore(b)
    first.set_consent("memory", True)
    first.set_preferences({"team": ["Equipe"]})
    assert second.view()["preferences"] == {}
    second.clear()
    assert first.view()["preferences"]["team"] == ["Equipe"]


def test_file_and_directory_security(store, tmp_path):
    store.set_consent("memory", True)
    target = tmp_path / "preferences.json"
    target.chmod(0o644)
    with pytest.raises(PermissionError):
        store.view()
    target.chmod(0o600)
    tmp_path.chmod(0o755)
    with pytest.raises(PermissionError):
        store.view()
    tmp_path.chmod(0o700)
    target.unlink()
    outside = tmp_path / "outside"
    outside.write_text("unchanged")
    target.symlink_to(outside)
    with pytest.raises(OSError):
        store.view()
    store.clear()
    assert outside.read_text() == "unchanged"


def test_lock_and_directory_symlinks_refused(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    (private / ".preferences.lock").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(OSError):
        ProfileStore(private).view()
    alias = tmp_path / "alias"
    alias.symlink_to(private, target_is_directory=True)
    with pytest.raises(OSError):
        ProfileStore(alias).view()


def test_corrupt_profile_fails_closed_but_can_be_cleared(store, tmp_path):
    store.set_consent("memory", True)
    target = tmp_path / "preferences.json"
    target.write_text('{"version":1,"version":1}')
    with pytest.raises(ValueError):
        store.view()
    with pytest.raises(ValueError):
        store.set_preferences({"team": ["Equipe"]})
    store.clear()
    assert not any(store.view()["consents"].values())


def test_forged_or_oversized_profile_rejected(store, tmp_path):
    store.set_consent("memory", True)
    path = tmp_path / "preferences.json"
    state = store.view()
    state["consents"]["memory"] = False
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError):
        store.view()
    path.write_bytes(b"x" * 32769)
    with pytest.raises(ValueError):
        store.view()


def test_concurrent_updates_are_serialized(store):
    store.set_consent("memory", True)
    fields = ["team", "sports", "films", "series", "streaming", "betting"]
    with ThreadPoolExecutor(max_workers=len(fields)) as pool:
        list(pool.map(lambda field: store.set_preferences({field: [field]}), fields))
    assert store.view()["preferences"] == {field: [field] for field in fields}


def test_no_network_for_any_operation(store, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network must not be accessed")
    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    store.view()
    store.set_consent("memory", True)
    store.set_preferences({"films": ["Drama"]})
    for purpose in ("questions", "external_search", "proactivity"):
        store.set_consent(purpose, True)
    store.view()
    store.clear()
