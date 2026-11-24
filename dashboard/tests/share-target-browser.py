"""Local Chromium POST navigation tests; no network outside loopback.
Usage: python tests/share-target-browser.py BUILD_DIR [BASE_PATH]
"""
import asyncio, functools, http.server, json, pathlib, subprocess, sys, tempfile, threading, time, urllib.request
import websockets
root = pathlib.Path(sys.argv[1]).resolve()
base = sys.argv[2] if len(sys.argv)>2 else '/'
assert base.startswith('/') and base.endswith('/')
class Server(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args): pass
    def translate_path(self, path):
        path = path.split('?', 1)[0]
        return str(root / path[len(base):]) if path.startswith(base) else '/not-found'
    def do_GET(self):
        self.server.paths.append(self.path)
        if self.path.startswith(base + 'assets/') and self.path.endswith('.js'):
            self.server.gate.wait(timeout=45)
        if self.path.startswith('/api/') or self.path.startswith(base+'api/'):
            self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(b'[]')
        else: super().do_GET()
    def do_POST(self):
        self.server.posts.append(self.path); self.send_error(403)
server = http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Server,directory=str(root)))
server.paths=[]; server.posts=[]; server.gate=threading.Event(); server.gate.set()
threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}{base}'
class CDP:
    def __init__(self, ws): self.ws=ws; self.n=0
    async def cmd(self,method,params=None):
        self.n+=1; n=self.n
        await self.ws.send(json.dumps({'id':n,'method':method,'params':params or {}}))
        while True:
            r=json.loads(await self.ws.recv())
            if r.get('id')==n:
                if 'error' in r: raise Exception(r['error'])
                return r.get('result',{})
    async def js(self,expr):
        r=await self.cmd('Runtime.evaluate',{'expression':expr,'awaitPromise':True,'returnByValue':True})
        if r.get('exceptionDetails'): raise Exception(r['exceptionDetails'])
        return r['result'].get('value')
