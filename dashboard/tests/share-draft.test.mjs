import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { build } from 'esbuild';

const result = await build({
  entryPoints: [new URL('../src/components/ui/share-draft.tsx', import.meta.url).pathname],
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  plugins: [{ name: 'stubs', setup(b) {
    b.onResolve({ filter: /^(react|react\/jsx-runtime)$/ }, args => ({ path: args.path, namespace: 'stub' }));
    b.onLoad({ filter: /.*/, namespace: 'stub' }, ({ path }) => ({ loader: 'js', contents: path === 'react'
      ? 'export const useState = (...a) => globalThis.hooks.useState(...a);'
      : 'export const jsx = (type, props) => ({type, props}); export const jsxs = jsx;' }));
  }}],
});
function setup(disabled = false) {
  const slots = []; let cursor = 0; const prepared = [];
  const hooks = { useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = initial; return [slots[i], next => { slots[i] = typeof next === 'function' ? next(slots[i]) : next; }]; } };
  const module = { exports: {} };
  // No navigator, storage, location or fetch exists: rendering/handlers must not use them.
  vm.runInNewContext(result.outputFiles[0].text, { module, exports: module.exports, hooks });
  const render = () => { cursor = 0; return module.exports.ShareDraft({ onPrepare: value => prepared.push(value), disabled }); };
  function find(node, pred) { if (!node || typeof node !== 'object') return; if (pred(node)) return node; for (const child of [node.props?.children].flat(Infinity)) { const hit = find(child, pred); if (hit) return hit; } }
  const button = text => find(render(), n => n.type === 'button' && n.props.children === text);
  const textarea = () => find(render(), n => n.type === 'textarea');
  return { render, button, textarea, prepared, find };
}
test('abre apenas com ação, conserva em memória e limpa ao fechar', () => {
  const ui = setup();
  assert.equal(ui.textarea(), undefined);
  assert.equal(ui.button('Colar texto ou link').props['aria-expanded'], false);
  ui.button('Colar texto ou link').props.onClick();
  assert.equal(ui.button('Colar texto ou link').props['aria-expanded'], true);
  const secret = 'https://example.invalid/segredo?token=privado';
  ui.textarea().props.onChange({ target: { value: secret } });
  assert.equal(ui.textarea().props.value, secret);
  assert.equal(ui.textarea().props.maxLength, 8192);
  ui.button('Adicionar ao rascunho do chat').props.onClick();
  assert.equal(ui.prepared.length, 1);
  assert.ok(ui.prepared[0].includes(secret));
  assert.match(ui.prepared[0], /Não abra links/);
  assert.equal(ui.textarea(), undefined);
  ui.button('Colar texto ou link').props.onClick();
  assert.equal(ui.textarea().props.value, '');
  // A different mounted instance/session starts empty.
  const other = setup(); other.button('Colar texto ou link').props.onClick();
  assert.equal(other.textarea().props.value, '');
});
test('não aceita arquivos colados ou arrastados', () => {
  const ui = setup(); ui.button('Colar texto ou link').props.onClick();
  let prevented = 0;
  ui.textarea().props.onPaste({ clipboardData: { files: [{}] }, preventDefault: () => prevented++ });
  assert.equal(prevented, 1);
  assert.match(ui.find(ui.render(), n => n.props?.role === 'status').props.children, /vídeos.*não são aceitos/);
  ui.textarea().props.onDrop({ preventDefault: () => prevented++ });
  assert.equal(prevented, 2);
  ui.textarea().props.onPaste({ clipboardData: { files: [], items: [{ kind: 'file' }] }, preventDefault: () => prevented++ });
  assert.equal(prevented, 3);
  assert.match(ui.find(ui.render(), n => n.props?.role === 'status').props.children, /vídeos.*não são aceitos/);
});
test('botões não submetem formulário e o pedido não substitui o envio', () => {
  const ui = setup(true); ui.button('Colar texto ou link').props.onClick();
  assert.equal(ui.button('Adicionar ao rascunho do chat').props.disabled, true);
  for (const label of ['Colar texto ou link', 'Adicionar ao rascunho do chat', 'Limpar e fechar']) assert.equal(ui.button(label).props.type, 'button');
  assert.equal(ui.textarea().props.id, 'share-draft-text');
  assert.equal(ui.textarea().props['aria-describedby'], 'share-draft-limits');
});


test('cola apenas texto simples na seleção, sem truncar ou transferir automaticamente', () => {
  const ui = setup(); ui.button('Colar texto ou link').props.onClick();
  ui.textarea().props.onChange({ target: { value: 'antes depois' } });
  const paste = (text, start, end) => {
    let prevented = false;
    ui.textarea().props.onPaste({
      clipboardData: { files: [], items: [{kind: 'string'}], getData: type => { assert.equal(type, 'text/plain'); return text; } },
      currentTarget: { selectionStart: start, selectionEnd: end }, preventDefault: () => { prevented = true; },
    });
    assert.equal(prevented, true);
  };
  paste('<b>link</b>', 6, 12);
  assert.equal(ui.textarea().props.value, 'antes <b>link</b>');
  assert.equal(ui.prepared.length, 0);
  const saved = ui.textarea().props.value;
  paste('x'.repeat(8193), 0, 0);
  assert.equal(ui.textarea().props.value, saved);
  assert.match(ui.find(ui.render(), n => n.props?.role === 'status').props.children, /não colado.*8.192/);
  paste('x'.repeat(8192), 0, saved.length);
  assert.equal(ui.textarea().props.value.length, 8192);
  ui.textarea().props.onChange({ target: { value: 'x'.repeat(8193) } });
  assert.equal(ui.textarea().props.value.length, 8192);
});

test('dados indisponíveis ou sem texto não são aceitos', () => {
  const ui = setup(); ui.button('Colar texto ou link').props.onClick();
  let prevented = 0;
  ui.textarea().props.onPaste({ clipboardData: null, preventDefault: () => prevented++ });
  assert.match(ui.find(ui.render(), n => n.props?.role === 'status').props.children, /Não foi possível/);
  ui.textarea().props.onPaste({ clipboardData: { files: [], items: [], getData: () => '' }, preventDefault: () => prevented++ });
  assert.match(ui.find(ui.render(), n => n.props?.role === 'status').props.children, /Nenhum texto simples/);
  assert.equal(prevented, 2);
  assert.equal(ui.textarea().props.value, '');
});
