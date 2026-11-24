"""Optional local integration: python tests/share-draft-browser.py /path/to/vite-build
Requires Chromium and Python websockets. No real backend, accounts or external service.
"""
import asyncio
import functools
import http.server
import json
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

import websockets

root = pathlib.Path(sys.argv[1]).resolve()
assert (root / 'index.html').exists(), 'Build the dashboard first'

class Server(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        self.server.paths.append(self.path)
        if self.path.startswith('/api/'):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'[]')
        else:
            super().do_GET()
    def do_POST(self):
        # There should be no POST during the test.
        self.server.posts += 1
        self.send_error(403)

server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Server, directory=str(root)))
server.posts = 0
server.paths = []
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f'http://127.0.0.1:{server.server_port}/'

async def check(port):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list') as r:
        pages = json.load(r)
    page = next(p for p in pages if p['type'] == 'page')
    async with websockets.connect(page['webSocketDebuggerUrl'], max_size=10_000_000) as ws:
        async def command(id, method, params):
            await ws.send(json.dumps({'id': id, 'method': method, 'params': params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get('id') == id:
                    return msg
        await command(1, 'Page.navigate', {'url': url})
        await asyncio.sleep(2)
        result = await command(2, 'Runtime.evaluate', {'awaitPromise': True, 'returnByValue': True, 'expression': r'''(async () => {
          const wait = () => new Promise(resolve => setTimeout(resolve, 80));
          const assert = (ok, why) => { if (!ok) throw Error(why); };
          const button = label => [...document.querySelectorAll('button')].find(b => b.textContent.trim() === label);
          const paste = (text, file = false) => {
            const dt = new DataTransfer();
            if (text) dt.setData('text/plain', text);
            if (file) dt.items.add(new File(['video'], 'clip.mp4', {type:'video/mp4'}));
            const event = new ClipboardEvent('paste', {bubbles:true, cancelable:true, clipboardData:dt});
            const el = document.querySelector('#share-draft-text');
            el.focus(); el.dispatchEvent(event);
            assert(event.defaultPrevented, 'paste was not intercepted');
          };
          const secret = 'https://example.invalid/video?test-private-marker';
          assert(button('Colar texto ou link'), 'chat missing');
          button('Colar texto ou link').click(); await wait();
          paste(secret); await wait();
          assert(document.querySelector('#share-draft-text').value === secret, 'paste failed');
          assert(document.querySelector('#chat-input').value === '', 'premature transfer');
          assert(!location.href.includes('private-marker'), 'location leaked');
          assert(!JSON.stringify(localStorage).includes('private-marker'), 'storage leaked');
          paste('', true); await wait();
          assert(document.querySelector('[role=status]').textContent.includes('vídeos'), 'no file warning');
          assert(document.querySelector('#share-draft-text').value === secret, 'file altered text');
          paste('x'.repeat(8193)); await wait();
          assert(document.querySelector('[role=status]').textContent.includes('não colado'), 'no limit warning');
          assert(document.querySelector('#share-draft-text').value === secret, 'oversized paste was truncated');
          button('Adicionar ao rascunho do chat').click(); await wait();
          assert(document.querySelector('#chat-input').value.includes(secret), 'not transferred to composer');
          assert(!document.querySelector('#share-draft-text'), 'staging not closed');
          assert(!JSON.stringify(localStorage).includes('private-marker'), 'transfer saved to storage');
          document.querySelector('[title="Novo Chat"]').click(); await wait();
          assert(!document.querySelector('#chat-input').value.includes(secret), 'composer leaked to new chat');
          button('Colar texto ou link').click(); await wait();
          assert(document.querySelector('#share-draft-text').value === '', 'staging leaked to new chat');
          paste(secret); await wait();
          document.querySelector('[title="Novo Chat"]').click(); await wait();
          button('Colar texto ou link').click(); await wait();
          assert(document.querySelector('#share-draft-text').value === '', 'open preview leaked to new chat');
          paste(secret); await wait();
          button('Limpar e fechar').click(); await wait();
          button('Colar texto ou link').click(); await wait();
          assert(document.querySelector('#share-draft-text').value === '', 'close did not clear staging');
          return 'ok';
        })()'''})
        if 'exceptionDetails' in result.get('result', {}):
            raise AssertionError(result['result']['exceptionDetails'].get('text', 'browser assertion failed'))
        assert result['result']['result']['value'] == 'ok', result

try:
    with tempfile.TemporaryDirectory() as profile:
        proc = subprocess.Popen(['chromium', '--headless', '--no-sandbox', '--disable-gpu', '--disable-background-networking', '--remote-debugging-port=0', f'--user-data-dir={profile}', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            path = pathlib.Path(profile) / 'DevToolsActivePort'
            for _ in range(100):
                if path.exists(): break
                time.sleep(.1)
            port = int(path.read_text().splitlines()[0])
            asyncio.run(check(port))
            assert server.posts == 0, 'Unexpected POST'
            assert all('private-marker' not in p for p in server.paths), 'Content leaked into request URL'
            print('PASS: local Chromium paste, review, limits, file refusal, session switch, no POST')
        finally:
            proc.terminate()
            proc.wait(timeout=10)
finally:
    server.shutdown()
