// Run with: node --test src/components/ui/message-actions.test.mjs
// Minimal hook harness: exercises handlers without adding browser/test dependencies.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';

const file = fileURLToPath(new URL('./message-actions.tsx', import.meta.url));
const result = await build({
  entryPoints: [file], bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  plugins: [{
    name: 'test-stubs',
    setup(build) {
      build.onResolve({ filter: /^(react|react\/jsx-runtime|lucide-react)$/ }, args => ({ path: args.path, namespace: 'stub' }));
      build.onResolve({ filter: /\.css$/ }, () => ({ path: 'css', namespace: 'stub' }));
      build.onLoad({ filter: /.*/, namespace: 'stub' }, ({ path }) => {
        if (path === 'react') return { contents: 'export const useState = (...a) => globalThis.hooks.useState(...a); export const useRef = (...a) => globalThis.hooks.useRef(...a); export const useEffect = (...a) => globalThis.hooks.useEffect(...a);', loader: 'js' };
        if (path === 'react/jsx-runtime') return { contents: 'export const jsx = (type, props, key) => ({type, props, key}); export const jsxs = jsx;', loader: 'js' };
        if (path === 'lucide-react') return { contents: 'export const Check=()=>null, Copy=()=>null, Square=()=>null, ThumbsDown=()=>null, ThumbsUp=()=>null, Volume2=()=>null;', loader: 'js' };
        return { contents: '', loader: 'js' };
      });
    },
  }],
});
const code = result.outputFiles[0].text;

function setup({ clipboard, synthesis, Utterance } = {}) {
  const slots = [];
  let cursor = 0;
  const cleanups = [];
  const hooks = {
    useState(initial) {
      const i = cursor++;
      if (!(i in slots)) slots[i] = initial;
      return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }];
    },
    useRef(initial) {
      const i = cursor++;
      if (!(i in slots)) slots[i] = { current: initial };
      return slots[i];
    },
    useEffect(effect) {
      const i = cursor++;
      if (!(i in slots)) { slots[i] = true; cleanups.push(effect()); }
    },
  };
  const module = { exports: {} };
  const context = { module, exports: module.exports, hooks, navigator: { clipboard }, window: {}, setTimeout, clearTimeout };
  if (synthesis) context.window.speechSynthesis = synthesis;
  if (Utterance) context.window.SpeechSynthesisUtterance = Utterance;
  vm.runInNewContext(code, context);
  const parent = module.exports.MessageActions({ content: 'Olá, mundo!' });
  function render() { cursor = 0; return parent.type(parent.props); }
  function find(node, predicate) {
    if (!node || typeof node !== 'object') return undefined;
    if (predicate(node)) return node;
    for (const child of [node.props?.children].flat(Infinity)) {
      const found = find(child, predicate);
      if (found) return found;
    }
  }
  const button = name => find(render(), n => n.type === 'button' && n.props['aria-label']?.includes(name));
  const status = () => find(render(), n => n.props?.role === 'status').props.children;
  const unmount = () => cleanups.forEach(fn => fn?.());
  render();
  return { button, status, unmount };
}

test('cópia bem-sucedida e falha são anunciadas sem expor texto', async () => {
  let text;
  const ui = setup({ clipboard: { writeText: async value => { text = value; } } });
  await ui.button('Copiar').props.onClick();
  assert.equal(text, 'Olá, mundo!');
  assert.equal(ui.status(), 'Resposta copiada.');
  assert.ok(ui.button('Resposta copiada'));
  ui.unmount();
  const failed = setup({ clipboard: { writeText: async () => { throw new Error('segredo'); } } });
  await failed.button('Copiar').props.onClick();
  assert.match(failed.status(), /Não foi possível copiar/);
  assert.doesNotMatch(failed.status(), /segredo|Olá/);
  failed.unmount();
});

test('reações são exclusivas, reversíveis e identificadas como locais', () => {
  const ui = setup();
  ui.button('Curtir resposta').props.onClick();
  assert.equal(ui.button('Curtir resposta').props['aria-pressed'], true);
  assert.match(ui.status(), /temporária.*não é enviada/);
  ui.button('Não curtir').props.onClick();
  assert.equal(ui.button('Curtir resposta').props['aria-pressed'], false);
  assert.equal(ui.button('Não curtir').props['aria-pressed'], true);
  ui.button('Não curtir').props.onClick();
  assert.equal(ui.button('Não curtir').props['aria-pressed'], false);
  ui.unmount();
});

test('leitura não suportada recebe aviso', () => {
  const ui = setup();
  ui.button('Ouvir').props.onClick();
  assert.match(ui.status(), /não é compatível/);
  ui.unmount();
});

test('leitura pode parar e é limpa ao desmontar', () => {
  let cancelled = 0;
  let spoken;
  const synthesis = { speaking: false, pending: false, getVoices: () => [{ localService: true, lang: 'pt-BR' }], speak: utterance => { spoken = utterance; }, cancel: () => { cancelled++; } };
  function Utterance(text) { this.text = text; }
  const ui = setup({ synthesis, Utterance });
  ui.button('Ouvir').props.onClick();
  assert.equal(spoken.text, 'Olá, mundo!');
  assert.equal(spoken.lang, 'pt-BR');
  ui.button('Parar').props.onClick();
  assert.equal(cancelled, 1);
  assert.match(ui.status(), /interrompida/);
  ui.button('Ouvir').props.onClick();
  ui.unmount();
  assert.equal(cancelled, 2);
});

test('não interrompe leitura pertencente a outro recurso', () => {
  let cancelled = 0;
  const synthesis = { speaking: true, pending: false, speak: () => assert.fail('speak'), cancel: () => { cancelled++; } };
  const ui = setup({ synthesis, Utterance: function() {} });
  ui.button('Ouvir').props.onClick();
  assert.match(ui.status(), /Outra leitura/);
  ui.unmount();
  assert.equal(cancelled, 0);
});
