from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from caiocore.wellbeing import CategoryPreference, Config, Presentation, suggest
from caiocore.wellbeing.planner import MESSAGES

UTC = timezone.utc
NOW = datetime(2030, 1, 2, 12, 0, tzinfo=UTC)


def pref(category="agua", **kwargs):
    values = {"consent": True, "consented_at": NOW - timedelta(days=1),
              "anchor_date": date(2030, 1, 1)}
    values.update(kwargs)
    return CategoryPreference(category, **values)


def config(*prefs, **kwargs):
    return Config(enabled=True, categories=prefs, **kwargs)


def test_defaults_and_separate_explicit_consent():
    assert suggest(Config(), "agua", NOW, ()) is None
    c = config(pref("agua"), CategoryPreference("mente"))
    assert suggest(c, "agua", NOW, ()) is not None
    assert suggest(c, "mente", NOW, ()) is None
    assert suggest(c, "colica_ciclo", NOW, ()) is None
    assert suggest(replace(c, enabled=False), "agua", NOW, ()) is None
    p = replace(c.categories[0], consent=False)
    assert suggest(replace(c, categories=(p,)), "agua", NOW, ()) is None
    with pytest.raises(ValueError):
        CategoryPreference("agua", consent=True)
    assert suggest(config(pref(consented_at=NOW + timedelta(seconds=1))), "agua", NOW, ()) is None


def test_all_opted_in_categories_have_discreet_neutral_push():
    push = set()
    for category in MESSAGES:
        result = suggest(config(pref(category)), category, NOW, ())
        assert result.category == category
        assert result.message == MESSAGES[category]
        push.add((result.push_title, result.push_body))
    assert len(push) == 1
    title, body = push.pop()
    for sensitive in ("mente", "água", "treino", "relação", "relacionamento", "cólica", "ciclo"):
        assert sensitive not in (title + body).lower()


def test_pauses_and_revocation():
    c = config(pref())
    assert suggest(replace(c, paused=True), "agua", NOW, ()) is None
    assert suggest(replace(c, paused_until=NOW + timedelta(seconds=1)), "agua", NOW, ()) is None
    assert suggest(replace(c, paused_until=NOW), "agua", NOW, ()) is not None
    assert suggest(replace(c, categories=(replace(c.categories[0], paused=True),)), "agua", NOW, ()) is None
    assert suggest(replace(c, categories=(replace(c.categories[0], consent=False, consented_at=None),)), "agua", NOW, ()) is None


def test_schedule_frequency_no_backlog_and_quiet_boundaries():
    p = pref(every_days=2, anchor_date=date(2030, 1, 2))
    c = config(p, quiet_start=time(13), quiet_end=time(7))
    assert suggest(c, "agua", NOW, ()) is not None
    assert suggest(c, "agua", NOW + timedelta(days=1), ()) is None
    assert suggest(c, "agua", NOW + timedelta(days=2), ()) is not None
    assert suggest(c, "agua", NOW + timedelta(minutes=1), ()) is None
    assert suggest(c, "agua", NOW - timedelta(days=1), ()) is None
    for clock, allowed in [(time(6, 59), False), (time(7), True), (time(12, 59), True), (time(13), False)]:
        x = NOW.replace(hour=clock.hour, minute=clock.minute)
        assert bool(suggest(replace(c, categories=(replace(p, local_time=clock),)), "agua", x, ())) == allowed
    daytime = replace(c, quiet_start=time(11), quiet_end=time(13))
    assert suggest(daytime, "agua", NOW, ()) is None


def test_rolling_caps_per_person_and_dedup():
    c = config(pref(), max_per_hour=2, max_per_day=3)
    recent = Presentation("treino", NOW - timedelta(minutes=10))
    assert suggest(c, "agua", NOW, (recent,)) is not None
    assert suggest(c, "agua", NOW, (recent, Presentation("mente", NOW - timedelta(minutes=20)))) is None
    assert suggest(c, "agua", NOW, (Presentation("treino", NOW - timedelta(hours=1)),)) is not None
    h = tuple(Presentation("treino", NOW - timedelta(hours=i)) for i in (2, 3, 4))
    assert suggest(c, "agua", NOW, h) is None
    # At exactly 24h, that event is outside the rolling window.
    h = tuple(Presentation("treino", NOW - timedelta(hours=i)) for i in (2, 3, 24))
    assert suggest(c, "agua", NOW, h) is not None
    assert suggest(c, "agua", NOW, (Presentation("agua", NOW - timedelta(hours=2)),)) is None
    assert suggest(c, "agua", NOW, (Presentation("treino", NOW + timedelta(seconds=1)),)) is None


def test_timezone_fold_cannot_repeat_after_confirmed_presentation():
    zone = ZoneInfo("America/New_York")
    first = datetime(2024, 11, 3, 1, 30, tzinfo=zone, fold=0)
    second = datetime(2024, 11, 3, 1, 30, tzinfo=zone, fold=1)
    p = CategoryPreference("mente", consent=True, consented_at=datetime(2024, 1, 1, tzinfo=UTC),
                           local_time=time(1, 30), anchor_date=date(2024, 1, 1))
    c = config(p, timezone="America/New_York", quiet_start=time(3), quiet_end=time(4))
    assert suggest(c, "mente", first, ()) is not None
    assert suggest(c, "mente", second, (Presentation("mente", first),)) is None


def test_dst_skipped_hour_is_not_caught_up():
    zone = ZoneInfo("America/New_York")
    p = CategoryPreference("agua", consent=True, consented_at=datetime(2024, 1, 1, tzinfo=UTC),
                           local_time=time(2, 30), anchor_date=date(2024, 1, 1))
    c = config(p, timezone="America/New_York", quiet_start=time(4), quiet_end=time(5))
    # UTC -> local conversion cannot create the nonexistent 02:30 on spring-forward day.
    for minute in range(24 * 60):
        instant = datetime(2024, 3, 10, tzinfo=zone).astimezone(UTC) + timedelta(minutes=minute)
        assert suggest(c, "agua", instant, ()) is None


@pytest.mark.parametrize("make", [
    lambda: Config(enabled=1),
    lambda: Config(timezone="not/a-zone"),
    lambda: Config(quiet_start=time(7), quiet_end=time(7)),
    lambda: Config(max_per_hour=3),
    lambda: Config(max_per_day=6),
    lambda: Config(categories=(pref(), pref())),
    lambda: CategoryPreference("xyz"),
    lambda: CategoryPreference("agua", consent="yes"),
    lambda: CategoryPreference("agua", consent=True, consented_at=datetime(2030, 1, 1)),
    lambda: CategoryPreference("agua", every_days=0),
    lambda: CategoryPreference("agua", local_time=time(12, 0, 1)),
    lambda: Presentation("agua", datetime(2030, 1, 1)),
    lambda: suggest(config(pref()), "agua", NOW.replace(tzinfo=None), ()),
    lambda: suggest(config(pref()), "agua", NOW, []),
])
def test_invalid_inputs_fail_closed(make):
    with pytest.raises(ValueError):
        make()
