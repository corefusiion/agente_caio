// Run offline: node --test src/components/ui/task-queue.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { build } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const require = createRequire(import.meta.url);
const bundle = await build({ entryPoints: [new URL('./task-queue.tsx', import.meta.url).pathname], bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic', external: ['react', 'react/jsx-runtime'], loader: { '.css': 'empty' } });
const module = { exports: {} };
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(require, module, module.exports);
const { TaskQueue } = module.exports;
const html = props => renderToStaticMarkup(createElement(TaskQueue, props));
const task = (status, more = {}) => ({ id: status, title: status, status, ...more });
function nodes(element, predicate, result = []) {
  if (element == null || typeof element !== 'object') return result;
  if (Array.isArray(element)) { for (const child of element) nodes(child, predicate, result); return result; }
  if (typeof element.type === 'function') return nodes(element.type(element.props), predicate, result);
  if (predicate(element)) result.push(element);
  nodes(element.props?.children, predicate, result);
  return result;
}
const find = (props, type) => nodes(createElement(TaskQueue, props), node => node.type === type);

test('modos indisponível, carregando e erro nunca parecem fila vazia nem oferecem ações', () => {
  for (const [props, text] of [[{ mode: 'unavailable', reason: 'Integração não configurada' }, /Integração não configurada/], [{ mode: 'loading' }, /Carregando fila/], [{ mode: 'error', reason: 'Sem conexão' }, /Sem conexão/]]) {
    const rendered = html(props);
    assert.match(rendered, text);
    assert.doesNotMatch(rendered, /Nenhuma tarefa|Criar tarefa|<button/);
  }
  assert.match(html({ mode: 'error', reason: '' }), /motivo não informado/);
  assert.match(html({ mode: 'ready', tasks: [] }), /Nenhuma tarefa na fila/);
});

test('exibe os seis estados, prioridade, progresso real e erro sem interpretar HTML', () => {
  const tasks = ['pending', 'in_progress', 'blocked', 'completed', 'failed', 'cancelled'].map(status => task(status));
  tasks[1] = task('in_progress', { title: '<img src=x>', priority: 'high', progress: 37.5 });
  tasks[4] = task('failed', { error: '<script>erro</script>' });
  const rendered = html({ mode: 'ready', tasks });
  for (const status of ['Pendente', 'Em andamento', 'Bloqueada', 'Concluída', 'Falhou', 'Cancelada']) assert.match(rendered, new RegExp(status));
  assert.match(rendered, /Prioridade: Alta/);
  assert.match(rendered, /Progresso: 37\.5%/);
  assert.match(rendered, /<progress[^>]*value="37.5"/);
  assert.match(rendered, /&lt;img src=x&gt;/);
  assert.match(rendered, /&lt;script&gt;erro&lt;\/script&gt;/);
  assert.doesNotMatch(rendered, /<img|<script/);
});

test('não inventa progresso nem prioridade e lida com valores desconhecidos', () => {
  for (const progress of [undefined, null, NaN, Infinity, -1, 101, '50']) {
    const rendered = html({ mode: 'ready', tasks: [task('pending', { progress })] });
    assert.doesNotMatch(rendered, /<progress|Progresso:|Prioridade:/);
  }
  for (const progress of [0, 100]) assert.match(html({ mode: 'ready', tasks: [task('pending', { progress })] }), new RegExp(`Progresso: ${progress}%`));
  assert.match(html({ mode: 'ready', tasks: [task('toString', { priority: 'toString' })] }), /Estado desconhecido/);
});

test('criação controlada apenas encaminha valor e não altera tarefas', () => {
  const calls = [];
  const props = { mode: 'ready', tasks: [], create: { value: '  Comprar pão  ', onChange: value => calls.push(['change', value]), onCreate: value => calls.push(['create', value]) } };
  find(props, 'input')[0].props.onChange({ target: { value: 'Outra' } });
  let prevented = false;
  find(props, 'form')[0].props.onSubmit({ preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.deepEqual(calls, [['change', 'Outra'], ['create', 'Comprar pão']]);
  assert.match(html(props), /Nenhuma tarefa na fila/);
  for (const overrides of [{ value: '   ' }, { pending: true }]) {
    const blocked = { ...props, create: { ...props.create, ...overrides } };
    assert.equal(find(blocked, 'button')[0].props.disabled, true);
    find(blocked, 'form')[0].props.onSubmit({ preventDefault() {} });
  }
  assert.equal(calls.length, 2);
  assert.equal(find({ mode: 'ready', tasks: [] }, 'form').length, 0);
});

test('ações só aparecem quando autorizadas, com estado permitido e callback; pendentes ficam bloqueadas', () => {
  const calls = [];
  const props = { mode: 'ready', tasks: [
    task('pending', { allowedActions: ['cancel', 'retry'] }),
    task('in_progress', { allowedActions: ['cancel'] }),
    task('blocked', { allowedActions: ['cancel'], actionPending: true }),
    task('failed', { allowedActions: ['retry', 'cancel'] }),
    task('cancelled', { allowedActions: ['retry'] }),
    task('completed', { allowedActions: ['cancel', 'retry'] }),
    task('failed', { id: 'no-permission' }),
  ], onCancel: id => calls.push(['cancel', id]), onRetry: id => calls.push(['retry', id]) };
  const buttons = find(props, 'button');
  assert.equal(buttons.length, 5);
  for (const button of buttons) button.props.onClick();
  assert.deepEqual(calls, [['cancel', 'pending'], ['cancel', 'in_progress'], ['retry', 'failed'], ['retry', 'cancelled']]);
  assert.equal(buttons.find(button => button.props['aria-label'].includes('blocked')).props.disabled, true);
  assert.equal(find({ ...props, onCancel: undefined, onRetry: undefined }, 'button').length, 0);
});
