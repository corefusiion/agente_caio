// Offline Chromium component test. Uses a temporary file:// fixture, no API or external services.
import { build } from 'esbuild';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const dir = await mkdtemp(join(tmpdir(), 'caio-queue-browser-'));
try {
  const fixture = `
    import React from 'react';
    import {createRoot} from 'react-dom/client';
    import {flushSync} from 'react-dom';
    import {LocalQueueView} from ${JSON.stringify(resolve('src/components/ui/local-queue-view.tsx'))};
    const root = createRoot(document.getElementById('root'));
    const draw = (props) => flushSync(() => root.render(React.createElement(LocalQueueView, props)));
    const check = (ok, message) => { if (!ok) throw new Error(message); };
    try {
      const states = ['pendente','em_andamento','concluida','falhou','bloqueada','cancelada'];
      const tasks = states.map((status, i) => ({id: String(i), status, description: 'Teste ' + status, priority: 0, evidence: status === 'concluida' ? '<img src=x>\\nProva' : null, reason: status === 'falhou' ? 'Falha de teste' : null}));
      draw({mode:'ready', tasks});
      const section = document.querySelector('section');
      for (const label of ['Pendente','Em andamento','Concluída','Falhou','Bloqueada','Cancelada','Evidência:','<img src=x>','Motivo:']) check(section.textContent.includes(label), 'missing ' + label);
      check(!section.querySelector('button,form,progress,img,a'), 'unexpected action, markup or progress');
      check(section.scrollWidth <= section.clientWidth, 'horizontal overflow at 320px');
      draw({mode:'error', reason:'Teste'});
      check(document.querySelector('[role=alert]').textContent.includes('Teste'), 'error missing');
      check(!document.getElementById('root').textContent.includes('Nenhuma tarefa'), 'error appears empty');
      draw({mode:'ready', tasks:[]});
      check(document.getElementById('root').textContent.includes('nesta página'), 'page empty state missing');
      draw({mode:'ready', tasks:null});
      check(document.querySelector('[role=alert]').textContent.includes('inválidos'), 'invalid data accepted');
      document.body.dataset.result = 'PASS';
    } catch (error) { document.body.dataset.result = 'FAIL: ' + error.message; }
  `;
  const result = await build({stdin: {contents: fixture, resolveDir: process.cwd(), sourcefile: 'queue-fixture.jsx', loader:'jsx'}, bundle:true, write:false, outfile: join(dir, 'fixture.js'), format:'iife', loader:{'.css':'css'}});
  const js = result.outputFiles.find(f => f.path.endsWith('.js')).text;
  const css = result.outputFiles.find(f => f.path.endsWith('.css')).text;
  const html = `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'"><style>${css}</style></head><body><div id="root" style="width:320px"></div><script>${js.replaceAll('</script', '<\\/script')}</script></body></html>`;
  const page = join(dir, 'index.html');
  await writeFile(page, html);
  const run = spawnSync('chromium', ['--headless','--no-sandbox','--disable-gpu','--disable-background-networking','--disable-extensions','--disable-sync','--no-first-run','--host-resolver-rules=MAP * ~NOTFOUND','--dump-dom', `file://${page}`], {encoding:'utf8', timeout:30000, maxBuffer:5_000_000});
  if (run.error || run.status !== 0 || !run.stdout.includes('data-result="PASS"')) {
    throw new Error(`Chromium test failed: ${run.error || run.status}\n${run.stdout.slice(-4000)}\n${run.stderr.slice(-1500)}`);
  }
  console.log('PASS: Chromium offline, seis estados, evidência segura, erro/vazio/inválido, largura 320px e nenhuma ação');
} finally { await rm(dir, {recursive:true, force:true}); }
