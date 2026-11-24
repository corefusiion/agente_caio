import test from 'node:test';
import assert from 'node:assert/strict';
import { createWaitTracker, formatElapsed, waitNotice } from '../src/lib/request-wait.js';

function clock() {
  let time = 0, next = 0;
  const jobs = new Map();
  return {
    now: () => time,
    schedule: (fn, delay) => { const id = ++next; jobs.set(id, { fn, at: time + delay }); return id; },
    cancel: id => jobs.delete(id),
    advance(ms) {
      const end = time + ms;
      while (true) {
        const entry = [...jobs.entries()].sort((a, b) => a[1].at - b[1].at)[0];
        if (!entry || entry[1].at > end) break;
        jobs.delete(entry[0]); time = entry[1].at; entry[1].fn();
      }
      time = end;
    },
    get count() { return jobs.size; },
  };
}

test('adaptive waiting reports only elapsed local time, never imagined phases', () => {
  const c = clock(), seen = [];
  const stop = createWaitTracker(x => seen.push(x), c);
  assert.deepEqual(seen[0], {seconds: 0, notice: waitNotice(0)});
  c.advance(29999); assert.equal(seen.at(-1).seconds, 25);
  c.advance(1); assert.equal(seen.at(-1).seconds, 30);
  assert.match(seen.at(-1).notice, /demorando/);
  c.advance(30000); assert.equal(seen.at(-1).seconds, 60);
  assert.match(seen.at(-1).notice, /não confirma/);
  c.advance(30000); assert.equal(seen.at(-1).seconds, 90);
  assert.deepEqual(seen.map(x => x.seconds), [0,5,10,15,20,25,30,40,50,60,90]);
  assert.equal(formatElapsed(90), '1 min 30 s');
  assert.ok(seen.every(x => !/\d+%|pesquisando|conclu[ií]do|cancelado/i.test(x.notice)));
  stop(); assert.equal(c.count, 0);
});

test('completion, failure, unmount and session switch dispose old trackers', () => {
  for (const cause of ['success', 'failure', 'unmount', 'session switch']) {
    const c = clock(), old = [], fresh = [];
    const stopOld = createWaitTracker(x => old.push(x), c);
    c.advance(5000);
    stopOld(); stopOld();
    const oldCount = old.length;
    if (cause === 'session switch') createWaitTracker(x => fresh.push(x), c);
    c.advance(60000);
    assert.equal(old.length, oldCount, cause);
    if (cause === 'session switch') { assert.equal(fresh[0].seconds, 0); assert.equal(fresh.at(-1).seconds, 60); }
    else assert.equal(c.count, 0, cause);
  }
});

test('stop inside callback prevents a timer being scheduled', () => {
  const c = clock(); let stop;
  stop = createWaitTracker(({seconds}) => { if (seconds === 5) stop(); }, c);
  c.advance(5000); assert.equal(c.count, 0);
});
