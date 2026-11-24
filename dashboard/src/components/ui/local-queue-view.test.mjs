// Run offline: node --test src/components/ui/local-queue-view.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { build } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const require = createRequire(import.meta.url);
const bundle = await build({ entryPoints: [new URL('./local-queue-view.tsx', import.meta.url).pathname], bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic', external: ['react', 'react/jsx-runtime'], loader: { '.css': 'empty' } });
const mod = { exports: {} };
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(require, mod, mod.exports);
const render = props => renderToStaticMarkup(createElement(mod.exports.LocalQueueView, props));
const task = (status, fields = {}) => ({ id: `id-${status}`, description: `tarefa ${status}`, status, priority: 0, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T00:00:00Z', evidence: null, reason: null, ...fields });

test('estados públicos, evidência e motivo são apresentados literalmente, sem ações', () => {
  const tasks = ['pendente', 'em_andamento', 'concluida', 'falhou', 'bloqueada', 'cancelada'].map(s => task(s));
  tasks[2] = task('concluida', { evidence: '<script>alert(1)</script>\nProva' });
  tasks[3] = task('falhou', { reason: '<img src=x>' });
  const result = render({ mode: 'ready', tasks });
  for (const label of ['Pendente', 'Em andamento', 'Concluída', 'Falhou', 'Bloqueada', 'Cancelada']) assert.ok(result.includes(label));
  for (const text of ['Evidência:', 'Motivo:', 'Prioridade: 0', 'Criada:', 'Atualizada:', 'id-concluida']) assert.ok(result.includes(text));
  assert.match(result, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(result, /&lt;img src=x&gt;/);
  assert.doesNotMatch(result, /<script|<img|<button|<form|<progress|href=/);
});

test('não inventa estado, progresso, evidência ou contagem total', () => {
  const result = render({ mode: 'ready', tasks: [task('toString', { evidence: null, reason: null })] });
  assert.match(result, /Estado desconhecido/);
  assert.doesNotMatch(result, /Evidência:|Motivo:|Progresso/);
  assert.match(result, /pode estar paginada/);
  assert.match(render({ mode: 'ready', tasks: [] }), /Nenhuma tarefa recebida nesta página/);
});

test('indisponível, erro, carregando e resposta inválida não parecem fila vazia', () => {
  for (const props of [{mode: 'unavailable'}, {mode: 'error', reason: 'Falhou'}, {mode: 'loading'}, {mode: 'ready', tasks: null}, {mode: 'ready', tasks: [null]}, {mode: 'ready', tasks: [{ id: '1', status: 'pendente' }]}]) {
    const result = render(props);
    assert.doesNotMatch(result, /Nenhuma tarefa recebida|<button|<form/);
  }
  assert.match(render({mode: 'ready', tasks: null}), /Dados da fila inválidos/);
  assert.match(render({mode: 'error', reason: 'Falhou'}), /role="alert"/);
});
