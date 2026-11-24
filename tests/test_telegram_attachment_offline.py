import asyncio
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

import caiocore.channels.telegram_attachment_offline as mod


def update(data=b'hello', uid=123, chat_id=123, **changes):
    user = NS(id=uid, is_bot=False)
    doc = NS(file_id='synthetic_id-1', file_size=len(data), mime_type='text/plain',
             file_name='../../sensitive.txt')
    msg = NS(chat_id=chat_id, chat=NS(id=chat_id, type='private'), from_user=user,
             sender_chat=None, business_connection_id=None, forward_origin=None,
             document=doc)
    for k, v in changes.items():
        setattr(msg, k, v)
    return NS(message=msg, effective_user=user)


def config(*ids, enabled=True):
    return mod.OfflineAttachmentConfig(enabled=enabled, allowed_user_ids=tuple(ids))


def run(coro):
    return asyncio.run(coro)


def source(*chunks):
    async def gen(_file_id):
        for chunk in chunks:
            yield chunk
    return Mock(side_effect=gen)


@pytest.mark.parametrize('cfg,u', [
    (config('123', enabled=False), update()),
    (mod.OfflineAttachmentConfig(), update()),
    (config('@name'), update()),
    (config('00123'), update()),
    (config('124'), update()),
    (config('123'), update(chat_id=124)),
    (config('123'), update(chat_id=-123)),
    (config('123'), update(uid=True, chat_id=1)),
    (config('123'), update(chat=NS(id=123, type='group'))),
    (config('123'), update(chat=NS(id=124, type='private'))),
    (config('123'), update(from_user=NS(id=124))),
    (config('123'), update(sender_chat=NS(id=123))),
    (config('123'), update(business_connection_id='x')),
    (config('123'), update(forward_origin=NS())),
    (config('123'), update(is_automatic_forward=True)),
])
def test_authorization_before_source(cfg, u):
    factory = source(b'hello')
    with pytest.raises(mod.AttachmentRejected, match='não recebido nem analisado'):
        run(mod.inspect_document_offline(u, cfg, factory))
    factory.assert_not_called()


@pytest.mark.parametrize('field,value', [
    ('file_size', None), ('file_size', True), ('file_size', 0),
    ('file_size', -1), ('file_size', mod.MAX_BYTES + 1),
    ('mime_type', None), ('mime_type', 'image/jpeg'), ('mime_type', 'text/plain; charset=utf-8'),
    ('file_id', '../abc'), ('file_id', 'https://attacker.test/a'), ('file_id', ''),
    ('file_id', 'x' * 513),
])
def test_bad_metadata_never_opens_source(field, value):
    u = update()
    setattr(u.message.document, field, value)
    factory = source(b'hello')
    with pytest.raises(mod.AttachmentRejected):
        run(mod.inspect_document_offline(u, config('123'), factory))
    factory.assert_not_called()


def test_windows_blocked_before_source(monkeypatch):
    monkeypatch.setattr(mod.sys, 'platform', 'win32')
    factory = source(b'hello')
    with pytest.raises(mod.AttachmentRejected):
        run(mod.inspect_document_offline(update(), config('123'), factory))
    factory.assert_not_called()


def test_synthetic_success_stream_utf8_and_no_files(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    data = 'alô\nOlá'.encode()
    u = update(data)
    factory = source(data[:3], data[3:4], data[4:])
    result = run(mod.inspect_document_offline(u, config('123'), factory))
    assert result == mod.Inspection(len(data), len('alô\nOlá'), 1)
    factory.assert_called_once_with('synthetic_id-1')
    assert list(tmp_path.iterdir()) == []
    assert 'alô' not in repr(result)


@pytest.mark.parametrize('declared,chunks', [
    (5, [b'hello!', b'sensitive']), (5, [b'hell']),
    (1, [b'x' * (mod.MAX_CHUNK_BYTES + 1)]), (5, [b'']),
    (3, [b'\xff\xff\xff']), (2, [b'\xe2\x82']), (3, ['abc']),
    (5, [bytearray(b'hello')]), (3, [b'a\x00b']),
])
def test_stream_enforced(declared, chunks):
    u = update()
    u.message.document.file_size = declared
    with pytest.raises(mod.AttachmentRejected, match='não recebido nem analisado'):
        run(mod.inspect_document_offline(u, config('123'), source(*chunks)))


def test_exception_redacted_and_source_closed(capsys):
    closed = []
    async def bad_source(_):
        try:
            yield b'hell'
            raise RuntimeError('SYNTHETIC_TOKEN AND CONTENT')
        finally:
            closed.append(True)
    with pytest.raises(mod.AttachmentRejected) as exc:
        run(mod.inspect_document_offline(update(), config('123'), bad_source))
    assert closed == [True]
    assert 'SYNTHETIC_TOKEN' not in str(exc.value)
    assert capsys.readouterr().out == '' and capsys.readouterr().err == ''


def test_timeout_and_closes(monkeypatch):
    closed = []
    monkeypatch.setattr(mod, 'TIMEOUT_SECONDS', .02)
    async def stall(_):
        try:
            await asyncio.sleep(2)
            yield b'hello'
        finally:
            closed.append(True)
    with pytest.raises(mod.AttachmentRejected, match='não recebido nem analisado'):
        run(mod.inspect_document_offline(update(), config('123'), stall))
    assert closed == [True]


def test_cancellation_propagates_and_closes():
    async def scenario():
        started, closed = asyncio.Event(), asyncio.Event()
        async def stall(_):
            try:
                started.set()
                await asyncio.sleep(2)
                yield b'hello'
            finally:
                closed.set()
        task = asyncio.create_task(mod.inspect_document_offline(update(), config('123'), stall))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()
    run(scenario())


def test_two_identities_are_independent():
    async def scenario():
        async def read(data, uid):
            u = update(data, uid=uid, chat_id=uid)
            return await mod.inspect_document_offline(u, config(str(uid)), source(data))
        a, b = await asyncio.gather(read(b'abc', 123), read(b'12345\n', 456))
        assert a == mod.Inspection(3, 3, 0)
        assert b == mod.Inspection(6, 6, 1)
    run(scenario())


def test_source_must_be_async_generator():
    async def bad(_):
        return b'hello'
    # Avoid creating an unawaited coroutine: the invalid factory is synchronous.
    with pytest.raises(mod.AttachmentRejected):
        run(mod.inspect_document_offline(update(), config('123'), lambda _: [b'hello']))
