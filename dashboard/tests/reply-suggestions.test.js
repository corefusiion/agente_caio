import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { build } from 'esbuild';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';

// Compile in memory only; no network, DOM package or build output required.
const require = createRequire(import.meta.url);
const bundle = await build({
  entryPoints: [new URL('../src/components/ui/reply-suggestions.tsx', import.meta.url).pathname],
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  external: ['react', 'react/jsx-runtime'], loader: { '.css': 'empty' },
});
const module = { exports: {} };
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(require, module, module.exports);
const { ReplySuggestions } = module.exports;
const props = (content, suggestions, onSelect = () => {}) => ({ content, suggestions, onSelect });
function getButtons(element) { return element.props.children[1].props.children; }
function labels(element) { return getButtons(element).map(button => button.props.children); }

test('empty responses render nothing', () => {
  for (const content of ['', '  ', null, undefined]) assert.equal(ReplySuggestions(props(content)), null);
});

test('fallback is three distinct short Portuguese options, explicitly general', () => {
  const element = ReplySuggestions(props('Uma resposta sem tema específico.'));
  assert.equal(element.props['aria-label'], 'Sugestões gerais');
  assert.deepEqual(labels(element), ['Resuma essa resposta', 'Explique melhor', 'Quais são os próximos passos?']);
  const html = renderToStaticMarkup(createElement(ReplySuggestions, props('Olá')));
  assert.match(html, /Sugestões gerais/);
  assert.equal((html.match(/type="button"/g) || []).length, 3);
});

test('broad local context changes options without echoing personal response', () => {
  const secret = 'NomePrivado 123-CPF';
  for (const [content, first] of [
    [`O orçamento é 100. ${secret}`, 'Resuma os custos'],
    [`Etapas do projeto ${secret}`, 'Resuma as etapas'],
    [`Ingredientes ${secret}`, 'Resuma o preparo'],
  ]) {
    const element = ReplySuggestions(props(content));
    assert.equal(labels(element)[0], first);
    assert.equal(JSON.stringify(element).includes(secret), false);
  }
});

test('accepts exactly three valid supplied options and sends only selected text', () => {
  const selected = [];
  const element = ReplySuggestions(props('Resposta', [' Primeira ', 'Segunda', 'Terceira'], text => selected.push(text)));
  assert.equal(element.props['aria-label'], 'Sugestões');
  assert.deepEqual(labels(element), ['Primeira', 'Segunda', 'Terceira']);
  for (const button of getButtons(element)) { assert.equal(button.props.type, 'button'); button.props.onClick(); }
  assert.deepEqual(selected, ['Primeira', 'Segunda', 'Terceira']);
});

test('malformed, duplicate, multiline and oversized suggestions fall back as a set', () => {
  for (const suggestions of [null, {}, [], ['a', 'b'], ['a', 'b', 'c', 'd'], ['a', 4, 'c'], ['a', '', 'c'], ['sim', ' SIM ', 'não'], ['a', 'b\nc', 'd'], ['a', '\u202Efake', 'd'], ['a', 'x'.repeat(81), 'c']]) {
    const element = ReplySuggestions(props('Resposta', suggestions));
    assert.equal(element.props['aria-label'], 'Sugestões gerais');
    assert.deepEqual(labels(element), ['Resuma essa resposta', 'Explique melhor', 'Quais são os próximos passos?']);
  }
});

test('untrusted option text is escaped in markup, not interpreted as HTML', () => {
  const html = renderToStaticMarkup(createElement(ReplySuggestions, props('Resposta', ['<img src=x>', 'Outra', 'Mais uma'])));
  assert.match(html, /&lt;img src=x&gt;/);
  assert.doesNotMatch(html, /<img/);
});
