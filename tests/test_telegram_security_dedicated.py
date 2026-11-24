import asyncio
import logging
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger
from caiocore.bus.events import OutboundMessage
from caiocore.channels.telegram import TelegramChannel, _split_message, _utf16_len, _TelegramTokenLogFilter
from caiocore.config.schema import TelegramConfig


def channel(allow=('123',), **kwargs):
    c = TelegramChannel(TelegramConfig(allow_from=list(allow), **kwargs), NS(publish_inbound=AsyncMock()), 'private-groq-key')
    bot = NS(send_message=AsyncMock(), send_photo=AsyncMock(), send_voice=AsyncMock(),
             send_audio=AsyncMock(), send_document=AsyncMock(), send_chat_action=AsyncMock(),
             get_file=AsyncMock())
    c._app = NS(bot=bot)
    c._start_typing = MagicMock()
    return c


def update(uid=123, chat_id=123, kind='private', **changes):
    user = NS(id=uid, is_bot=False, username='u', first_name='n')
    msg = NS(chat_id=chat_id, chat=NS(id=chat_id, type=kind), from_user=user, sender_chat=None,
             business_connection_id=None, text='hello secret', caption=None, photo=[], voice=None,
             audio=None, document=None, message_id=42, reply_text=AsyncMock())
    for k, v in changes.items(): setattr(msg, k, v)
    return NS(message=msg, effective_user=user)


@pytest.mark.parametrize('allow,uid,chat,kind', [
    ([],123,123,'private'), (['@u'],123,123,'private'), (['123|u'],123,123,'private'),
    (['00123'],123,123,'private'), (['123'],124,124,'private'),
    (['123'],123,-5,'group'), (['123'],123,-1005,'supergroup'),
    (['123'],123,124,'private'), (['123'],0,0,'private'), (['123'],True,1,'private'),
    (['123'],123,123,'channel'), (['123'],123,123,None),
])
@pytest.mark.asyncio
async def test_denied_every_handler_before_any_side_effect(allow, uid, chat, kind):
    c=channel(allow); u=update(uid,chat,kind)
    u.message.voice=NS(file_id='danger')
    for method in (c._on_message,c._on_start,c._on_help,c._forward_command):
        await method(u,None)
    c.bus.publish_inbound.assert_not_awaited()
    c._app.bot.get_file.assert_not_awaited()
    u.message.reply_text.assert_not_awaited()
    c._start_typing.assert_not_called()


@pytest.mark.parametrize('change', [
    {'from_user': None}, {'from_user': NS(id=999)}, {'chat': NS(id=999,type='private')},
    {'sender_chat': NS(id=123)}, {'business_connection_id':'business'},
])
@pytest.mark.asyncio
async def test_forged_inconsistent_updates_denied(change):
    c=channel(); u=update(**change)
    await c._on_message(u,None); await c._on_start(u,None)
    c.bus.publish_inbound.assert_not_awaited(); u.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_text_commands_and_reply():
    c=channel(reply_to_message=True); u=update()
    await c._on_message(u,None); await c._forward_command(u,None)
    await c._on_start(u,None); await c._on_help(u,None)
    assert c.bus.publish_inbound.await_count==2
    item=c.bus.publish_inbound.await_args_list[0].args[0]
    assert (item.sender_id,item.chat_id,item.content,item.metadata['message_id']) == ('123','123','hello secret',42)
    assert u.message.reply_text.await_count == 2
    await c.send(OutboundMessage(channel='telegram',chat_id='123',content='**hello**',metadata={'message_id':42}))
    kw=c._app.bot.send_message.await_args.kwargs
    assert kw['chat_id']==123 and kw['text']=='<b>hello</b>' and kw['reply_parameters'].message_id==42


@pytest.mark.asyncio
async def test_defense_direct_bus_and_outbound_blocked(tmp_path):
    c=channel(); f=tmp_path/'s'; f.write_text('secret')
    await c._handle_message('123','124','no')
    await c._handle_message('124','124','no')
    for target in ['124','-123','00123','+123',' 123','@u','',None,True]:
        await c.send(OutboundMessage(channel='telegram',chat_id=target,content='secret',media=[str(f)]))
    c.bus.publish_inbound.assert_not_awaited(); c._app.bot.send_message.assert_not_awaited(); c._app.bot.send_document.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['voice','audio','document','photo'])
