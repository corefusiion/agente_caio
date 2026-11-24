import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { build } from 'esbuild';

const require = createRequire(import.meta.url);
const bundle = await build({
  entryPoints: [new URL('../src/components/ui/message-actions.tsx', import.meta.url).pathname],
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  external: ['react', 'react/jsx-runtime'], loader: { '.css': 'empty' },
});

function setup(voices, failVoices = false) {
  const states = [];
  let constructed = 0;
  const spoken = [];
  const react = {
    ...require('react'),
    useState(initial) { const i = states.length; states.push(initial); return [initial, value => { states[i] = value; }]; },
    useRef(initial) { return { current: initial }; },
    useEffect(effect) { effect(); },
  };
  class Utterance { constructor(text) { constructed++; this.text = text; } }
  globalThis.window = {
    SpeechSynthesisUtterance: Utterance,
    speechSynthesis: {
      speaking: false, pending: false,
      getVoices() { if (failVoices) throw new Error('unavailable'); return voices; },
      speak(utterance) { spoken.push(utterance); },
      cancel() {},
    },
  };
  const module = { exports: {} };
  new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(
    name => name === 'react' ? react : require(name), module, module.exports,
  );
  const outer = module.exports.MessageActions({ content: 'texto privado' });
  const rendered = outer.type(outer.props);
  const buttons = rendered.props.children[0].props.children;
  const listen = buttons.find(button => button.props['aria-label'] === 'Ouvir resposta');
  return { listen: () => listen.props.onClick(), states, spoken, count: () => constructed };
}

test('without explicitly local voice, no private utterance is constructed or spoken', () => {
  for (const voices of [[], [{ lang: 'pt-BR', localService: false }], [{ lang: 'pt-BR' }]]) {
    const s = setup(voices); s.listen();
    assert.equal(s.count(), 0);
    assert.equal(s.spoken.length, 0);
    assert.match(s.states[3], /Nenhuma voz local disponível/);
  }
});

test('uses an explicitly local voice, preferring Brazilian Portuguese over remote voices', () => {
  const remote = { lang: 'pt-BR', localService: false };
  const local = { lang: 'pt-BR', localService: true };
  const s = setup([remote, { lang: 'en-US', localService: true }, local]); s.listen();
  assert.equal(s.spoken.length, 1);
  assert.equal(s.spoken[0].voice, local);
  assert.equal(s.spoken[0].text, 'texto privado');
});

test('failure loading voices never falls back to a default voice', () => {
  const s = setup([], true); s.listen();
  assert.equal(s.count(), 0);
  assert.equal(s.spoken.length, 0);
  assert.match(s.states[3], /Não foi possível/);
});
