#!/usr/bin/env python3
"""Offline Chromium smoke test against synthetic loopback-only backend.

Requires dashboard/dist, local Node/esbuild, chromium and Python websockets.
Screenshots go to /tmp, never the repository. Queue harness is built in a temporary
directory. No real gateway, credentials, or user profile are used.
"""
import asyncio
import base64
import json
import http.client
import os
import pathlib
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import websockets

ROOT = pathlib.Path(__file__).resolve().parents[1]
DIST = ROOT / 'dashboard' / 'dist'
ARTIFACTS = pathlib.Path('/tmp/caio_visual_test')
POSTS = []
REQUESTS = []
QUEUE_DIR = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, content, ctype):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'")
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def path_checked(self):
        parsed = urllib.parse.urlsplit(self.path)
        # When used as a proxy, explicitly reject every non-test origin.
        if parsed.netloc and parsed.netloc != f'127.0.0.1:{self.server.server_port}':
            return None
        if parsed.scheme and parsed.scheme != 'http':
            return None
        return urllib.parse.unquote(parsed.path)

    def do_CONNECT(self):
        REQUESTS.append(['CONNECT', 'BLOCKED_NONLOCAL_PROXY_REQUEST'])
        self.send(403, b'Blocked', 'text/plain')

    def do_GET(self):
        path = self.path_checked()
        REQUESTS.append(['GET', path if path is not None else 'BLOCKED_NONLOCAL_PROXY_REQUEST'])
        if path is None:
            return self.send(403, b'Blocked', 'text/plain')
        if path.startswith('/api/chat/history/'):
            return self.send(200, b'{"messages":[]}', 'application/json')
        if path.startswith('/api/'):
            return self.send(404, b'{"message":"not mocked"}', 'application/json')
        base = QUEUE_DIR if path.startswith('/__queue_test/') else DIST
        relative = path.removeprefix('/__queue_test/') if base == QUEUE_DIR else path.lstrip('/')
        filename = (base / relative).resolve()
        if filename == base:
            filename = base / 'index.html'
        if base.resolve() not in filename.parents or not filename.is_file():
            return self.send(404, b'Not found', 'text/plain')
        mimetype = {'.html':'text/html', '.js':'text/javascript', '.css':'text/css', '.svg':'image/svg+xml', '.png':'image/png', '.woff2':'font/woff2'}.get(filename.suffix, 'application/octet-stream')
        self.send(200, filename.read_bytes(), mimetype)

    def do_POST(self):
        path = self.path_checked()
        REQUESTS.append(['POST', path])
        if path != '/api/chat':
            return self.send(403, b'{"message":"blocked"}', 'application/json')
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 <= length <= 10000:
                raise ValueError('invalid length')
            obj = json.loads(self.rfile.read(length))
            POSTS.append(obj)
            self.send(200, json.dumps({'content':'Resposta sintética de teste.'}).encode(), 'application/json')
        except (ValueError, json.JSONDecodeError):
            self.send(400, b'{}', 'application/json')


