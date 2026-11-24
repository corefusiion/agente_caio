import asyncio
from dataclasses import replace
from threading import Barrier, Lock, Thread
import unittest
from uuid import UUID

from caiocore.conversation_memory import (
    Identity, Message, Turn, State, InvalidConversation, StalePlan,
    BudgetExceeded, NoSavings, new_id, append, context, plan, apply, summarize,
)


def count(messages):
    # Deliberately an estimator, including simple per-message overhead.
    return sum(len(m.content) + 2 + sum(len(x) for x in m.tool_calls)
               + len(m.tool_call_id or '') for m in messages)


def turn(text, answer='ok'):
    return Turn((Message('user', text), Message('assistant', answer)))


def tool_turn():
    return Turn((Message('user', 'check tools'),
                 Message('assistant', '', ('call-a', 'call-b')),
                 Message('tool', 'B', tool_call_id='call-b'),
                 Message('tool', 'A', tool_call_id='call-a'),
                 Message('assistant', 'done')))


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.id = Identity(new_id(), new_id())
        self.state = State(self.id)
        for t in (turn('a' * 80), tool_turn(), turn('recent')):
            self.state = append(self.state, self.id, t)

    def planned(self, state=None, budget=55, keep=1):
        return plan(state or self.state, self.id, budget=budget,
                    keep_last_turns=keep, counter=count)

    def test_identity_and_isolation(self):
        with self.assertRaises(InvalidConversation):
            Identity(UUID(int=0), new_id())
        for other in (Identity(new_id(), self.id.conversation), Identity(self.id.user, new_id())):
            with self.assertRaises(InvalidConversation): context(self.state, other)
            with self.assertRaises(InvalidConversation): append(self.state, other, turn('x'))
            with self.assertRaises(InvalidConversation): plan(self.state, other, budget=1, keep_last_turns=1, counter=count)
            with self.assertRaises(InvalidConversation): apply(self.state, other, self.planned(), 's', counter=count)
        self.assertNotEqual(new_id(), new_id())

    def test_archive_tool_pairing_and_recent(self):
        p = self.planned()
        self.assertEqual(p.turns_to_summarize, self.state.archive[:2])
        r = apply(self.state, self.id, p, 'short', counter=count)
        self.assertEqual(r.state.archive, self.state.archive)
        self.assertEqual(context(r.state, self.id), (Message('summary', 'short'),) + self.state.archive[-1].messages)
        self.assertEqual(r.saved_tokens, count(context(self.state, self.id)) - count(context(r.state, self.id)))
        self.assertEqual(r.state.version, self.state.version + 1)
        self.assertEqual(self.state.summary, '')

    def test_keep_tool_turn_whole(self):
        s = append(self.state, self.id, tool_turn())
        p = self.planned(s, budget=70, keep=1)
        r = apply(s, self.id, p, 's', counter=count)
        self.assertEqual(context(r.state, self.id)[1:], tool_turn().messages)

    def test_invalid_and_incomplete_turns(self):
        bad = [
            (Message('user', 'x'), Message('assistant', '', ('a',))),
            (Message('user', 'x'), Message('tool', 'x', tool_call_id='a'), Message('assistant', 'end')),
            (Message('user', 'x'), Message('assistant', '', ('a', 'b')), Message('tool', 'x', tool_call_id='a'), Message('assistant', 'end')),
            (Message('user', 'x'), Message('assistant', '', ('a',)), Message('tool', 'x', tool_call_id='a'), Message('tool', 'x', tool_call_id='a'), Message('assistant', 'end')),
            (Message('user', 'x'), Message('assistant', '', ('a',)), Message('tool', 'x', tool_call_id='a'), Message('assistant', '', ('a',)), Message('tool', 'x', tool_call_id='a'), Message('assistant', 'end')),
            (Message('user', 'x'), Message('user', 'y'), Message('assistant', 'end')),
        ]
        for messages in bad:
            with self.subTest(messages=messages), self.assertRaises(InvalidConversation): Turn(messages)
        with self.assertRaises(InvalidConversation): Message('assistant', '', ('a', 'a'))
        with self.assertRaises(InvalidConversation): Message('tool', 'x')
        with self.assertRaises(InvalidConversation): Message('system', 'x')
        with self.assertRaises(InvalidConversation): State(self.id, summary=' ')
        with self.assertRaises(InvalidConversation): State(self.id, archive=(turn('x'),), summarized_turns=1, summary='')

    def test_stale_and_divergent_same_version(self):
        p = self.planned()
        changed = append(self.state, self.id, turn('new'))
        with self.assertRaises(StalePlan): apply(changed, self.id, p, 's', counter=count)
        # Equal versions from diverging branches must not make a plan valid.
        other = replace(self.state, archive=self.state.archive[:-1] + (turn('other'),))
        self.assertEqual(other.version, self.state.version)
        with self.assertRaises(StalePlan): apply(other, self.id, p, 's', counter=count)

    def test_failures_preserve_old_state_and_no_fake_savings(self):
        p = self.planned()
        before = self.state
        for bad_summary in ('', '  ', None):
            with self.assertRaises(InvalidConversation): apply(self.state, self.id, p, bad_summary, counter=count)
        with self.assertRaises(NoSavings): apply(self.state, self.id, p, 'x' * 300, counter=count)
        with self.assertRaises(BudgetExceeded): apply(self.state, self.id, p, 'x' * 60, counter=count)
        with self.assertRaises(InvalidConversation): apply(self.state, self.id, replace(p, boundary=1), 's', counter=count)
        self.assertEqual(self.state, before)
        with self.assertRaises(BudgetExceeded): self.planned(budget=1, keep=3)
        for invalid in (-1, True, 0):
            with self.assertRaises(InvalidConversation): self.planned(budget=invalid)
        for v in (-1, True, 1.5):
            with self.assertRaises(InvalidConversation): plan(self.state, self.id, budget=1, keep_last_turns=1, counter=lambda _: v)
        def fail(_): raise RuntimeError('counter failure')
        with self.assertRaises(RuntimeError): apply(self.state, self.id, p, 's', counter=fail)
        self.assertEqual(self.state, before)

    def test_async_and_sync_callbacks_and_failure(self):
        p = self.planned()
        calls = []
        def sync(old, turns):
            calls.append((old, turns))
            return 's'
        async def async_cb(old, turns):
            return sync(old, turns)
        self.assertEqual(asyncio.run(summarize(p, sync)), 's')
        self.assertEqual(asyncio.run(summarize(p, async_cb)), 's')
        self.assertEqual(calls[0], ('', self.state.archive[:2]))
        def fail(old, turns): raise RuntimeError('failed')
        with self.assertRaises(RuntimeError): asyncio.run(summarize(p, fail))
        with self.assertRaises(InvalidConversation): asyncio.run(summarize(p, lambda *_: '  '))
        self.assertEqual(self.state.summary, '')

    def test_repeated_summary_supplied_and_contradiction_not_discarded(self):
        s = State(self.id)
        for text in ('I like coffee ' + 'a'*60, 'I no longer like coffee ' + 'b'*60, 'recent'):
            s = append(s, self.id, turn(text))
        p = self.planned(s)
        observed = []
        async def cb(old, turns):
            observed.extend(m.content for t in turns for m in t.messages)
            return 'changed preference'
        r = apply(s, self.id, p, asyncio.run(summarize(p, cb)), counter=count)
        self.assertTrue(any('I like coffee' in x for x in observed))
        self.assertTrue(any('I no longer like coffee' in x for x in observed))
        self.assertEqual(r.state.archive, s.archive)
        s2 = append(r.state, self.id, turn('c'*90))
        p2 = self.planned(s2)
        self.assertEqual(p2.previous_summary, 'changed preference')
        self.assertEqual(p2.turns_to_summarize, (s.archive[-1],))

    def test_long_and_no_compaction_below_budget(self):
        s = State(self.id)
        for _ in range(500): s = append(s, self.id, turn('a'*80))
        self.assertIsNone(plan(s, self.id, budget=10**9, keep_last_turns=2, counter=count))
        p = self.planned(s, budget=200, keep=2)
        r = apply(s, self.id, p, 'brief', counter=count)
        self.assertEqual(len(r.state.archive), 500)
        self.assertEqual(len(p.turns_to_summarize), 498)
        self.assertEqual(context(r.state, self.id)[1:], tuple(m for t in s.archive[-2:] for m in t.messages))

    def test_concurrent_commit_requires_caller_lock_and_compare(self):
        p = self.planned()
        shared = [self.state]
        lock = Lock()
        barrier = Barrier(2)
        outcomes = []
        def worker(summary):
            barrier.wait()
            with lock:  # Caller must atomically read, compare, apply and publish.
                try:
                    shared[0] = apply(shared[0], self.id, p, summary, counter=count).state
                    outcomes.append('committed')
                except StalePlan:
                    outcomes.append('stale')
        threads = [Thread(target=worker, args=(s,)) for s in ('one', 'two')]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertCountEqual(outcomes, ['committed', 'stale'])
        self.assertEqual(shared[0].archive, self.state.archive)


if __name__ == '__main__': unittest.main()
