"""Offline security regressions for Evolution and the local bridge client."""
import asyncio
import copy
import json
from unittest.mock import AsyncMock

import pytest

from caiocore.bus.events import OutboundMessage
from caiocore.bus.queue import MessageBus
from caiocore.channels.evolution import EvolutionChannel
from caiocore.channels.whatsapp import WhatsAppChannel
from caiocore.config.schema import EvolutionConfig, WhatsAppConfig

PHONE = '5571999999999'
JID = PHONE + '@s.whatsapp.net'


def run(coro):
    return asyncio.run(coro)


def event():
    return {'event': 'messages.upsert', 'instance': 'Caio', 'data': {
        'key': {'fromMe': False, 'remoteJid': JID, 'id': 'id'},
        'message': {'conversation': 'hello'}}}


def evo(allowed):
    return EvolutionChannel(EvolutionConfig(base_url='https://example.invalid', allow_from=allowed), MessageBus())


def test_evolution_direct_allowlist_and_outbound():
    async def exercise():
        c = evo([JID])
        c.client.post = AsyncMock()
        c.client.post.return_value.raise_for_status = lambda: None
        await c.handle_webhook(event())
        assert c.bus.inbound_size == 1
        assert (await c.bus.consume_inbound()).sender_id == PHONE
        await c.send(OutboundMessage('evolution', JID, 'hello'))
        assert c.client.post.await_count == 1
        for target in ('5581999999999', PHONE + '@g.us', '*', PHONE + '@lid'):
            await c.send(OutboundMessage('evolution', target, 'hello'))
        assert c.client.post.await_count == 1
        await c.stop()
    run(exercise())


@pytest.mark.parametrize('allowed', [[], ['*'], [''], ['all'], [PHONE + '@g.us']])
def test_evolution_empty_or_invalid_allowlist_denies(allowed):
    async def exercise():
        c = evo(allowed)
        c.client.post = AsyncMock()
        await c.handle_webhook(event())
        await c.send(OutboundMessage('evolution', JID, 'hello'))
        assert c.bus.inbound_size == 0
        c.client.post.assert_not_awaited()
        await c.stop()
    run(exercise())


@pytest.mark.parametrize('mutation', [
    lambda x: x.update(data=[]),
    lambda x: x.update(instance='other'),
    lambda x: x['data'].update(key=None),
    lambda x: x['data']['key'].pop('fromMe'),
    lambda x: x['data']['key'].update(fromMe='false'),
    lambda x: x['data']['key'].update(fromMe=True),
    lambda x: x['data']['key'].update(remoteJid=PHONE + '@g.us'),
    lambda x: x['data']['key'].update(remoteJid=PHONE + '@lid'),
    lambda x: x['data']['key'].update(remoteJid='5581888888888@s.whatsapp.net'),
    lambda x: x['data']['key'].update(remoteJid=PHONE + ':1@s.whatsapp.net'),
    lambda x: x['data']['key'].update(participant='someone'),
    lambda x: x['data'].update(message=[]),
    lambda x: x['data'].update(message={'extendedTextMessage': 'bad'}),
    lambda x: x['data'].update(message={'conversation': ['bad']}),
    lambda x: x['data'].update(message={'conversation': 'x' * 4097}),
])
def test_evolution_malformed_and_unauthorized_rejected(mutation):
    async def exercise():
        c = evo([PHONE])
        payload = event()
        mutation(payload)
        await c.handle_webhook(payload)
        assert c.bus.inbound_size == 0
        await c.stop()
    run(exercise())


def test_whatsapp_inbound_and_outbound_fail_closed():
    async def exercise():
        c = WhatsAppChannel(WhatsAppConfig(allow_from=[PHONE]), MessageBus())
        c._ws = AsyncMock()
        c._connected = True
        valid = {'type': 'message', 'sender': JID, 'pn': '', 'content': 'hello', 'isGroup': False}
        await c._handle_bridge_message(json.dumps(valid))
        assert c.bus.inbound_size == 1
        for updates in ({'isGroup': True}, {'sender': PHONE + '@g.us'}, {'sender': PHONE + '@lid'},
                        {'pn': '5581888888888@s.whatsapp.net'}, {'isGroup': None}, {'content': []}):
            await c._handle_bridge_message(json.dumps({**valid, **updates}))
        assert c.bus.inbound_size == 1
        await c.send(OutboundMessage('whatsapp', '5581888888888@s.whatsapp.net', 'hello'))
        c._ws.send.assert_not_awaited()
        await c.send(OutboundMessage('whatsapp', JID, 'hello'))
        c._ws.send.assert_awaited_once()
        c.config.allow_from = []
        await c._handle_bridge_message(json.dumps(valid))
        assert c.bus.inbound_size == 1
        await c.send(OutboundMessage('whatsapp', JID, 'hello'))
        assert c._ws.send.await_count == 1
    run(exercise())


def test_bridge_client_never_connects_without_token_or_to_remote(monkeypatch):
    import websockets
    connect = AsyncMock()
    monkeypatch.setattr(websockets, 'connect', connect)
    for url, token in [('ws://localhost:3001', ''), ('ws://example.com:3001', 'secret'),
                       ('ws://localhost@example.com', 'secret')]:
        c = WhatsAppChannel(WhatsAppConfig(bridge_url=url, bridge_token=token), MessageBus())
        run(c.start())
    connect.assert_not_called()


@pytest.mark.parametrize('reply,accepted', [('{}', False), ('{"type":"auth_ok"}', True)])
def test_bridge_client_requires_auth_ack_before_receiving(monkeypatch, reply, accepted):
    import websockets
    async def exercise():
        c = WhatsAppChannel(WhatsAppConfig(bridge_token='secret', allow_from=[PHONE]), MessageBus())
        class FakeWS:
            def __init__(self): self.frames = []
            async def send(self, value): self.frames.append(json.loads(value))
            async def recv(self): return reply
            def __aiter__(self): return self
            async def __anext__(self):
                c._running = False
                if not hasattr(self, 'sent'):
                    self.sent = True
                    return json.dumps({'type': 'message', 'sender': JID, 'pn': '', 'content': 'hello', 'isGroup': False})
                raise StopAsyncIteration
        ws = FakeWS()
        class Context:
            async def __aenter__(self): return ws
            async def __aexit__(self, *args): c._running = False
        monkeypatch.setattr(websockets, 'connect', lambda *args, **kwargs: Context())
        await c.start()
        assert ws.frames == [{'type': 'auth', 'token': 'secret'}]
        assert c.bus.inbound_size == int(accepted)
        assert not c._connected and c._ws is None
    run(exercise())