async def test_authorized_media_never_downloads(kind, tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path)); c=channel(); u=update(caption='caption')
    media=NS(file_id='../../unsafe',mime_type='audio/ogg')
    setattr(u.message, kind, [media] if kind=='photo' else media)
    file=NS(download_to_drive=AsyncMock()); c._app.bot.get_file.return_value=file
    await c._on_message(u,None)
    c._app.bot.get_file.assert_not_awaited(); file.download_to_drive.assert_not_awaited()
    assert not (tmp_path / '.caiocore').exists()
    item=c.bus.publish_inbound.await_args.args[0]
    assert item.media == [] and 'caption' in item.content and 'não recebido nem analisado' in item.content


@pytest.mark.asyncio
@pytest.mark.parametrize('suffix', ['.jpg','.ogg','.mp3','.pdf'])
async def test_outbound_media_blocks_arbitrary_files_symlinks_and_success_text(suffix, tmp_path, monkeypatch):
    c=channel(); secret=tmp_path/('secret'+suffix); secret.write_text('private')
    link=tmp_path/('link'+suffix); link.symlink_to(secret)
    opened = MagicMock(side_effect=AssertionError('must not open'))
    monkeypatch.setattr('builtins.open', opened)
    for path in (secret, link, tmp_path/('nonexistent'+suffix)):
        with pytest.raises(ValueError, match='anexos desativado'):
            await c.send(OutboundMessage(channel='telegram',chat_id='123',content='Arquivo enviado!',media=[str(path)]))
    opened.assert_not_called()
    for name in ('send_photo','send_voice','send_audio','send_document','send_message'):
        getattr(c._app.bot,name).assert_not_awaited()


@pytest.mark.asyncio
async def test_media_and_error_logs_do_not_include_sensitive_content(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path)); c=channel(); u=update(voice=NS(file_id='x',mime_type=None))
    c._app.bot.get_file.side_effect=RuntimeError('SENSITIVE_FAKE')
    logs=[]; h=logger.add(logs.append, format='{message}')
    try:
        await c._on_message(u,None)
        await c._on_error(None,NS(error=RuntimeError('SENSITIVE_FAKE')))
        c._app.bot.send_message.side_effect=RuntimeError('SENSITIVE_FAKE')
        await c.send(OutboundMessage(channel='telegram',chat_id='123',content='PRIVATE_FAKE'))
    finally: logger.remove(h)
    text=''.join(logs)
    assert 'SENSITIVE_FAKE' not in text and 'PRIVATE_FAKE' not in text and 'hello secret' not in text
    assert 'não recebido nem analisado' in c.bus.publish_inbound.await_args.args[0].content
    c._app.bot.get_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_split_limits_escape_expansion_and_exact_content():
    c=channel(); content='😀'*2100+'<&'*2100+'x'*9000+'\n  '
    chunks=_split_message(content)
    assert ''.join(chunks)==content and all(_utf16_len(x)<=4096 for x in chunks)
    await c.send(OutboundMessage(channel='telegram',chat_id='123',content=content))
    calls=c._app.bot.send_message.await_args_list
    assert all(_utf16_len(call.kwargs['text'])<=4096 for call in calls)
    assert any('parse_mode' not in call.kwargs for call in calls)


@pytest.mark.asyncio
async def test_html_fallback_and_reply_validation():
    c=channel(reply_to_message=True)
    c._app.bot.send_message.side_effect=[ValueError('bad html'), None]
    await c.send(OutboundMessage(channel='telegram',chat_id='123',content='hello',metadata={'message_id':'unsafe'}))
    assert c._app.bot.send_message.await_count==2
    assert c._app.bot.send_message.await_args.kwargs['reply_parameters'] is None
    assert c._app.bot.send_message.await_args.kwargs['text']=='hello'


@pytest.mark.asyncio
async def test_start_fail_closed_no_builder(monkeypatch):
    c=channel([],token='1:'+('a'*40)); c._app=None
    builder=MagicMock(); monkeypatch.setattr('caiocore.channels.telegram.Application.builder', builder)
    await c.start()
    builder.assert_not_called(); assert not c.is_running


@pytest.mark.asyncio
async def test_start_failure_sanitized_and_cleanup(monkeypatch):
    c=channel(['123'],token='1:'+('a'*40)); c._app=None
    app=NS(add_error_handler=MagicMock(),add_handler=MagicMock(),initialize=AsyncMock(),
           start=AsyncMock(side_effect=RuntimeError('TOKEN SECRET')),shutdown=AsyncMock(),
           stop=AsyncMock(),updater=NS(stop=AsyncMock()),bot=NS())
    builder=MagicMock(); builder.token.return_value=builder; builder.request.return_value=builder
    builder.get_updates_request.return_value=builder; builder.build.return_value=app
    monkeypatch.setattr('caiocore.channels.telegram.Application.builder',lambda:builder)
    logs=[]; h=logger.add(logs.append,format='{message}')
    try: await c.start()
    finally: logger.remove(h)
    app.shutdown.assert_awaited_once(); app.stop.assert_not_awaited()
    assert not c.is_running and c._app is None and 'TOKEN SECRET' not in ''.join(logs)


