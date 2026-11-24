"""Local Chromium integration, no backend: python tests/request-wait-browser.py dist."""
import asyncio, functools, http.server, json, pathlib, subprocess, sys, tempfile, threading, time, urllib.request
import websockets

root = pathlib.Path(sys.argv[1]).resolve()
assert (root / 'index.html').exists()
class Server(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        self.server.paths.append(self.path)
        if self.path.startswith('/api/'):
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(b'[]')
        else: super().do_GET()
    def do_POST(self):
        self.server.posts += 1
        self.send_error(403)
server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Server, directory=str(root)))
server.posts, server.paths = 0, []
threading.Thread(target=server.serve_forever, daemon=True).start()

async def check(port):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list') as r: pages = json.load(r)
    page = next(p for p in pages if p['type'] == 'page')
    async with websockets.connect(page['webSocketDebuggerUrl'], max_size=10_000_000) as ws:
        async def command(id, method, params):
            await ws.send(json.dumps({'id': id, 'method': method, 'params': params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get('id') == id: return msg
        setup = '''(() => {
          const original = window.fetch;
          window.requests = [];
          window.fetch = (url, options) => {
            if (url === '/api/chat' && options?.method === 'POST') {
              return new Promise((resolve, reject) => window.requests.push({resolve, reject}));
            }
            return original(url, options);
          };
        })();'''
        await command(10, 'Page.enable', {})
        added = await command(1, 'Page.addScriptToEvaluateOnNewDocument', {'source': setup})
        assert 'error' not in added, added
        await command(2, 'Page.navigate', {'url': f'http://127.0.0.1:{server.server_port}/'})
        await asyncio.sleep(2)
        result = await command(3, 'Runtime.evaluate', {'awaitPromise': True, 'returnByValue': True, 'expression': r'''(async () => {
          const delay = () => new Promise(r => setTimeout(r, 120));
          const assert = (ok, why) => { if (!ok) throw Error(why); };
          const send = async (text) => {
            const el = document.querySelector('#chat-input');
            Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(el, text);
            el.dispatchEvent(new Event('input', {bubbles:true})); await delay();
            el.closest('form').dispatchEvent(new Event('submit', {bubbles:true, cancelable:true})); await delay();
          };
          await send('segredo da conversa antiga');
          assert(requests.length === 1, 'one request expected');
          assert(document.querySelector('.request-wait').textContent.includes('Aguardando resposta'), 'no waiting indicator');
          assert(document.querySelector('.request-wait').textContent.includes('0 s'), 'no elapsed time');
          assert(!document.querySelector('.request-wait button'), 'unsupported cancel');
          document.querySelector('[title="Novo Chat"]').click(); await delay();
          assert(!document.querySelector('.request-wait'), 'old wait leaked');
          assert(!document.querySelector('.chat-column').textContent.includes('segredo da conversa antiga'), 'old chat leaked');
          await send('nova mensagem');
          assert(requests.length === 2 && document.querySelector('.request-wait'), 'new request missing');
          requests[0].resolve(new Response(JSON.stringify({status:'completed', content:'RESPOSTA ANTIGA PRIVADA'}), {status:200, headers:{'Content-Type':'application/json'}})); await delay();
          assert(!document.body.textContent.includes('RESPOSTA ANTIGA PRIVADA'), 'old reply leaked');
          assert(document.querySelector('.request-wait'), 'old result cleared new wait');
          requests[1].reject(new Error('network unavailable')); await delay();
          assert(!document.querySelector('.request-wait'), 'failure did not clear wait');
          assert(document.body.textContent.includes('O trabalho pode continuar no servidor'), 'unsafe failure text');
          await send('outra mensagem');
          assert(requests.length === 3, 'additional or missing request');
          requests[2].resolve(new Response(JSON.stringify({status:'completed', content:'resposta real'}), {status:200, headers:{'Content-Type':'application/json'}})); await delay();
          assert(document.body.textContent.includes('resposta real'), 'success missing');
          assert(!document.querySelector('.request-wait'), 'success did not clear wait');
          return 'ok';
        })()'''})
        assert 'exceptionDetails' not in result.get('result', {}), result
        assert result['result']['result']['value'] == 'ok', result

try:
    with tempfile.TemporaryDirectory() as profile:
        proc = subprocess.Popen(['chromium', '--headless', '--no-sandbox', '--disable-gpu', '--disable-background-networking', '--remote-debugging-port=0', f'--user-data-dir={profile}', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            path = pathlib.Path(profile) / 'DevToolsActivePort'
            for _ in range(100):
                if path.exists(): break
                time.sleep(.1)
            asyncio.run(check(int(path.read_text().splitlines()[0])))
            assert server.posts == 0
            assert not any('tracing/stream' in p for p in server.paths)
            print('PASS: real UI waiting, session isolation, failure, completion, no extra POST or tracing stream')
        finally:
            proc.terminate(); proc.wait(timeout=10)
finally:
    server.shutdown()