class CDP:
    def __init__(self, ws, origin):
        self.ws, self.origin, self.seq, self.pending = ws, origin, 0, {}
        self.exceptions, self.console_errors, self.blocked = [], [], []
        self.reader = asyncio.create_task(self.read())

    async def read(self):
        async for raw in self.ws:
            data = json.loads(raw)
            if 'id' in data:
                future = self.pending.pop(data['id'], None)
                if future and not future.done():
                    future.set_result(data)
                continue
            method, p = data.get('method'), data.get('params', {})
            if method == 'Fetch.requestPaused':
                url = p['request']['url']
                parsed = urllib.parse.urlsplit(url)
                safe = parsed.scheme == 'http' and parsed.netloc == self.origin
                if not safe:
                    self.blocked.append(url)
                asyncio.create_task(self.call('Fetch.continueRequest' if safe else 'Fetch.failRequest', {'requestId': p['requestId'], **({} if safe else {'errorReason':'BlockedByClient'})}))
            elif method == 'Runtime.exceptionThrown':
                self.exceptions.append(p.get('exceptionDetails', {}))
            elif method == 'Runtime.consoleAPICalled' and p.get('type') == 'error':
                self.console_errors.append([x.get('value', x.get('description', '')) for x in p.get('args', [])])

    async def call(self, method, params=None):
        self.seq += 1
        n = self.seq
        future = asyncio.get_running_loop().create_future()
        self.pending[n] = future
        await self.ws.send(json.dumps({'id':n,'method':method,'params':params or {}}))
        result = await asyncio.wait_for(future, 10)
        if 'error' in result:
            raise RuntimeError(f'{method}: {result["error"]}')
        return result.get('result', {})

    async def js(self, expression):
        result = await self.call('Runtime.evaluate', {'expression':expression, 'returnByValue':True, 'awaitPromise':True})
        if 'exceptionDetails' in result:
            raise RuntimeError(str(result['exceptionDetails']))
        return result.get('result', {}).get('value')

    async def wait(self, expression, seconds=8):
        for _ in range(int(seconds * 10)):
            if await self.js(expression):
                return
            await asyncio.sleep(.1)
        print('TIMEOUT diagnostics:', await self.js("({url:location.href,ready:document.readyState,body:document.body && document.body.innerText.slice(0,1000)})"), 'exceptions:', self.exceptions, 'console:', self.console_errors, 'blocked:', self.blocked, 'requests:', REQUESTS, flush=True)
        raise AssertionError('Timeout waiting for ' + expression)

    async def screenshot(self, name):
        data = await self.call('Page.captureScreenshot', {'format':'png', 'captureBeyondViewport':False})
        (ARTIFACTS / name).write_bytes(base64.b64decode(data['data']))

    async def click(self, selector):
        # Exercise real pointer hit-testing, not HTMLElement.click().
        js = json.dumps(selector)
        pos = await self.js(f"""(()=>{{let b=document.querySelector({js}); if(!b) return null;
          b.scrollIntoView({{block:'nearest'}}); let r=b.getBoundingClientRect();
          let x=r.left+r.width/2,y=r.top+r.height/2,e=document.elementFromPoint(x,y);
          return {{x,y,visible:r.width>0 && r.height>0 && b.contains(e),hit:e?.outerHTML.slice(0,180)}};}})()""")
        assert pos and pos['visible'], f'{selector}: not clickable: {pos}'
        await self.call('Input.dispatchMouseEvent', {'type':'mousePressed','x':pos['x'],'y':pos['y'],'button':'left','clickCount':1})
        await self.call('Input.dispatchMouseEvent', {'type':'mouseReleased','x':pos['x'],'y':pos['y'],'button':'left','clickCount':1})


