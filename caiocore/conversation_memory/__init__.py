"""Pure, opt-in conversation compaction; no persistence, transport, or authentication.

All objects are immutable. The trusted caller owns identity binding, storage, and
atomic compare-and-swap. See docs/MEMORIA_MOTOR_LOCAL.md before using.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional
from uuid import UUID, uuid4
import inspect


class MemoryErrorBase(ValueError):
    pass


class InvalidConversation(MemoryErrorBase):
    pass


class StalePlan(MemoryErrorBase):
    pass


class BudgetExceeded(MemoryErrorBase):
    pass


class NoSavings(MemoryErrorBase):
    pass


def _id(value: UUID) -> None:
    if not isinstance(value, UUID) or value.version != 4 or value.variant != 'specified in RFC 4122':
        raise InvalidConversation('identity must be a UUIDv4')


@dataclass(frozen=True)
class Identity:
    """IDs bound to an authenticated principal by a trusted caller, not clients."""
    user: UUID
    conversation: UUID

    def __post_init__(self) -> None:
        _id(self.user)
        _id(self.conversation)


def new_id() -> UUID:
    return uuid4()


@dataclass(frozen=True)
class Message:
    role: str
    content: str
    tool_calls: tuple[str, ...] = ()
    tool_call_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.role not in ('user', 'assistant', 'tool', 'summary') or not isinstance(self.content, str):
            raise InvalidConversation('invalid role or content')
        if not isinstance(self.tool_calls, tuple) or any(not isinstance(x, str) or not x.strip() for x in self.tool_calls):
            raise InvalidConversation('tool call IDs must be a tuple of nonempty strings')
        if len(set(self.tool_calls)) != len(self.tool_calls):
            raise InvalidConversation('duplicate tool call ID')
        if self.role != 'assistant' and self.tool_calls:
            raise InvalidConversation('only assistant can request tools')
        if self.role == 'tool':
            if not isinstance(self.tool_call_id, str) or not self.tool_call_id.strip():
                raise InvalidConversation('tool response requires an ID')
        elif self.tool_call_id is not None:
            raise InvalidConversation('tool response ID on non-tool message')


@dataclass(frozen=True)
class Turn:
    """One complete user turn; all tool requests and responses remain together."""
    messages: tuple[Message, ...]

    def __post_init__(self) -> None:
        m = self.messages
        if not isinstance(m, tuple) or len(m) < 2 or not all(isinstance(x, Message) for x in m):
            raise InvalidConversation('turn requires immutable messages')
        if m[0].role != 'user' or m[-1].role != 'assistant' or m[-1].tool_calls:
            raise InvalidConversation('turn must start with user and end with final assistant')
        pending: set[str] = set()
        seen: set[str] = set()
        for msg in m[1:]:
            if msg.role == 'assistant':
                if pending:
                    raise InvalidConversation('tool responses missing before assistant message')
                if seen.intersection(msg.tool_calls):
                    raise InvalidConversation('tool IDs must be unique within a turn')
                seen.update(msg.tool_calls)
                pending.update(msg.tool_calls)
            elif msg.role == 'tool':
                if msg.tool_call_id not in pending:
                    raise InvalidConversation('orphan or repeated tool response')
                pending.remove(msg.tool_call_id)
            else:
                raise InvalidConversation('user or summary inside turn')
        if pending:
            raise InvalidConversation('incomplete tool calls')


@dataclass(frozen=True)
class State:
    identity: Identity
    version: int = 0
    archive: tuple[Turn, ...] = ()
    summarized_turns: int = 0
    summary: str = ''

    def __post_init__(self) -> None:
        if not isinstance(self.identity, Identity):
            raise InvalidConversation('trusted identity required')
        if type(self.version) is not int or self.version < 0:
            raise InvalidConversation('invalid version')
        if not isinstance(self.archive, tuple) or not all(isinstance(t, Turn) for t in self.archive):
            raise InvalidConversation('archive must contain complete turns')
        if type(self.summarized_turns) is not int or not 0 <= self.summarized_turns <= len(self.archive):
            raise InvalidConversation('invalid compacted boundary')
        if (not isinstance(self.summary, str)
                or (self.summarized_turns == 0 and self.summary != '')
                or (self.summarized_turns > 0 and not self.summary.strip())):
            raise InvalidConversation('summary and boundary must agree')


def append(state: State, identity: Identity, turn: Turn) -> State:
    _access(state, identity)
    if not isinstance(turn, Turn):
        raise InvalidConversation('only complete turns accepted')
    return State(state.identity, state.version + 1, state.archive + (turn,), state.summarized_turns, state.summary)


def _access(state: State, identity: Identity) -> None:
    if not isinstance(state, State) or not isinstance(identity, Identity) or state.identity != identity:
        raise InvalidConversation('identity mismatch')


def context(state: State, identity: Identity) -> tuple[Message, ...]:
    _access(state, identity)
    prefix = (Message('summary', state.summary),) if state.summary else ()
    return prefix + tuple(m for turn in state.archive[state.summarized_turns:] for m in turn.messages)


Counter = Callable[[tuple[Message, ...]], int]


def _count(counter: Counter, messages: tuple[Message, ...]) -> int:
    n = counter(messages)
    if type(n) is not int or n < 0:
        raise InvalidConversation('counter must return a nonnegative integer')
    return n


@dataclass(frozen=True)
class Plan:
    snapshot: State
    boundary: int
    budget: int
    before_tokens: int
    keep_last_turns: int

    @property
    def previous_summary(self) -> str:
        return self.snapshot.summary

    @property
    def turns_to_summarize(self) -> tuple[Turn, ...]:
        return self.snapshot.archive[self.snapshot.summarized_turns:self.boundary]


def plan(state: State, identity: Identity, *, budget: int, keep_last_turns: int, counter: Counter) -> Optional[Plan]:
    _access(state, identity)
    if type(budget) is not int or budget <= 0 or type(keep_last_turns) is not int or keep_last_turns < 1:
        raise InvalidConversation('budget and retained turn count must be positive integers')
    before = _count(counter, context(state, identity))
    if before <= budget:
        return None
    boundary = len(state.archive) - keep_last_turns
    if boundary <= state.summarized_turns:
        raise BudgetExceeded('no eligible complete turns; recent turns do not fit')
    return Plan(state, boundary, budget, before, keep_last_turns)


@dataclass(frozen=True)
class Result:
    state: State
    before_tokens: int
    after_tokens: int

    @property
    def saved_tokens(self) -> int:
        return self.before_tokens - self.after_tokens


def apply(current: State, identity: Identity, candidate: Plan, summary: str, *, counter: Counter) -> Result:
    _access(current, identity)
    if not isinstance(candidate, Plan) or current != candidate.snapshot:
        raise StalePlan('state changed after plan was calculated')
    if (type(candidate.boundary) is not int or type(candidate.keep_last_turns) is not int
            or candidate.keep_last_turns < 1 or candidate.boundary != len(current.archive) - candidate.keep_last_turns
            or not current.summarized_turns < candidate.boundary < len(current.archive)
            or type(candidate.budget) is not int or candidate.budget <= 0):
        raise InvalidConversation('invalid plan')
    if not isinstance(summary, str) or not summary.strip():
        raise InvalidConversation('summary must not be empty')
    # Count the actual full contexts now, rather than trusting a caller-supplied plan count.
    before = _count(counter, context(current, identity))
    proposed = State(identity, current.version + 1, current.archive, candidate.boundary, summary)
    after = _count(counter, context(proposed, identity))
    if after >= before:
        raise NoSavings('summary does not reduce context tokens')
    if after > candidate.budget:
        raise BudgetExceeded('summary and retained turns exceed budget')
    return Result(proposed, before, after)


async def summarize(plan_: Plan, callback: Callable[[str, tuple[Turn, ...]], object]) -> str:
    """Invoke an explicitly injected synchronous or asynchronous summarizer.

    The callback may do I/O: only inject one trusted to handle the data. This
    module itself does no I/O. Caller must apply using the then-current state.
    """
    result = callback(plan_.previous_summary, plan_.turns_to_summarize)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, str) or not result.strip():
        raise InvalidConversation('summarizer must return a nonempty string')
    return result