async def check(port):
    def pages():
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list') as r: return json.load(r)
    first=next(p for p in pages() if p['type']=='page')
    async with websockets.connect(first['webSocketDebuggerUrl'],max_size=10000000) as w:
        c=CDP(w); await c.cmd('Network.enable'); await c.cmd('Network.setCacheDisabled',{'cacheDisabled':True}); await c.cmd('Page.navigate',{'url':url}); await asyncio.sleep(2)
        assert await c.js("navigator.serviceWorker.ready.then(r=>!!r.active)")
        async def submit(text):
            await c.js('''(()=>{let f=document.createElement('form');f.method='POST';f.action=BASE+'share-receive';let i=document.createElement('input');i.name='text';i.value=TEXT;f.append(i);document.body.append(f);f.submit();return true})()'''.replace('BASE',json.dumps(url)).replace('TEXT',json.dumps(text)))
            await asyncio.sleep(2)
        marker='https://example.invalid/secret-marker?private=x'
        await submit(marker)
        assert await c.js("document.querySelector('#received-preview')?.value")==marker, 'preview missing'
        assert await c.js("document.querySelector('#chat-input')?.value")==''
        assert await c.js("!location.href.includes('secret-marker') && !JSON.stringify(localStorage).includes('secret-marker')")
        # Existing draft is never overwritten and explicit confirmation is required.
        await c.js("""(()=>{let e=document.querySelector('#chat-input');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(e,'texto existente');e.dispatchEvent(new Event('input',{bubbles:true}));window.confirm=()=>false})()""")
        await c.js("[...document.querySelectorAll('button')].find(b=>b.textContent==='Adicionar compartilhamento ao rascunho').click()")
        assert await c.js("document.querySelector('#chat-input').value") == 'texto existente'
        assert await c.js("!!document.querySelector('#received-preview')")
        await c.js("window.confirm=()=>true; [...document.querySelectorAll('button')].find(b=>b.textContent==='Adicionar compartilhamento ao rascunho').click()")
        assert await c.js("document.querySelector('#chat-input').value")=='texto existente\n'+marker
        assert not server.posts and all('secret-marker' not in x for x in server.paths)
        await c.cmd('Page.navigate',{'url':url}); await asyncio.sleep(2)
        await submit('x'*8193)
        valid = await c.js("document.querySelector('[role=alert]')?.textContent.includes('Não foi possível')")
        assert valid, 'oversize alert missing: '+str(await c.js("JSON.stringify({url:location.href,html:document.body.innerText.slice(0,1000),preview:!!document.querySelector('#received-preview')})"))
        assert await c.js("!document.querySelector('#received-preview') && !document.querySelector('#chat-input').value")
        # Duplicate fields and a multipart file must be rejected, not partially accepted.
        await c.cmd('Page.navigate',{'url':url}); await asyncio.sleep(1)
        await c.js("""(()=>{let f=document.createElement('form');f.method='POST';f.action=BASE+'share-receive';for(let v of ['one','two']){let i=document.createElement('input');i.name='text';i.value=v;f.append(i)}document.body.append(f);f.submit()})()""".replace('BASE',json.dumps(url)))
        await asyncio.sleep(2)
        assert await c.js("document.querySelector('[role=alert]')?.textContent.includes('Não foi possível')"), 'duplicate accepted'
        await c.cmd('Page.navigate',{'url':url}); await asyncio.sleep(1)
        await c.js("""(()=>{let f=document.createElement('form');f.method='POST';f.enctype='multipart/form-data';f.action=BASE+'share-receive';let i=document.createElement('input');i.name='text';i.value='normal';f.append(i);let x=document.createElement('input');x.type='file';x.name='file';let dt=new DataTransfer();dt.items.add(new File(['123'],'clip.mp4',{type:'video/mp4'}));x.files=dt.files;f.append(x);document.body.append(f);f.submit()})()""".replace('BASE',json.dumps(url)))
        await asyncio.sleep(2)
        assert await c.js("document.querySelector('[role=alert]')?.textContent.includes('Não foi possível')"), 'file accepted'
        # Block JS startup on the POST result to inspect a still-uncollected payload.
        await c.cmd('Page.navigate',{'url':url}); await asyncio.sleep(1)
        for _ in range(2): await c.cmd('Target.createTarget',{'url':url})
        await asyncio.sleep(2)
        others=[p for p in pages() if p['type']=='page' and p['id']!=first['id']]
        assert len(others)>=2
        async with websockets.connect(others[0]['webSocketDebuggerUrl']) as w1, websockets.connect(others[1]['webSocketDebuggerUrl']) as w2:
            a,b=CDP(w1),CDP(w2)
            server.gate.clear()
            await submit('two-tabs-marker')
            take="""new Promise(resolve=>{let c=new MessageChannel();c.port1.onmessage=e=>resolve(e.data);navigator.serviceWorker.controller.postMessage({type:'CAIO_SHARE_TAKE'},[c.port2])})"""
            assert (await a.js(take)).get('error') and (await b.js(take)).get('error'), 'other tab stole receipt'
            result=await c.js(take); assert result.get('text')=='two-tabs-marker', f'target did not receive: {result}'
            assert (await c.js(take)).get('error'), 'receipt could be collected twice'
            server.gate.set(); await asyncio.sleep(2)
            assert await c.js("document.querySelector('[role=alert]')?.textContent.includes('Não foi possível')")
            # An uncollected receipt expires after the production TTL.
            await c.cmd('Page.navigate',{'url':url}); await asyncio.sleep(1)
            server.gate.clear(); await submit('expiry-marker'); await asyncio.sleep(31)
            assert (await c.js(take)).get('error'), 'expired receipt still collectible'
            server.gate.set(); await asyncio.sleep(2)
            assert await c.js("document.querySelector('[role=alert]')?.textContent.includes('Não foi possível')"), 'expiry not reported'
            # A worker restart discards volatile receipts and produces an explicit notice.
            await c.cmd('Page.navigate',{'url':url}); await asyncio.sleep(1)
            server.gate.clear(); await submit('worker-stop-marker')
            await c.cmd('ServiceWorker.enable'); await c.cmd('ServiceWorker.stopAllWorkers'); await asyncio.sleep(1)
            server.gate.set(); await asyncio.sleep(2)
            assert await c.js("document.querySelector('[role=alert]')?.textContent.includes('Não foi possível')"), 'worker loss not reported'
            assert await c.js("!document.querySelector('#received-preview')")
        assert not server.posts
        print('PASS: POST preview, no network POST, two attacker tabs, one collection, expiry, worker termination, size, duplicates, file, base='+base)
try:
    with tempfile.TemporaryDirectory() as profile:
        proc=subprocess.Popen(['chromium','--headless','--no-sandbox','--disable-gpu','--disable-background-networking','--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1','--remote-debugging-port=0',f'--user-data-dir={profile}','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            p=pathlib.Path(profile)/'DevToolsActivePort'
            for _ in range(100):
                if p.exists(): break
                time.sleep(.1)
            asyncio.run(check(int(p.read_text().splitlines()[0])))
        finally: proc.terminate(); proc.wait(timeout=10)
finally: server.gate.set(); server.shutdown()