async def scenario(c, port, label, width, height):
    print(f'\n--- {label} {width}x{height} ---', flush=True)
    await c.call('Emulation.setDeviceMetricsOverride', {'width':width, 'height':height, 'deviceScaleFactor':1, 'mobile':label == 'mobile'})
    await c.call('Page.navigate', {'url':f'http://127.0.0.1:{port}/'})
    await c.wait("document.readyState === 'complete' && !!document.querySelector('#chat-input')")
    await asyncio.sleep(.8)
    print('initial', json.dumps(await c.js("({title:document.title,body:document.body.innerText.slice(0,600),vw:innerWidth,sw:document.documentElement.scrollWidth})"), ensure_ascii=False), flush=True)
    assert await c.js("document.body.innerText.includes('Como posso ajudar?')"), 'welcome missing'
    assert await c.js("document.querySelector('[aria-label=\"Enviar mensagem\"]').disabled"), 'empty submit enabled'
    await c.screenshot(label + '_welcome.png')
    await c.js("document.querySelector('#chat-input').focus()")
    text = 'Mensagem sintética ' + label
    await c.call('Input.insertText', {'text':text})
    await c.wait("!document.querySelector('[aria-label=\"Enviar mensagem\"]').disabled")
    await c.call('Input.dispatchKeyEvent', {'type':'keyDown','key':'Enter','code':'Enter','windowsVirtualKeyCode':13})
    await c.call('Input.dispatchKeyEvent', {'type':'keyUp','key':'Enter','code':'Enter','windowsVirtualKeyCode':13})
    await c.wait("document.body.innerText.includes('Resposta sintética de teste.')")
    assert await c.js(f"document.body.innerText.includes({json.dumps(text)})"), 'user message missing'
    assert any(p.get('message') == text and p.get('session_id') for p in POSTS), 'mock did not receive chat'
    await c.wait("!!document.querySelector('[aria-label=\"Copiar resposta\"]') && !!document.querySelector('[aria-label=\"Sugestões gerais\"]')")
    await asyncio.sleep(.4)
    await c.screenshot(label + '_chat.png')
    post_count = len(POSTS)
    # Clipboard: native Chromium API with explicit, isolated origin permission.
    await c.call('Browser.grantPermissions', {'origin':f'http://127.0.0.1:{port}', 'permissions':['clipboardReadWrite','clipboardSanitizedWrite']})
    await c.click('[aria-label="Copiar resposta"]')
    await c.wait("!!document.querySelector('[aria-label=\"Resposta copiada\"]')")
    assert await c.js('navigator.clipboard.readText()') == 'Resposta sintética de teste.', 'clipboard content mismatch'
    like = '[aria-label="Curtir resposta (reação local temporária)"]'
    dislike = '[aria-label="Não curtir resposta (reação local temporária)"]'
    await c.click(like)
    assert await c.js(f'document.querySelector({json.dumps(like)}).getAttribute("aria-pressed")') == 'true'
    await c.click(dislike)
    assert await c.js(f'document.querySelector({json.dumps(like)}).getAttribute("aria-pressed")') == 'false'
    assert await c.js(f'document.querySelector({json.dumps(dislike)}).getAttribute("aria-pressed")') == 'true'
    await c.click(dislike)
    assert await c.js(f'document.querySelector({json.dumps(dislike)}).getAttribute("aria-pressed")') == 'false'
    # Test rejection of remote/unknown voices and successful local-only choice using
    # a controlled browser speech adapter; this does not claim audible playback.
    native_voices = await c.js("'speechSynthesis' in window ? speechSynthesis.getVoices().map(v=>({lang:v.lang,local:v.localService})) : null")
    await c.js("""(()=>{window.__speechTest={calls:[], cancelled:0, voices:[{lang:'pt-BR',localService:false},{lang:'pt-BR'}]};
      Object.defineProperty(window,'speechSynthesis',{configurable:true,value:{speaking:false,pending:false,
      getVoices:()=>window.__speechTest.voices, speak:u=>window.__speechTest.calls.push({text:u.text,lang:u.lang,local:u.voice.localService}),
      cancel:()=>window.__speechTest.cancelled++}});
      Object.defineProperty(window,'SpeechSynthesisUtterance',{configurable:true,value:function(text){this.text=text}});})()""")
    await c.click('[aria-label="Ouvir resposta"]')
    await c.wait("document.querySelector('.caio-message-actions [role=status]').innerText.includes('Nenhuma voz local')")
    assert await c.js('window.__speechTest.calls.length') == 0, 'remote voice was used'
    await c.js("window.__speechTest.voices.push({lang:'pt-BR',localService:true})")
    await c.click('[aria-label="Ouvir resposta"]')
    await c.wait("!!document.querySelector('[aria-label=\"Parar leitura\"]')")
    assert await c.js('window.__speechTest.calls') == [{'text':'Resposta sintética de teste.', 'lang':'pt-BR', 'local':True}]
    await c.click('[aria-label="Parar leitura"]')
    assert await c.js('window.__speechTest.cancelled') == 1
    assert await c.js("document.querySelector('.caio-message-actions [role=status]').innerText.includes('interrompida')")
    # Suggestions must append to a pre-existing draft, focus it, and never send.
    suggestions = await c.js("[...document.querySelectorAll('[aria-label=\"Sugestões gerais\"] button')].map(b=>b.innerText)")
    assert suggestions == ['Resuma essa resposta', 'Explique melhor', 'Quais são os próximos passos?'], suggestions
    await c.js("document.querySelector('#chat-input').focus()")
    await c.call('Input.insertText', {'text':'Rascunho sintético'})
    for i, option in enumerate(suggestions):
        await c.click(f'[aria-label="Sugestões gerais"] button:nth-child({i+1})')
        expected = 'Rascunho sintético\n' + '\n'.join(suggestions[:i+1])
        await c.wait(f'document.querySelector("#chat-input").value === {json.dumps(expected)}')
        assert await c.js('document.activeElement.id') == 'chat-input'
    assert len(POSTS) == post_count, 'local controls unexpectedly sent a request'
    await c.screenshot(label + '_controls.png')
    print('native clipboard, local reactions, three suggestions: PASS; speech safety/stop via synthetic adapter: PASS; native voices:', native_voices, flush=True)
    metrics = await c.js("({viewport:innerWidth,doc:document.documentElement.scrollWidth,body:document.body.scrollWidth, input:(()=>{let r=document.querySelector('#chat-input').getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom}})()})")
    print('chat: PASS; layout:', metrics, flush=True)
    assert metrics['doc'] <= metrics['viewport'] + 1 and metrics['body'] <= metrics['viewport'] + 1, 'horizontal overflow'
    assert metrics['input']['left'] >= 0 and metrics['input']['right'] <= width + 1 and metrics['input']['bottom'] <= height + 1, 'composer off-screen'
    if label == 'mobile':
        assert await c.js("!!document.querySelector('[aria-label=\"Abrir menu\"]')"), 'mobile menu opener missing'
        await c.js("document.querySelector('[aria-label=\"Abrir menu\"]').click()")
        await c.wait("!!document.querySelector('[title=\"Gerenciar Habilidades\"]')")
        await asyncio.sleep(.3)
        await c.screenshot(label + '_menu.png')
    await c.js("document.querySelector('[title=\"Gerenciar Habilidades\"]').click()")
    await c.wait("!!document.querySelector('[role=dialog]')")
    await asyncio.sleep(.8)  # Wait for motion so screenshots and hit tests reflect the settled UI.
    assert await c.js("document.querySelector('[role=dialog]').innerText.includes('Seleção visual')"), 'skills disclosure missing'
    hit = await c.js("(()=>{let b=[...document.querySelectorAll('[role=dialog] button')].find(x=>x.innerText.includes('Arquivos'));let r=b.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2,e=document.elementFromPoint(x,y);return {x,y,receives:b.contains(e),hit:e?.outerHTML.slice(0,180)};})()")
    print('skills hit-test:', hit, flush=True)
    if not hit['receives']:
        raise AssertionError('skills button is obscured and not clickable')
    await c.call('Input.dispatchMouseEvent', {'type':'mousePressed','x':hit['x'],'y':hit['y'],'button':'left','clickCount':1})
    await c.call('Input.dispatchMouseEvent', {'type':'mouseReleased','x':hit['x'],'y':hit['y'],'button':'left','clickCount':1})
    await c.wait("[...document.querySelectorAll('[role=dialog] button')].find(x=>x.innerText.includes('Arquivos')).getAttribute('aria-pressed') === 'true'")
    await asyncio.sleep(.5)
    await c.screenshot(label + '_skills.png')
    await c.js("document.querySelector('[aria-label=\"Fechar habilidades\"]').click()")
    await c.wait("!document.querySelector('[role=dialog]')")
    print('skills modal/toggle: PASS', flush=True)
    await c.js("document.querySelector('[title=\"Novo Chat\"]').click()")
    await c.wait("document.body.innerText.includes('Como posso ajudar?')")
    print('new chat: PASS', flush=True)


