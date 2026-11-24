// Offline component and real local worker fixture; no network, token, proxy or shared data.
// Run from dashboard: node tests/local-queue-chat-gate.test.mjs
import { build } from 'esbuild';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const dir = await mkdtemp(join(tmpdir(), 'caio-gate-test-'));
try {
  // Disposable POSIX queue, actual worker and actual SQLite reopen; never production data.
  const python = `
import json, os, pathlib, sys, time
sys.path.insert(0, os.path.abspath('..'))
from caiocore.task_queue import Queue
from caiocore.task_queue.worker import LocalTextWorker
p = pathlib.Path(sys.argv[1]) / 'q'
p.mkdir(mode=0o700)
q = Queue(p, 'local_operator')
t = q.create_text_stats('Olá mundo', consent=True)
w = LocalTextWorker(q, interval=.01)
w.start()
try:
    for i in range(400):
        r = q.get(t['id'])
        if r['status'] == 'concluida': break
        time.sleep(.01)
    else: raise RuntimeError('worker timeout')
finally: w.stop()
r2 = Queue(p, 'local_operator').get(t['id'])
assert r2 == r
assert json.loads(r2['evidence']) == {'tipo':'estatisticas_texto_local_v1','caracteres':9,'palavras':2,'linhas':1,'bytes_utf8':10}
print(json.dumps(r2, ensure_ascii=True))
`;
  const worker = spawnSync('python', ['-c', python, dir], {cwd: process.cwd(), encoding:'utf8', timeout:15000, env:{...process.env, PYTHONDONTWRITEBYTECODE:'1'}});
  if (worker.status !== 0) throw new Error('real worker fixture failed: ' + worker.stderr);
  const task = JSON.parse(worker.stdout);
  // The gate MUST ignore even a true real result passed from an untrusted caller.
  const fixture = `
    import React from 'react';
    import {createRoot} from 'react-dom/client';
    import {flushSync} from 'react-dom';
    import {LocalQueueChatGate} from ${JSON.stringify(resolve('src/components/ui/local-queue-chat-gate.tsx'))};
    import {LocalQueueView} from ${JSON.stringify(resolve('src/components/ui/local-queue-view.tsx'))};
    const task = ${JSON.stringify(task)};
    let calls = 0;
    window.fetch = () => { calls++; throw Error('network forbidden'); };
    window.XMLHttpRequest = class { constructor() { calls++; throw Error('network forbidden'); } };
    window.WebSocket = class { constructor() { calls++; throw Error('network forbidden'); } };
    const el = document.getElementById('root');
    let root = createRoot(el);
    const draw = (props, key) => flushSync(() => root.render(React.createElement(LocalQueueChatGate, { ...props, key })));
    const check = (ok, message) => { if (!ok) throw Error(message); };
    try {
      for (const optIn of [undefined, null, false, 1, 'true', {}, []]) {
        draw({optIn}); check(el.textContent === '', 'truthy untrusted opt-in accepted');
      }
      const injected = { optIn:true, sessionId:'privado-a', text:'segredo-a', owner:'other', token:'TOKEN-SECRETO', tasks:[task], worker:{running:true}, consent:true, endpoint:'https://example.invalid' };
      draw(injected, 'a');
      let t = el.textContent;
      for (const word of ['indisponível','isolamento por sessão','Nenhuma tarefa foi enviada ou consultada','somente estatísticas locais','não é IA','consentimento explícito','servidor ativo','Windows']) check(t.includes(word), 'missing ' + word);
      for (const secret of ['privado-a','segredo-a','TOKEN-SECRETO',task.id, task.evidence]) check(!t.includes(secret), 'leaked data');
      check(!el.querySelector('button,input,textarea,form,a,progress'), 'action exists');
      draw({optIn:true, sessionId:'b', tasks:[{...task, description:'SEGREDO-B'}]}, 'b');
      check(!el.textContent.includes('SEGREDO-B') && !el.textContent.includes(task.id), 'session data leaked');
      flushSync(() => root.unmount()); root = createRoot(el);
      draw({optIn:true, sessionId:'a'}, 'a');
      check(!el.textContent.includes('segredo-a') && !el.textContent.includes(task.id), 'reopen leaked prior data');
      draw({optIn:false, sessionId:'a'}, 'a'); check(el.textContent === '', 'opt-out failed');
      check(calls === 0, 'unexpected network call');
      // The existing, disconnected read-only presentation can display authentic evidence,
      // but this does NOT prove a secure chat integration or authorize mounting it there.
      flushSync(() => root.render(React.createElement(LocalQueueView, {mode:'ready', tasks:[task]})));
      t = el.textContent;
      check(t.includes('Concluída') && t.includes('Evidência:') && t.includes('estatisticas_texto_local_v1') && t.includes(task.evidence) && JSON.parse(task.evidence).palavras === 2, 'real worker evidence missing');
      check(!el.querySelector('button,form,progress'), 'unsafe action');
      document.body.dataset.result = 'PASS';
    } catch (error) { document.body.dataset.result = 'FAIL: ' + error.message; }
  `;
  const built = await build({stdin:{contents:fixture, resolveDir:process.cwd(), sourcefile:'gate-fixture.jsx', loader:'jsx'}, bundle:true, write:false, outfile:join(dir,'fixture.js'), format:'iife', loader:{'.css':'css'}});
  const js = built.outputFiles.find(f => f.path.endsWith('.js')).text;
  const css = built.outputFiles.find(f => f.path.endsWith('.css')).text;
  const html = `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'"><style>${css}</style></head><body><div id="root" style="width:320px"></div><script>${js.replaceAll('</script', '<\\/script')}</script></body></html>`;
  const page = join(dir, 'index.html');
  await writeFile(page, html);
  const run = spawnSync('chromium', ['--headless','--no-sandbox','--disable-gpu','--disable-background-networking','--disable-extensions','--disable-sync','--no-first-run','--host-resolver-rules=MAP * ~NOTFOUND','--dump-dom', `file://${page}`], {encoding:'utf8', timeout:30000, maxBuffer:5_000_000});
  if (run.error || run.status !== 0 || !run.stdout.includes('data-result="PASS"')) throw Error(`Chromium failed: ${run.error || run.status}\n${run.stdout.match(/data-result="[^"]*"/)?.[0] || run.stdout.slice(0,2000)}\n${run.stderr.slice(-1000)}`);
  console.log('PASS: gate offline, opt-in estrito, dados não confiáveis ignorados, troca/reabertura, nenhuma ação/rede; apresentação isolada de resultado real persistido.');
} finally { await rm(dir, {recursive:true, force:true}); }
