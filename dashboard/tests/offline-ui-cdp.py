#!/usr/bin/env python3
"""Offline smoke test against a Vite production build. Requires Chromium and Python websockets.

Run: python3 tests/offline-ui-cdp.py /tmp/caio-dashboard-build
Only a loopback static server is used. Application fetches are rejected in-page.
"""
import asyncio, base64, contextlib, functools, http.server, json, os, pathlib, subprocess, sys, tempfile, threading, time, urllib.request
import websockets

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else 'dist').resolve()
assert (root / 'index.html').is_file(), 'Build directory missing index.html'
class Static(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_): pass
    def end_headers(self):
        self.send_header('Content-Security-Policy', "default-src 'self'; connect-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'")
        super().end_headers()
server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Static, directory=str(root)))
threading.Thread(target=server.serve_forever, daemon=True).start()

async def main(port):
    for _ in range(100):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=1) as r: targets = json.load(r)
            if targets: break
        except Exception: await asyncio.sleep(.1)
    else: raise RuntimeError('Chromium CDP startup timed out')
    async with websockets.connect(targets[0]['webSocketDebuggerUrl'], max_size=25_000_000) as ws:
        counter = 0
        async def cmd(method, params=None):
            nonlocal counter
            counter += 1; ident = counter
            await ws.send(json.dumps({'id': ident, 'method':method, 'params':params or {}}))
            while True:
                result = json.loads(await asyncio.wait_for(ws.recv(), 30))
                if result.get('id') == ident:
                    if 'error' in result: raise RuntimeError(result['error'])
                    return result.get('result', {})
        await cmd('Page.enable'); await cmd('Runtime.enable'); await cmd('Network.enable')
        await cmd('Network.setBlockedURLs', {'urls':['http://*/*','https://*/*']})
        # Local static page must remain available; all application fetch is mocked below.
        await cmd('Network.setBlockedURLs', {'urls':['https://*/*']})
        await cmd('Page.addScriptToEvaluateOnNewDocument', {'source':'''window.__calls=[];window.fetch=(...a)=>{window.__calls.push(String(a[0]));return Promise.reject(new Error('offline test'));};'''})
        async def evaluate(js):
            result=await cmd('Runtime.evaluate', {'expression':js,'awaitPromise':True,'returnByValue':True})
            if 'exceptionDetails' in result: raise RuntimeError(str(result['exceptionDetails']))
            return result['result'].get('value')
        for width in (320,1280):
            for reduced in (False,True):
                await cmd('Emulation.setDeviceMetricsOverride', {'width':width,'height':800,'deviceScaleFactor':1,'mobile':width<768})
                await cmd('Emulation.setEmulatedMedia', {'features':[{'name':'prefers-reduced-motion','value':'reduce' if reduced else 'no-preference'}]})
                await cmd('Page.navigate', {'url':f'http://127.0.0.1:{server.server_port}/'})
                await asyncio.sleep(2)
                for label, action in (('chat', None), ('minimized', "document.querySelector('.caio-minimize-button').click()")):
                    if action:
                        await evaluate(action); await asyncio.sleep(.2)
                    shot = await cmd('Page.captureScreenshot', {'format':'png'})
                    pathlib.Path(f'/tmp/caio-ui-{label}-{width}-{"reduce" if reduced else "normal"}.png').write_bytes(base64.b64decode(shot['data']))
                await evaluate("document.querySelector('.caio-page-launcher').click()")
                await asyncio.sleep(.2)
                result = await evaluate('''(async()=>{
                  const delay=ms=>new Promise(r=>setTimeout(r,ms));
                  const check=(v,m)=>{if(!v)throw Error(m)};
                  const q=s=>document.querySelector(s);
                  check(innerWidth === WIDTH,'viewport '+innerWidth);
                  check(q('#chat-input'),'chat input missing');
                  q('.caio-minimize-button').click(); await delay(100);
                  const launcher=q('.caio-page-launcher'); check(launcher,'launcher missing');
                  check(document.activeElement===launcher,'launcher focus');
                  check(getComputedStyle(q('main')).display !== 'none' && !q('main').getClientRects().length,'app not hidden');
                  if(REDUCED) check(getComputedStyle(q('.caio-page-launcher-avatar')).animationName==='none','launcher animates');
                  launcher.click(); await delay(100);
                  check(!q('.caio-page-launcher'),'launcher persisted');
                  check(document.activeElement===q('.caio-minimize-button'),'focus not restored');
                  if(WIDTH<768){q('[aria-label="Abrir menu"]').click();await delay(500);}
                  let nav=[...document.querySelectorAll('nav button')].find(b=>b.textContent.trim()==='Integrações');
                  check(nav,'integration nav missing'); nav.click(); await delay(800);
                  check(q('h1')?.textContent==='Integrações','integration page missing');
                  for(const n of ['Gmail','Google Calendar','Google Drive','GitHub']) check([...document.querySelectorAll('h3')].some(x=>x.textContent===n),'missing '+n);
                  const buttons=[...document.querySelectorAll('button')].filter(x=>x.textContent.trim()==='Conectar');
                  check(buttons.length===4 && buttons.every(x=>x.disabled),'connect enabled');
                  check(document.body.textContent.includes('Estado não verificado')&&document.body.textContent.includes('não verifica contas'),'status dishonest');
                  check(!document.querySelector('input[type=password]'),'password field');
                  check(!window.__calls.some(u=>/oauth|integrations|connect/i.test(u)),'integration request');
                  check(document.documentElement.scrollWidth<=innerWidth,'horizontal overflow');
                  if(WIDTH<768) check(!q('.app-sidebar'),'mobile sidebar remained open');
                  return 'PASS';
                })()'''.replace('WIDTH',str(width)).replace('REDUCED','true' if reduced else 'false'))
                print(f'{width}px reduced={reduced}: {result}', flush=True)
                shot=await cmd('Page.captureScreenshot',{'format':'png'})
                pathlib.Path(f'/tmp/caio-ui-{width}-{"reduce" if reduced else "normal"}.png').write_bytes(base64.b64decode(shot['data']))

with tempfile.TemporaryDirectory(prefix='caio-cdp-') as profile:
    # Ephemeral local debugging port; never expose on public interfaces.
    proc=subprocess.Popen(['chromium','--headless','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--disable-background-networking','--disable-extensions','--disable-sync','--no-first-run','--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1','--remote-debugging-address=127.0.0.1','--remote-debugging-port=0','--remote-allow-origins=*',f'--user-data-dir={profile}','about:blank'], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        f=pathlib.Path(profile)/'DevToolsActivePort'
        for _ in range(100):
            if f.exists(): break
            if proc.poll() is not None: raise RuntimeError('Chromium exited during startup')
            time.sleep(.1)
        else: raise RuntimeError('Chromium port startup timed out')
        asyncio.run(main(int(f.read_text().splitlines()[0])))
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except subprocess.TimeoutExpired: proc.kill(); proc.wait()
        server.shutdown(); server.server_close()
