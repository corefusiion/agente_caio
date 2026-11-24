from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import socket

import pytest

from caiocore.morning_briefing import CATEGORIES, MAX_AGE, Consent, assemble

NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
HOST = 'bbc.com'  # Synthetic fixture; no connection or claim of real content.


def record(category='world', **updates):
    result = {'category': category, 'title': 'Título de teste', 'text': 'Conteúdo de teste',
              'source_url': 'https://bbc.com/artigo', 'observed_at': NOW - timedelta(minutes=30),
              'retrieved_at': NOW - timedelta(minutes=10)}
    if category in ('sports', 'films'):
        result['interest'] = 'Interesse A'
    result.update(updates)
    return result


def consent(*categories, use_interests=False, source=HOST):
    return Consent(True, frozenset(categories), frozenset((c, source) for c in categories), use_interests)


def section(briefing, category):
    return next(s for s in briefing.sections if s.category == category)


def test_empty_is_explicit_not_invented_and_defaults_closed():
    result = assemble([record()], Consent(), NOW)
    assert result.generated_at == NOW
    assert tuple(s.category for s in result.sections) == CATEGORIES
    assert all(s.status == 'not_consented' and not s.items for s in result.sections)
    result = assemble([], consent('weather', 'world'), NOW)
    assert section(result, 'weather').status == 'unavailable'
    assert section(result, 'world').status == 'unavailable'
    assert section(result, 'usd_brl').status == 'not_consented'


def test_separate_enabled_categories_sources_and_interest_permission():
    records = [record('weather'), record('world'), record('sports'), record('films')]
    partial = Consent(True, frozenset(['weather', 'world', 'sports', 'films']),
                      frozenset([('world', HOST), ('sports', HOST), ('films', HOST)]), False)
    result = assemble(records, partial, NOW, interests={'sports': ['Interesse A'], 'films': ['Interesse A']})
    assert section(result, 'weather').status == 'unavailable'
    assert section(result, 'world').status == 'current'
    assert section(result, 'sports').status == 'not_consented'
    assert section(result, 'films').status == 'not_consented'
    result = assemble(records, consent('sports', 'films', use_interests=True), NOW,
                      interests={'sports': ['interesse a'], 'films': ['Outro']})
    assert section(result, 'sports').status == 'current'
    assert section(result, 'films').status == 'unavailable'
    assert section(assemble(records, consent('sports', use_interests=True), NOW), 'sports').status == 'unavailable'


def test_six_sections_and_timestamp_conversion_and_immutability():
    local = timezone(timedelta(hours=-3))
    recs = [record(c, observed_at=(NOW - timedelta(minutes=30)).astimezone(local),
                   retrieved_at=(NOW - timedelta(minutes=10)).astimezone(local)) for c in CATEGORIES]
    result = assemble(recs, consent(*CATEGORIES, use_interests=True), NOW.astimezone(local),
                      interests={'sports': ['Interesse A'], 'films': ['Interesse A']})
    assert all(s.status == 'current' and len(s.items) == 1 for s in result.sections)
    assert all(s.items[0].observed_at.tzinfo is timezone.utc and s.items[0].retrieved_at.tzinfo is timezone.utc for s in result.sections)
    assert result.generated_at.tzinfo is timezone.utc
    with pytest.raises(FrozenInstanceError):
        result.sections[0].status = 'current'
    recs[0]['title'] = 'Modificado'
    assert result.sections[0].items[0].title == 'Título de teste'


@pytest.mark.parametrize('category', CATEGORIES)
def test_age_boundary_and_old_publication_even_if_just_retrieved(category):
    policy = consent(category, use_interests=True)
    kw = {'interests': {category: ['Interesse A']}} if category in ('sports', 'films') else {}
    expired = record(category, observed_at=NOW - MAX_AGE[category], retrieved_at=NOW)
    recent = record(category, observed_at=NOW - MAX_AGE[category] + timedelta(microseconds=1), retrieved_at=NOW)
    assert section(assemble([expired], policy, NOW, **kw), category).status == 'unavailable'
    current = section(assemble([recent], policy, NOW, **kw), category)
    assert current.status == 'current'
    assert current.items[0].expires_at == NOW + timedelta(microseconds=1)
    assert section(assemble([recent], policy, current.items[0].expires_at, **kw), category).status == 'unavailable'


