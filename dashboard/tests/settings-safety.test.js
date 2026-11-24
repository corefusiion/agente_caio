import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { build } from 'esbuild';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { DISPLAY_KEY, readMessageTextSize, writeMessageTextSize } from '../src/lib/display-preference.js';

const require = createRequire(import.meta.url);
async function component(path) {
  const bundle = await build({ entryPoints: [new URL(path, import.meta.url).pathname], bundle: true, write: false,
    platform: 'node', format: 'cjs', jsx: 'automatic', external: ['react', 'react/jsx-runtime'] });
  const module = { exports: {} };
  new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(require, module, module.exports);
  return module.exports.default;
}
const Settings = await component('../src/pages/CoreSettings.tsx');
const Skills = await component('../src/pages/AgentsPage.jsx');

test('message size persists locally, rejects invalid values and handles blocked storage', () => {
  const data = new Map();
  const storage = { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) };
  assert.equal(readMessageTextSize(storage), 'standard');
  assert.equal(writeMessageTextSize(storage, 'large'), true);
  assert.equal(data.get(DISPLAY_KEY), 'large');
  assert.equal(readMessageTextSize(storage), 'large');
  assert.equal(writeMessageTextSize(storage, 'invented'), false);
  assert.equal(data.get(DISPLAY_KEY), 'large');
  data.set(DISPLAY_KEY, 'invalid');
  assert.equal(readMessageTextSize(storage), 'standard');
  const blocked = { getItem() { throw Error('blocked') }, setItem() { throw Error('blocked') } };
  assert.equal(readMessageTextSize(blocked), 'standard');
  assert.equal(writeMessageTextSize(blocked, 'large'), false);
});

test('settings exposes only a labeled local reading control, no credentials or fake save', () => {
  const html = renderToStaticMarkup(createElement(Settings, { messageTextSize: 'large', onMessageTextSizeChange() {} }));
  assert.match(html, /Leitura no chat/);
  assert.match(html, /<input[^>]*checked=""[^>]*value="large"/);
  assert.match(html, /Esta tela não solicita nem salva senhas ou chaves/);
  assert.match(html, /disponibilidade real dos serviços não foi verificada/i);
  assert.doesNotMatch(html, /type="password"|textarea|Salvar Tudo|Conectado|ATIVADO/);
});

test('skills do not pretend catalog or status are live or offer non-working actions', () => {
  const html = renderToStaticMarkup(createElement(Skills));
  assert.match(html, /Categorias ilustrativas/);
  assert.match(html, /Disponibilidade não verificada/);
  assert.doesNotMatch(html, /Online|módulos ativos|>Abrir</);
});

test('settings and skills have no backend requests or toggle handlers', async () => {
  for (const name of ['CoreSettings.tsx', 'AgentsPage.jsx']) {
    const src = await readFile(new URL('../src/pages/' + name, import.meta.url), 'utf8');
    assert.doesNotMatch(src, /\bfetch\s*\(|\bapi\./);
  }
  const app = await readFile(new URL('../src/App.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(app, /toggleSkill\s*\(/);
  assert.match(app, /messageTextSize={messageTextSize}/);
  const css = await readFile(new URL('../src/chat.css', import.meta.url), 'utf8');
  assert.match(css, /\.large-message-text \.chat-message-content\s*\{[^}]*font-size:19px/);
});