def make_queue_harness(folder):
    # Bundle the real isolated component into /tmp only, never into product/dist.
    source = r"""
import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {TaskQueue} from './src/components/ui/task-queue.tsx';
window.__queueCalls=[];
function App() {
 const [mode,setMode]=useState('unavailable'), [value,setValue]=useState('');
 const tasks=[{id:'1',title:'Tarefa sintética em andamento',status:'in_progress',priority:'high',progress:45,allowedActions:['cancel']},
 {id:'2',title:'Tarefa sintética falhou',status:'failed',error:'Erro sintético',allowedActions:['retry']}];
 let props = mode === 'unavailable' ? {mode,reason:'Teste isolado'} : mode === 'loading' ? {mode} :
 mode === 'error' ? {mode,reason:'Falha sintética'} : {mode:'ready',tasks,
 create:{value,onChange:setValue,onCreate:(x)=>window.__queueCalls.push(['create',x])},
 onCancel:(id)=>window.__queueCalls.push(['cancel',id]),onRetry:(id)=>window.__queueCalls.push(['retry',id])};
 return <main><h1>TESTE ISOLADO — DADOS SINTÉTICOS</h1><nav>{['unavailable','loading','error','ready'].map(x=><button key={x} onClick={()=>setMode(x)}>{x}</button>)}</nav><TaskQueue {...props}/></main>;
}
createRoot(document.getElementById('root')).render(<App/>);
"""
    build = """import {build} from 'esbuild';
await build({stdin:{contents:process.env.QUEUE_SOURCE,resolveDir:process.cwd(),sourcefile:'test-queue.jsx',loader:'jsx'},bundle:true,outfile:process.env.QUEUE_OUTPUT,format:'iife',platform:'browser'});"""
    env = dict(os.environ, QUEUE_SOURCE=source, QUEUE_OUTPUT=str(folder / 'queue.js'))
    subprocess.run(['node', '--input-type=module', '-e', build], cwd=ROOT / 'dashboard', env=env, check=True, timeout=30)
    (folder / 'index.html').write_text('<!doctype html><html lang="pt-BR"><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="queue.css"></head><body><div id="root"></div><script src="queue.js"></script></body></html>')


