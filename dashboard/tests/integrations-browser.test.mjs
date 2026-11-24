// Offline test of the real application and its integration navigation.
import { build } from 'esbuild';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const dir = await mkdtemp(join(tmpdir(), 'caio-integrations-'));
try {
  const fixture = `
    import React from 'react';
    import {createRoot} from 'react-dom/client';
    import {flushSync} from 'react-dom';
    import App from ${JSON.stringify(resolve('src/App.tsx'))};
    const check = (ok, message) => { if (!ok) throw new Error(message); };
    const calls = [];
    window.fetch = (...args) => { calls.push(String(args[0])); return Promise.reject(new Error('offline')); };
    flushSync(() => createRoot(document.getElementById('root')).render(React.createElement(App)));
    const test = async () => {
      try {
        if (matchMedia('(max-width: 767px)').matches) document.querySelector('[aria-label="Abrir menu"]').click();
        await new Promise(r => setTimeout(r, 350));
        const nav = [...document.querySelectorAll('nav button')].find(b => b.textContent.trim() === 'Integrações');
        check(nav, 'navigation missing'); nav.click();
        await new Promise(r => setTimeout(r, 700));
        check(document.querySelector('h1')?.textContent === 'Integrações', 'page missing');
        for (const name of ['Gmail','Google Calendar','Google Drive','GitHub']) check([...document.querySelectorAll('h3')].some(h => h.textContent === name), 'missing ' + name);
        const buttons = [...document.querySelectorAll('button')].filter(b => b.textContent.trim() === 'Conectar');
        check(buttons.length === 4 && buttons.every(b => b.disabled && b.getAttribute('aria-describedby') === 'integrations-help'), 'connection buttons not safely disabled');
        check(document.body.textContent.includes('Estado não verificado') && document.body.textContent.includes('não verifica contas'), 'unverified status missing');
        check(!document.querySelector('input[type=password], form[action]'), 'credentials or form present');
        check(!calls.some(url => /oauth|integrations|connect/i.test(url)), 'integration network request');
        check(document.documentElement.scrollWidth <= innerWidth, 'horizontal overflow');
        if (matchMedia('(max-width: 767px)').matches) check(!document.querySelector('.app-sidebar'), 'mobile menu did not close');
        else check(nav.getAttribute('aria-current') === 'page', 'active state missing');
        document.body.dataset.result = 'PASS';
      } catch (error) { document.body.dataset.result = 'FAIL: ' + error.message; }
    };
    test();
  `;
  const result = await build({stdin:{contents:fixture,resolveDir:process.cwd(),sourcefile:'integration-fixture.jsx',loader:'jsx'},bundle:true,write:false,outfile:join(dir,'fixture.js'),format:'iife',loader:{'.css':'css','.png':'dataurl'}});
  const js = result.outputFiles.find(f => f.path.endsWith('.js')).text;
  const css = result.outputFiles.find(f => f.path.endsWith('.css'))?.text || '';
  const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:"><style>${css}</style></head><body><div id="root"></div><script>${js.replaceAll('</script','<\\/script')}</script></body></html>`;
  const page = join(dir,'index.html'); await writeFile(page, html);
  for (const width of [320, 1280]) {
    const run = spawnSync('chromium',['--headless','--no-sandbox','--disable-gpu','--disable-background-networking','--disable-extensions','--disable-sync','--no-first-run','--host-resolver-rules=MAP * ~NOTFOUND',`--window-size=${width},900`,'--virtual-time-budget=4000','--dump-dom',`file://${page}`],{encoding:'utf8',timeout:30000,maxBuffer:10_000_000});
    if (run.error || run.status !== 0 || !run.stdout.includes('data-result="PASS"')) throw new Error(`width ${width} failed: ${run.error || run.status}\n${run.stdout.slice(0,500)}\n${run.stderr.slice(-1000)}`);
    console.log(`PASS: integrações no aplicativo em ${width}px, ações desabilitadas e sem OAuth`);
  }
} finally { await rm(dir,{recursive:true,force:true}); }
