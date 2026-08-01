"""Compiling a parsed pattern to a DFA, and comparing two DFAs.

The alphabet is finite by construction: every character named in either
pattern, plus the ``OTHER`` sentinel standing for everything else. Two
patterns are equivalent over that alphabet exactly when they are equivalent
over all of Unicode, because unnamed characters are indistinguishable to both.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from ._parse import (
    OTHER,
    Alternate,
    CharSet,
    Complement,
    Concat,
    Empty,
    Intersect,
    Node,
    Repeat,
    Unsupported,
)

__all__ = ["build_dfa", "find_distinguishing_string", "DFA"]

_MAX_STATES = 20_000


class _NFA:
    """Thompson construction: epsilon transitions, one start, one accept."""

    def __init__(self, alphabet: tuple[str, ...]) -> None:
        self.transitions: dict[int, list[tuple[CharSet | None, int]]] = {}
        self.count = 0
        # Needed because complement and intersection are not Thompson
        # constructions: they are computed on determinized sub-machines, which
        # requires knowing the alphabet.
        self.alphabet = alphabet

    def new_state(self) -> int:
        state = self.count
        self.count += 1
        self.transitions[state] = []
        if self.count > _MAX_STATES:
            raise Unsupported("pattern expands to too many states to analyze")
        return state

    def link(self, src: int, dst: int, on: CharSet | None = None) -> None:
        self.transitions[src].append((on, dst))

    def build(self, node: Node) -> tuple[int, int]:
        start = self.new_state()
        accept = self.new_state()
        self._emit(node, start, accept)
        return start, accept

    def _emit(self, node: Node, start: int, accept: int) -> None:
        if isinstance(node, Empty):
            self.link(start, accept)

        elif isinstance(node, CharSet):
            self.link(start, accept, node)

        elif isinstance(node, Concat):
            current = start
            for part in node.parts[:-1]:
                nxt = self.new_state()
                self._emit(part, current, nxt)
                current = nxt
            self._emit(node.parts[-1], current, accept)

        elif isinstance(node, Alternate):
            for option in node.options:
                s, a = self.new_state(), self.new_state()
                self.link(start, s)
                self._emit(option, s, a)
                self.link(a, accept)

        elif isinstance(node, Repeat):
            self._emit_repeat(node, start, accept)

        elif isinstance(node, Complement):
            inner = build_dfa(node.node, self.alphabet)
            self._embed(_complement(inner), start, accept)

        elif isinstance(node, Intersect):
            combined = build_dfa(node.parts[0], self.alphabet)
            for part in node.parts[1:]:
                combined = _intersect(combined, build_dfa(part, self.alphabet))
            self._embed(combined, start, accept)

        else:  # pragma: no cover - all node types are handled above
            raise Unsupported(f"cannot compile node {type(node).__name__}")

    def _embed(self, dfa: DFA, start: int, accept: int) -> None:
        """Splice a finished DFA into this NFA as one fragment.

        Complement and intersection have to determinize their operands, but
        the result still has to compose with concatenation, repetition and the
        rest. A DFA is a special case of an NFA, so it can simply be copied in
        and its accepting states linked to the fragment's exit.
        """
        named = frozenset(symbol for symbol in dfa.alphabet if symbol != OTHER)
        mirror = {state: self.new_state() for state in dfa.delta}
        self.link(start, mirror[dfa.start])
        for state, row in dfa.delta.items():
            for symbol, destination in row.items():
                # A set matching only OTHER is the negation of every named
                # character, which is exactly what OTHER stands for.
                on = CharSet(named, negated=True) if symbol == OTHER else CharSet(frozenset(symbol))
                self.link(mirror[state], mirror[destination], on)
        for state in dfa.accepting:
            self.link(mirror[state], accept)

    def _emit_repeat(self, node: Repeat, start: int, accept: int) -> None:
        low, high = node.minimum, node.maximum

        if high is not None and high > 1000:
            raise Unsupported("repetition bound is too large to analyze")

        current = start
        for _ in range(low):
            nxt = self.new_state()
            self._emit(node.node, current, nxt)
            current = nxt

        if high is None:
            loop = self.new_state()
            self.link(current, loop)
            body_start = self.new_state()
            self.link(loop, body_start)
            body_end = self.new_state()
            self._emit(node.node, body_start, body_end)
            self.link(body_end, loop)
            self.link(loop, accept)
        else:
            optional = high - low
            self.link(current, accept)
            for _ in range(optional):
                nxt = self.new_state()
                self._emit(node.node, current, nxt)
                self.link(nxt, accept)
                current = nxt

    def epsilon_closure(self, states: frozenset[int]) -> frozenset[int]:
        stack = list(states)
        seen = set(states)
        while stack:
            state = stack.pop()
            for on, dst in self.transitions[state]:
                if on is None and dst not in seen:
                    seen.add(dst)
                    stack.append(dst)
        return frozenset(seen)

    def step(self, states: frozenset[int], symbol: str) -> frozenset[int]:
        out: set[int] = set()
        for state in states:
            for on, dst in self.transitions[state]:
                if on is not None and on.accepts(symbol):
                    out.add(dst)
        return self.epsilon_closure(frozenset(out))


@dataclass
class DFA:
    alphabet: tuple[str, ...]
    delta: dict[int, dict[str, int]] = field(default_factory=dict)
    accepting: set[int] = field(default_factory=set)
    start: int = 0

    def accepts(self, text: str) -> bool:
        state = self.start
        for ch in text:
            symbol = ch if ch in self.delta[state] else OTHER
            state = self.delta[state][symbol]
        return state in self.accepting


def _complement(dfa: DFA) -> DFA:
    """Accept exactly what `dfa` rejects.

    Sound only because the DFA is complete — every state has a transition on
    every symbol, so rejection is always an explicit state rather than a
    missing edge.
    """
    return DFA(
        alphabet=dfa.alphabet,
        delta=dfa.delta,
        accepting=set(dfa.delta) - dfa.accepting,
        start=dfa.start,
    )


def _intersect(left: DFA, right: DFA) -> DFA:
    """Product construction: accept where both machines accept."""
    alphabet = left.alphabet
    start = (left.start, right.start)
    index: dict[tuple[int, int], int] = {start: 0}
    product = DFA(alphabet=alphabet, start=0)
    queue: deque[tuple[int, int]] = deque([start])

    while queue:
        current = queue.popleft()
        sid = index[current]
        product.delta[sid] = {}
        if current[0] in left.accepting and current[1] in right.accepting:
            product.accepting.add(sid)
        for symbol in alphabet:
            nxt = (left.delta[current[0]][symbol], right.delta[current[1]][symbol])
            if nxt not in index:
                if len(index) > _MAX_STATES:
                    raise Unsupported("intersection expands to too many DFA states")
                index[nxt] = len(index)
                queue.append(nxt)
            product.delta[sid][symbol] = index[nxt]

    return product


def build_dfa(node: Node, alphabet: tuple[str, ...]) -> DFA:
    """Subset-construct a complete DFA over `alphabet`."""
    nfa = _NFA(alphabet)
    start, accept = nfa.build(node)

    initial = nfa.epsilon_closure(frozenset({start}))
    index: dict[frozenset[int], int] = {initial: 0}
    dfa = DFA(alphabet=alphabet, start=0)
    queue: deque[frozenset[int]] = deque([initial])

    while queue:
        current = queue.popleft()
        sid = index[current]
        dfa.delta[sid] = {}
        if accept in current:
            dfa.accepting.add(sid)

        for symbol in alphabet:
            nxt = nfa.step(current, symbol)
            if nxt not in index:
                if len(index) > _MAX_STATES:
                    raise Unsupported("pattern expands to too many DFA states")
                index[nxt] = len(index)
                queue.append(nxt)
            dfa.delta[sid][symbol] = index[nxt]

    return dfa


def find_distinguishing_string(left: DFA, right: DFA, other_char: str) -> str | None:
    """Return a shortest string accepted by exactly one DFA, or None.

    Breadth-first over the product automaton, so the witness is a shortest
    counterexample — which is what makes it readable in a failure report.

    `other_char` is a concrete character standing in for the OTHER symbol. It
    must not appear in either pattern, so the returned witness is a real
    string that genuinely reproduces the difference.
    """
    alphabet = left.alphabet
    start = (left.start, right.start)
    seen = {start}
    queue: deque[tuple[tuple[int, int], str]] = deque([(start, "")])

    while queue:
        (ls, rs), word = queue.popleft()
        if (ls in left.accepting) != (rs in right.accepting):
            return word
        for symbol in alphabet:
            nxt = (left.delta[ls][symbol], right.delta[rs][symbol])
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, word + (other_char if symbol == OTHER else symbol)))
    return None