async def queue_scenario(c, port, label, width, height):
    print(f'--- isolated queue {label} ---', flush=True)
    count = len(REQUESTS)
    await c.call('Emulation.setDeviceMetricsOverride', {'width':width,'height':height,'deviceScaleFactor':1,'mobile':label == 'mobile'})
    await c.call('Page.navigate', {'url':f'http://127.0.0.1:{port}/__queue_test/index.html'})
    await c.wait("!!document.querySelector('[aria-label=\"Fila de tarefas\"]')")
    assert await c.js("document.querySelector('[aria-label=\"Fila de tarefas\"]').innerText.includes('Fila indisponível')")
    for mode, expected in [('loading','Carregando fila'),('error','Não foi possível carregar'),('ready','Tarefa sintética em andamento')]:
        await c.click(f'nav button:nth-child({["unavailable","loading","error","ready"].index(mode)+1})')
        await c.wait(f'document.querySelector("[aria-label=\\"Fila de tarefas\\"]").innerText.includes({json.dumps(expected)})')
    assert await c.js("document.querySelector('progress').value") == 45
    await c.screenshot(label + '_queue_isolated.png')
    await c.click('[aria-label="Cancelar tarefa: Tarefa sintética em andamento"]')
    await c.click('[aria-label="Repetir tarefa: Tarefa sintética falhou"]')
    await c.js("document.querySelector('.task-queue__field input').focus()")
    await c.call('Input.insertText', {'text':'Nova tarefa sintética'})
    await c.click('.task-queue__form button')
    assert await c.js('window.__queueCalls') == [['cancel','1'],['retry','2'],['create','Nova tarefa sintética']]
    assert await c.js('document.documentElement.scrollWidth <= innerWidth + 1'), 'queue horizontal overflow'
    assert not any(r[1] and r[1].startswith('/api/') for r in REQUESTS[count:]), 'queue made API request'
    print('isolated queue states, progress, local callbacks, layout: PASS', flush=True)


