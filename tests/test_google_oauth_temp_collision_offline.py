"""Collision after recovery must not unlink an uncreated file."""
import os

import pytest

from caiocore.server import google_oauth_local as mod


@pytest.mark.skipif(os.name != 'posix', reason='POSIX only')
def test_exclusive_create_collision_never_unlinks_existing_file(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    with mod.PrivateTokenStore(tmp_path) as store:
        name = '.google-token-' + 'a' * 32
        path = tmp_path / name
        original = mod.os.open
        monkeypatch.setattr(mod.secrets, 'token_hex', lambda n: 'a' * 32)
        def collide(file, flags, *args, **kwargs):
            if file == name and flags & os.O_EXCL:
                path.write_text('unrelated')
                path.chmod(0o600)
            return original(file, flags, *args, **kwargs)
        monkeypatch.setattr(mod.os, 'open', collide)
        record = {'refresh_token': 'synthetic', 'scopes': [mod.SCOPES['calendar_read']]}
        with pytest.raises(mod.OAuthError, match='Token storage failed'):
            store.save(record)
        assert path.read_text() == 'unrelated'
        with pytest.raises(mod.OAuthError, match='Ambiguous temporary'):
            store.load()
        with pytest.raises(mod.OAuthError, match='Ambiguous temporary'):
            store.delete_local()
        assert path.read_text() == 'unrelated'
        assert not (tmp_path / 'google-token.json').exists()
        path.unlink()  # Simulate owner inspection and resolution.
        monkeypatch.setattr(mod.os, 'open', original)
        store.save(record)
        assert store.load() == record
