import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { build } from 'esbuild';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';

const require = createRequire(import.meta.url);
const bundle = await build({
  entryPoints: [new URL('../src/components/ui/caio-avatar.tsx', import.meta.url).pathname],
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  external: ['react', 'react/jsx-runtime'], loader: { '.png': 'dataurl' },
});
const module = { exports: {} };
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(require, module, module.exports);
const { CaioAvatar } = module.exports;
const read = path => readFile(new URL(path, import.meta.url));

test('avatar is the exact selected local image', async () => {
  const png = await read('../src/assets/caio-avatar.png');
  assert.equal(createHash('sha256').update(png).digest('hex'), 'e3d6e2bc71973953658f83b3c1287f83f4d0d39b7f0701f08416a5e2cb30627a');
});

test('both sizes render the local image as decorative adjacent to Caio text', () => {
  for (const size of ['small', 'welcome']) {
    const html = renderToStaticMarkup(createElement(CaioAvatar, { size }));
    assert.match(html, new RegExp(`caio-avatar--${size}`));
    assert.match(html, /aria-hidden="true"/);
    assert.match(html, /alt=""/);
    assert.match(html, /src="data:image\/png;base64,/);
    assert.doesNotMatch(html, /https?:\/\//);
  }
});

test('chat shows avatar persistently in header and in empty welcome', async () => {
  const source = String(await read('../src/components/ui/animated-ai-chat.tsx'));
  assert.match(source, /chat-header-identity[^\n]*<CaioAvatar\s*\/>/);
  assert.match(source, /messages\.length === 0 && !isTyping[\s\S]*?chat-welcome[^\n]*<CaioAvatar size="welcome"/);
});

test('responsive sizing preserves image aspect and reduced motion disables avatar animation', async () => {
  const css = String(await read('../src/chat.css'));
  assert.match(css, /\.caio-avatar\s*\{[^}]*aspect-ratio:1/);
  assert.match(css, /\.caio-avatar img\s*\{[^}]*object-fit:contain/);
  assert.match(css, /\.caio-avatar--welcome\s*\{[^}]*width:clamp\(/);
  assert.match(css, /@media\s*\(prefers-reduced-motion:reduce\)\s*\{\s*\.caio-avatar--welcome\s*\{\s*animation:none/);
});