def test_httpx_token_filter():
    f=_TelegramTokenLogFilter('1:secret')
    record=logging.LogRecord('httpx',logging.INFO,'',0,'HTTP %s',('https://api.telegram.org/bot1:secret/getMe',),None)
    assert not f.filter(record)
    record=logging.LogRecord('httpx',logging.INFO,'',0,'unrelated %s',('ok',),None)
    assert not f.filter(record)
    for name in ('telegram.ext.Application', 'telegram.ext.ExtBot', 'httpcore.http11'):
        record=logging.LogRecord(name,logging.DEBUG,'',0,'Processing %s',('private text',),None)
        assert not f.filter(record)


def test_httpx_filter_drops_traceback_stack_and_unrelated_records():
    f=_TelegramTokenLogFilter('unused-fake')
    try:
        raise RuntimeError('synthetic credential only in exception')
    except RuntimeError:
        import sys
        record=logging.LogRecord('httpx',logging.ERROR,'',0,'ordinary message',(),sys.exc_info(),sinfo='synthetic private stack')
    assert not f.filter(record)


@pytest.mark.asyncio
async def test_real_ptb_builder_proxy_requests_without_network(monkeypatch):
    import caiocore.channels.telegram as tg
    original_request=tg.HTTPXRequest
    requests=[]
    def create_request(**kwargs):
        requests.append(kwargs)
        return original_request(**kwargs)
    monkeypatch.setattr(tg, 'HTTPXRequest', create_request)
    # Build a real PTB Application, but stop before any networking.
    initialize=AsyncMock(side_effect=RuntimeError('offline'))
    monkeypatch.setattr(tg.Application, 'initialize', initialize)
    proxy='http://127.0.0.1:54321'
    c=channel(['123'], token='1:'+('a'*40), proxy=proxy); c._app=None
    await c.start()
    initialize.assert_awaited_once()
    assert len(requests)==2 and all(r['proxy']==proxy for r in requests)
    assert not c.is_running and c._app is None


@pytest.mark.asyncio
async def test_direct_media_injection_is_rejected():
    c=channel()
    with pytest.raises(ValueError, match='recepção de anexos desativada'):
        await c._handle_message('123', '123', 'file', media=['/secret'])
    c.bus.publish_inbound.assert_not_awaited()


@pytest.mark.asyncio
async def test_actual_httpx_exception_record_blocked_during_start(monkeypatch):
    import caiocore.channels.telegram as tg
    import io
    stream=io.StringIO(); handler=logging.StreamHandler(stream)
    log=logging.getLogger('httpx'); old_level=log.level; log.setLevel(logging.INFO); log.addHandler(handler)
    async def initialize(app):
        try:
            raise RuntimeError('synthetic-credential-only-in-traceback')
        except RuntimeError:
            log.exception('innocuous message')
        raise RuntimeError('offline')
    monkeypatch.setattr(tg.Application, 'initialize', initialize)
    c=channel(['123'], token='1:'+('a'*40)); c._app=None
    try:
        await c.start()
    finally:
        log.removeHandler(handler); log.setLevel(old_level)
    assert stream.getvalue() == ''


@pytest.mark.asyncio
async def test_incoming_media_never_follows_symlinked_media_dir(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    protected=tmp_path/'protected'; protected.mkdir()
    parent=tmp_path/'.caiocore'; parent.mkdir()
    (parent/'media').symlink_to(protected, target_is_directory=True)
    c=channel(); u=update(document=NS(file_id='ignored'))
    await c._on_message(u,None)
    c._app.bot.get_file.assert_not_awaited()
    assert list(protected.iterdir()) == []


@pytest.mark.asyncio
async def test_invalid_proxy_aborts_before_builder_and_without_fallback(monkeypatch):
    import caiocore.channels.telegram as tg
    builder=MagicMock(); monkeypatch.setattr(tg.Application, 'builder', builder)
    c=channel(['123'], token='1:'+('a'*40), proxy='invalid-proxy'); c._app=None
    await c.start()
    builder.assert_not_called()
    assert not c.is_running and c._app is None