@pytest.mark.parametrize('updates', [
    {'observed_at': NOW.replace(tzinfo=None)}, {'retrieved_at': NOW.replace(tzinfo=None)},
    {'observed_at': NOW + timedelta(seconds=1)}, {'retrieved_at': NOW + timedelta(seconds=1)},
    {'observed_at': NOW, 'retrieved_at': NOW - timedelta(seconds=1)},
    {'observed_at': '2026-01-01T00:00:00Z'}, {'title': ''}, {'text': 'a\nb'},
    {'title': 'a\u202eb'}, {'interest': 'unexpected'}, {'extra': 'unexpected'},
])
def test_invalid_record_omitted_and_other_record_survives(updates):
    result = assemble([record(**updates), record('weather')], consent('world', 'weather'), NOW)
    assert section(result, 'world').status == 'unavailable'
    assert section(result, 'weather').status == 'current'


@pytest.mark.parametrize('url', [
    'http://bbc.com/x', 'https://user:pw@bbc.com/x', 'https://bbc.com:444/x',
    'https://bbc.com/x?token=secret', 'https://bbc.com/x#frag',
    'https://bbc.com/x?', 'https://bbc.com/x#', 'https://BBC.COM/x',
    'https://bbc.com.evil.org/x', 'https://bbc.com@evil.org/x',
    'https://127.0.0.1/x', 'https://[::1]/x', 'https://localhost/x',
    'https://abc.local/x', 'https://example.com/x', 'https://bbc.com\\@evil.org/x',
    'https://bbc.com/with space', 'https://bbc.com/\n', '//bbc.com/x',
])
def test_unsafe_untrusted_sources_not_returned(url):
    result = assemble([record(source_url=url)], consent('world'), NOW)
    assert section(result, 'world').status == 'unavailable'


def test_source_is_scoped_exactly_to_category_and_no_wildcards():
    policy = Consent(True, frozenset(['world', 'brazil']), frozenset([('world', HOST)]))
    result = assemble([record('world'), record('brazil')], policy, NOW)
    assert section(result, 'world').status == 'current'
    assert section(result, 'brazil').status == 'unavailable'
    for host in ('*.bbc.com', 'BBC.COM', '127.0.0.1', 'example.com', 'bbc.com:443'):
        with pytest.raises(ValueError):
            consent('world', source=host)


def test_malformed_top_level_inputs_fail_closed():
    for invalid in (None, True, 'yes'):
        with pytest.raises(ValueError):
            assemble([], invalid, NOW)
    for kwargs in ({'enabled': 1}, {'categories': {'world'}}, {'sources': frozenset(['bbc.com'])},
                   {'categories': frozenset(['unknown'])}, {'use_interests': 1}):
        with pytest.raises(ValueError):
            Consent(**kwargs)
    with pytest.raises(ValueError):
        assemble([], Consent(), NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        assemble([None] * 501, Consent(), NOW)
    with pytest.raises(ValueError):
        assemble([], consent('sports', use_interests=True), NOW, interests={'sports': ['\n']})
    # Unconsented interests are not inspected, used or returned.
    assert section(assemble([], Consent(), NOW, interests={'sports': ['\n']}), 'sports').status == 'not_consented'


def test_malformed_and_incomplete_records_do_not_crash_or_leak():
    incomplete = record()
    del incomplete['observed_at']
    result = assemble([None, 1, [], {}, incomplete, {'category': []}, record()], consent('world'), NOW)
    assert len(section(result, 'world').items) == 1


def test_no_network_or_implicit_clock(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('network forbidden')
    monkeypatch.setattr(socket, 'socket', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(socket, 'getaddrinfo', blocked)
    result = assemble([record('sports')], consent('sports', use_interests=True), NOW,
                      interests={'sports': ['Interesse A']})
    assert section(result, 'sports').status == 'current'
