import test from 'node:test';
import assert from 'node:assert/strict';
import { applyTraceEvent, MAX_TRACE_STEPS } from '../src/lib/trace-events.js';

const event = (step, content, extra = {}) => ({ type: 'thought', session_id: 'dashboard:chat-1', request_id: 'req-1', step, content, ...extra });
const apply = (steps, step, content, extra = {}) => applyTraceEvent(steps, event(step, content, extra), 'chat-1', 'req-1');

test('only exact non-empty dashboard session and recognized event types are accepted', () => {
  const steps = [];
  for (const data of [event('thought', '', { session_id: 'dashboard:chat-2' }), event('thought', '', { session_id: 'chat-1' }), event('thought', '', { type: 'run_complete' }), event('unknown', ''), null, {}, []]) {
    assert.equal(applyTraceEvent(steps, data, 'chat-1', 'req-1'), steps);
  }
  for (const id of ['', ' ', null, undefined, 1, 'x'.repeat(257)]) {
    assert.equal(applyTraceEvent(steps, event('thought', '', { session_id: `dashboard:${id}` }), id, 'req-1'), steps);
  }
  assert.deepEqual(applyTraceEvent(null, event('thought', ''), 'chat-1', 'req-1'), []);
});

test('real loop sequence shows observations only, with no argument/result leakage or success claim', () => {
  const sensitive = 'segredo_pessoal_123';
  let steps = apply([], 'thought', `Processando ${sensitive}`, { agent_id: sensitive });
  steps = apply(steps, 'tool_call', 'Solicitando ferramenta: web_search', { metadata: { args: { query: sensitive } } });
  assert.deepEqual(steps, [{ kind: 'thought', label: 'Processando interação' }, { kind: 'tool', name: 'Busca na web', returned: false }]);
  steps = apply(steps, 'tool_result', 'Ferramenta web_search retornou resultado.', { metadata: { result_preview: `error: ${sensitive}` } });
  assert.deepEqual(steps[2], { kind: 'tool', name: 'Busca na web', returned: true });
  assert.equal(steps[1].returned, false);
  assert.ok(!JSON.stringify(steps).includes(sensitive));
  assert.ok(!JSON.stringify(steps).match(/sucesso|conclu[ií]d/i));
});

test('unknown names are private and distinct unknown tools do not match each other', () => {
  let steps = apply([], 'tool_call', 'Solicitando ferramenta: mcp_nome_privado_1');
  steps = apply(steps, 'tool_result', 'Ferramenta mcp_nome_privado_2 retornou resultado.');
  assert.deepEqual(steps, [{ kind: 'tool', name: 'Outra ferramenta', returned: false }, { kind: 'tool', name: 'Outra ferramenta', returned: true }]);
  steps = apply(steps, 'tool_result', 'Ferramenta mcp_nome_privado_1 retornou resultado.');
  assert.equal(steps[0].returned, false);
  assert.equal(steps.at(-1).returned, true);
  assert.ok(!JSON.stringify(steps).includes('nome_privado'));
  steps = apply(steps, 'tool_call', 'Solicitando ferramenta: constructor');
  assert.equal(steps.at(-1).name, 'Outra ferramenta');
});

test('malformed names/content are ignored, including huge content and untrusted suffixes', () => {
  let steps = apply([], 'tool_call', 'Solicitando ferramenta: exec');
  for (const bad of [null, {}, '', 'Solicitando ferramenta: exec argumentos=secret', 'Solicitando ferramenta: <img>', `Solicitando ferramenta: ${'x'.repeat(101)}`, 'x'.repeat(10000)]) {
    assert.equal(apply(steps, 'tool_call', bad), steps);
  }
  assert.equal(apply(steps, 'tool_result', 'Ferramenta exec retornou resultado. secret'), steps);
});

test('repeated thought events group without invented stages, with bounded count', () => {
  let steps = [];
  for (let i = 0; i < 1010; i++) steps = apply(steps, 'thought', 'conteúdo secreto');
  assert.deepEqual(steps, [{ kind: 'thought', label: 'Processando interação', count: 999 }]);
  steps = apply(steps, 'tool_call', 'Solicitando ferramenta: web_search');
  steps = apply(steps, 'thought', 'outro');
  assert.equal(steps.length, 3);
});

test('retention cap and matched result after eviction do not invent a request', () => {
  let steps = apply([], 'tool_call', 'Solicitando ferramenta: read_pdf');
  for (let i = 0; i < 45; i++) steps = apply(steps, 'tool_call', 'Solicitando ferramenta: web_search');
  assert.equal(steps.length, MAX_TRACE_STEPS);
  steps = apply(steps, 'tool_result', 'Ferramenta read_pdf retornou resultado.');
  assert.equal(steps.length, MAX_TRACE_STEPS);
  assert.deepEqual(steps.at(-1), { kind: 'tool', name: 'Leitura de documento', returned: true });
});

test('session switch does not carry old steps or correlate across sessions', () => {
  const a = apply([], 'tool_call', 'Solicitando ferramenta: read_pdf');
  const b = applyTraceEvent(a, event('tool_result', 'Ferramenta read_pdf retornou resultado.', { session_id: 'dashboard:chat-2' }), 'chat-2', 'req-1');
  assert.deepEqual(b, [{ kind: 'tool', name: 'Leitura de documento', returned: true }]);
  assert.notEqual(b[0], a[0]);
  assert.equal(applyTraceEvent(b, event('thought', ''), 'chat-2', 'req-1'), b);
  const unowned = [{ kind: 'thought', label: 'segredo de outra sessão' }];
  const result = applyTraceEvent(unowned, event('thought', ''), 'chat-1', 'req-1');
  assert.deepEqual(result, [{ kind: 'thought', label: 'Processando interação' }]);
});

test('results are not paired with calls without a call ID', () => {
  let steps = apply([], 'tool_call', 'Solicitando ferramenta: exec');
  steps = apply(steps, 'tool_call', 'Solicitando ferramenta: exec');
  const previous = steps;
  steps = apply(steps, 'tool_result', 'Ferramenta exec retornou resultado.');
  assert.deepEqual(steps.map(s => s.returned), [false, false, true]);
  assert.deepEqual(previous.map(s => s.returned), [false, false]);
  steps = apply(steps, 'tool_result', 'Ferramenta exec retornou resultado.');
  assert.deepEqual(steps.map(s => s.returned), [false, false, true, true]);
});


test('rejects missing or wrong request correlation, including same-session events', () => {
  const steps = [];
  const e = event('thought', 'private');
  assert.equal(applyTraceEvent(steps, e, 'chat-1'), steps);
  assert.equal(applyTraceEvent(steps, e, 'chat-1', 'req-2'), steps);
  assert.equal(applyTraceEvent(steps, { ...e, request_id: undefined }, 'chat-1', 'req-1'), steps);
  let a = apply(steps, 'thought', 'private');
  const b = applyTraceEvent(a, { ...e, request_id: 'req-2' }, 'chat-1', 'req-2');
  assert.deepEqual(b, [{ kind: 'thought', label: 'Processando interação' }]);
  assert.notEqual(a, b);
});
