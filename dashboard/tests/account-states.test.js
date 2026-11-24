import test from 'node:test';
import assert from 'node:assert/strict';
import { api } from '../src/services/api.js';

// No sockets: each test installs a deterministic fetch stub.
function mockResponses(responses) {
  globalThis.fetch = async url => {
    const value = responses[url];
    if (value instanceof Error) throw value;
    if (!value) throw new Error(`Unexpected request: ${url}`);
    return { ok: value.ok !== false, status: value.status ?? 500, json: async () => value.data };
  };
}
const success = data => ({ data });
const failure = { ok: false, data: { message: 'indisponível' } };
const get = (agents, id) => agents.find(a => a.id === id);

function setup(inbox, schedule, agents = []) {
  mockResponses({ '/api/agents': success(agents), '/api/agent/email/inbox': inbox, '/api/agent/schedule/data': schedule });
}

test('request failures do not expose fictional email or active account states', async () => {
  setup(failure, new Error('network unavailable'), [{ id: 'spec-email', status: 'online' }, { id: 'spec-schedule', status: 'online' }]);
  const agents = await api.getAgents();
  const email = get(agents, 'spec-email'), schedule = get(agents, 'spec-schedule');
  assert.equal(email.status, 'offline');
  assert.equal(schedule.status, 'offline');
  assert.match(email.statusDetail, /indisponíve/i);
  assert.match(schedule.statusDetail, /indisponíve/i);
  assert.deepEqual(email.monitorData.inbox, []);
  assert.deepEqual(schedule.monitorData, { crons: [], upcomingEvents: [] });
  assert.equal(email.metrics.emailsProcessed, null);
  assert.equal(email.metrics.unread, null);
  assert.equal(email.metrics.urgentAlerts, null);
  assert.equal(schedule.metrics.eventsToday, null);
  assert.equal(schedule.metrics.activeCrons, null);
  assert.deepEqual(await api.getEmailInbox(), []); // existing UI contract
});

test('valid empty and non-empty payloads preserve shape without claiming connections', async () => {
  setup(success({ inbox: [{ from: 'real@example.com', read: false, priority: 'urgent' }] }), success({ crons: [], upcomingEvents: [{ title: 'Consulta' }], metrics: { eventsToday: 1, activeCrons: 0 } }));
  let agents = await api.getAgents();
  let email = get(agents, 'spec-email'), schedule = get(agents, 'spec-schedule');
  assert.equal(email.status, 'offline');
  assert.match(email.statusDetail, /não verificada/i);
  assert.equal(email.metrics.unread, 1);
  assert.equal(email.metrics.urgentAlerts, 1);
  assert.equal(email.monitorData.inbox[0].from, 'real@example.com');
  assert.equal(schedule.status, 'offline');
  assert.match(schedule.statusDetail, /não verificada/i);
  assert.equal(schedule.monitorData.upcomingEvents[0].title, 'Consulta');
  assert.equal(schedule.metrics.eventsToday, 1);
  setup(success({ inbox: [] }), success({ crons: [], upcomingEvents: [], metrics: {} }));
  agents = await api.getAgents();
  email = get(agents, 'spec-email');
  assert.deepEqual(email.monitorData.inbox, []);
  assert.equal(email.metrics.unread, 0);
  assert.equal(email.status, 'offline');
});

test('malformed and explicit error payloads fail closed', async () => {
  setup(success({ status: 'error', inbox: [] }), success({ status: 'error', crons: [], upcomingEvents: [] }));
  let agents = await api.getAgents();
  assert.match(get(agents, 'spec-email').statusDetail, /indisponíve/i);
  assert.match(get(agents, 'spec-schedule').statusDetail, /indisponíve/i);
  setup(success({ inbox: 'invalid' }), success({ crons: {}, upcomingEvents: [] }));
  agents = await api.getAgents();
  assert.match(get(agents, 'spec-email').statusDetail, /indisponíve/i);
  assert.match(get(agents, 'spec-schedule').statusDetail, /indisponíve/i);
  assert.deepEqual(await api.getEmailInbox(), []);
  setup(success({ inbox: [null] }), success({ crons: [], upcomingEvents: [] }));
  agents = await api.getAgents();
  assert.match(get(agents, 'spec-email').statusDetail, /indisponíve/i);
});

test('email service never presents guessed connection status or timing', async () => {
  mockResponses({ '/api/status': failure });
  let email = (await api.getServices()).find(s => s.id === 'email');
  assert.equal(email.status, 'offline');
  assert.equal(email.response, undefined);
  assert.equal(email.uptime, undefined);
  mockResponses({ '/api/status': success({ services: [{ id: 'email', name: 'Email', status: 'online', uptime: '1h', response: '~4s' }] }) });
  email = (await api.getServices()).find(s => s.id === 'email');
  assert.equal(email.status, 'offline');
  assert.equal(email.response, undefined);
  assert.equal(email.uptime, undefined);
});