async def main():
    global QUEUE_DIR
    if not (DIST / 'index.html').is_file():
        raise SystemExit('BLOCKED: dashboard/dist/index.html not found')
    ARTIFACTS.mkdir(mode=0o700, exist_ok=True)
    queue_tmp = tempfile.TemporaryDirectory(prefix='caio-queue-test-')
    QUEUE_DIR = pathlib.Path(queue_tmp.name)
    make_queue_harness(QUEUE_DIR)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_port
    # Verify the local proxy refuses absolute external HTTP and HTTPS tunnels;
    # neither request opens an external connection.
    for method, target in [('GET', 'http://outside.invalid/probe'), ('CONNECT', 'outside.invalid:443')]:
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        conn.request(method, target)
        response = conn.getresponse()
        assert response.status == 403, f'proxy permitted {method} {target}'
        response.read()
        conn.close()
    print('proxy external HTTP/CONNECT rejection: PASS', flush=True)
    with tempfile.TemporaryDirectory(prefix='caio-chromium-') as profile:
        env = {'HOME':profile, 'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8', 'NO_PROXY':'127.0.0.1,localhost'}
        cmd = ['/usr/bin/chromium', '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', '--no-first-run', '--no-default-browser-check', '--disable-background-networking', '--disable-component-update', '--disable-sync', '--disable-features=OptimizationHints,MediaRouter,AutofillServerCommunication', '--disable-quic', '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1', f'--proxy-server=http://127.0.0.1:{port}', f'--user-data-dir={profile}', '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0', 'about:blank']
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            active = pathlib.Path(profile) / 'DevToolsActivePort'
            for _ in range(100):
                if active.exists():
                    break
                if proc.poll() is not None:
                    raise RuntimeError('Chromium exited')
                await asyncio.sleep(.1)
            debug_port = int(active.read_text().splitlines()[0])
            with urllib.request.urlopen(f'http://127.0.0.1:{debug_port}/json/list', timeout=5) as response:
                ws_url = json.load(response)[0]['webSocketDebuggerUrl']
            async with websockets.connect(ws_url, max_size=30_000_000, proxy=None) as ws:
                c = CDP(ws, f'127.0.0.1:{port}')
                await c.call('Page.enable')
                await c.call('Runtime.enable')
                await c.call('Network.enable')
                await c.call('Fetch.enable', {'patterns':[{'urlPattern':'*'}]})
                await scenario(c, port, 'desktop', 1440, 900)
                # Fresh synthetic session, no persisted app data between sizes.
                await c.js('localStorage.clear()')
                await scenario(c, port, 'mobile', 390, 844)
                await queue_scenario(c, port, 'desktop', 1440, 900)
                await queue_scenario(c, port, 'mobile', 390, 844)
                assert not any(path == '/api/tracing/stream' for _, path in REQUESTS), 'unexpected SSE subscription'
                print('\nJS exceptions:', json.dumps(c.exceptions, ensure_ascii=False), flush=True)
                print('console errors:', json.dumps(c.console_errors, ensure_ascii=False), flush=True)
                print('blocked non-local requests:', json.dumps(c.blocked, ensure_ascii=False), flush=True)
                print('mock requests:', json.dumps(REQUESTS, ensure_ascii=False), flush=True)
                assert not c.exceptions, 'uncaught JavaScript exceptions'
                assert not c.console_errors, 'JavaScript console errors'
                print('PASS', flush=True)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            server.shutdown()
            server.server_close()
            queue_tmp.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
