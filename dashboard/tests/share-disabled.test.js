import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { startShareReceipt } from '../src/lib/share-receipt.js';

const read = path => readFile(new URL(path, import.meta.url), 'utf8');

test('manifesto contém PNGs RGBA do rosto nas dimensões declaradas', async () => {
  const manifest = JSON.parse(await read('../public/manifest.webmanifest'));
  assert.equal(manifest.icons.length, 2);
  for (const [index, size] of [192, 512].entries()) {
    const icon = manifest.icons[index];
    assert.deepEqual(icon, { src: `icons/caio-rosto-${size}.png`, sizes: `${size}x${size}`, type: 'image/png' });
    const image = await readFile(new URL(`../public/${icon.src}`, import.meta.url));
    assert.deepEqual(image.subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
    assert.equal(image.toString('ascii', 12, 16), 'IHDR');
    assert.equal(image.readUInt32BE(16), size);
    assert.equal(image.readUInt32BE(20), size);
    assert.equal(image[24], 8);
    assert.equal(image[25], 6); // RGBA
    assert.equal(new URL(icon.src, 'https://example.test/app/manifest.webmanifest').pathname, `/app/icons/caio-rosto-${size}.png`);
  }
});

test('ícone do navegador usa o rosto escolhido sem ativar o manifesto', async () => {
  const html = await read('../index.html');
  assert.match(html, /<link\b[^>]*rel=["']icon["'][^>]*href=["']\/icons\/caio-rosto-192\.png["']/i);
  assert.doesNotMatch(html, /<link\b[^>]*\brel\s*=\s*["']manifest["']/i);
});

test('manifest não anuncia share target nem é vinculado pelo dashboard', async () => {
  const manifest = JSON.parse(await read('../public/manifest.webmanifest'));
  assert.equal(Object.hasOwn(manifest, 'share_target'), false);
  const html = await read('../index.html');
  assert.doesNotMatch(html, /<link\b[^>]*\brel\s*=\s*["']manifest["']/i);
});

test('recepção permanece desativada e não registra service worker', async () => {
  let registrations = 0;
  globalThis.navigator = {serviceWorker: {register() { registrations++; throw new Error('unexpected registration'); }}};
  globalThis.location = {pathname:'/'};
  assert.equal(await startShareReceipt(), null);
  globalThis.location.pathname = '/app/share-receive';
  assert.deepEqual(await startShareReceipt(), {error:true});
  assert.equal(registrations, 0);
  const code = await read('../src/lib/share-receipt.js');
  assert.doesNotMatch(code, /serviceWorker\s*\.\s*register\s*\(/);
});

test('worker substituto rejeita POST sem ler nem persistir conteúdo', async () => {
  const code = await read('../public/share-worker.js');
  assert.doesNotMatch(code, /indexedDB|localStorage|caches\.|console\.|Response\.redirect/);
  const handlers = {};
  const self = {registration: {scope:'https://example.test/app/'}, addEventListener: (name, fn) => { handlers[name] = fn; }};
  vm.runInNewContext(code, {self, URL, Response});
  let response;
  const request = {url:'https://example.test/app/share-receive', method:'POST', get body() { throw Error('body read'); }};
  handlers.fetch({request, respondWith(value) { response = value; }});
  assert.equal(response.status, 410);
  assert.equal(response.headers.get('Cache-Control'), 'no-store');
  assert.doesNotMatch(await response.text(), /secret-marker/);
  for (const request of [
    {url:'https://example.test/app/share-receive', method:'GET'},
    {url:'https://other.test/app/share-receive', method:'POST'},
    {url:'https://example.test/app/elsewhere', method:'POST'},
  ]) {
    let intercepted = false;
    handlers.fetch({request, respondWith() { intercepted = true; }});
    assert.equal(intercepted, false);
  }
});
