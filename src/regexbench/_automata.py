"""Compiling a parsed pattern to a DFA, and comparing two DFAs.

The alphabet is finite by construction: every character named in either
pattern, plus two sentinels standing for everything else — one for unnamed word
characters and one for unnamed non-word characters. Two patterns are equivalent
over that alphabet exactly when they are equivalent over all of Unicode,
because within each class the unnamed characters are indistinguishable to both.

Word boundaries are why the sentinel is split rather than single. `\\b` is
regular — it depends only on the two characters either side of a position — but
deciding it requires knowing whether each side is a word character, so the
alphabet has to preserve that distinction. Carrying it costs one extra symbol
and one bit of DFA state.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from ._parse import (
    OTHER_NONWORD,
    OTHER_WORD,
    Alternate,
    Assert,
    CharSet,
    Complement,
    Concat,
    Empty,
    Intersect,
    Node,
    Repeat,
    Unsupported,
    is_word_symbol,
    uses_assertions,
)

__all__ = ["build_dfa", "find_distinguishing_string", "DFA"]

_MAX_STATES = 20_000

# The entry contexts an embedded sub-machine may find itself in. "At the start
# of the string" implies the previous character is non-word, so there are three
# rather than four.
_CONTEXTS = ((False, True), (False, False), (True, False))


@dataclass(frozen=True)
class _Context:
    """Passable only when the surrounding machine is in this context.

    Complement and intersection determinize their operands, and a determinized
    operand has baked in an assumption about what precedes it — which is wrong
    the moment the operator is not at the start of the pattern. `x((\\bab)&(ab))`
    matches nothing, because between 'x' and 'a' there is no boundary, but a
    sub-machine built as though it began the string would think there was. So
    one copy is built per possible context and gated on the real one.
    """

    previous_is_word: bool
    at_start: bool


@dataclass(frozen=True)
class _Symbols:
    """Matches exactly these alphabet symbols.

    Used when splicing a finished DFA back into an NFA, where transitions are
    already keyed by symbol and must not be re-interpreted as character sets —
    a CharSet cannot tell the two sentinels apart, and here that matters.
    """

    symbols: frozenset[str]

    def accepts(self, symbol: str) -> bool:
        return symbol in self.symbols


class _NFA:
    """Thompson construction: epsilon transitions, one start, one accept."""

    def __init__(self, alphabet: tuple[str, ...]) -> None:
        # A label is None for an epsilon edge, an Assert for a zero-width
        # boundary, or something with .accepts() for a consuming edge.
        self.transitions: dict[int, list[tuple[object, int]]] = {}
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

    def link(self, src: int, dst: int, on: object = None) -> None:
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

        elif isinstance(node, Assert):
            self.link(start, accept, node)

        elif isinstance(node, Repeat):
            self._emit_repeat(node, start, accept)

        elif isinstance(node, (Complement, Intersect)):
            # An operand with no assertion in it cannot care what precedes it,
            # so one copy does — which is the common case, and three times the
            # determinization is not free.
            contexts = _CONTEXTS if uses_assertions(node) else ((False, True),)
            for previous_is_word, at_start in contexts:
                built = self._determinize(node, previous_is_word, at_start)
                entry = self.new_state()
                gate = _Context(previous_is_word, at_start) if len(contexts) > 1 else None
                self.link(start, entry, gate)
                self._embed(built, entry, accept)

        else:  # pragma: no cover - all node types are handled above
            raise Unsupported(f"cannot compile node {type(node).__name__}")

    def _determinize(self, node: Node, previous_is_word: bool, at_start: bool) -> DFA:
        """Build the sub-machine for one operator, for one entry context."""
        def sub(child: Node) -> DFA:
            return build_dfa(
                child,
                self.alphabet,
                previous_is_word=previous_is_word,
                at_start=at_start,
            )

        if isinstance(node, Complement):
            return _complement(sub(node.node))
        combined = sub(node.parts[0])
        for part in node.parts[1:]:
            combined = _intersect(combined, sub(part))
        return combined

    def _embed(self, dfa: DFA, start: int, accept: int) -> None:
        """Splice a finished DFA into this NFA as one fragment.

        Complement and intersection have to determinize their operands, but
        the result still has to compose with concatenation, repetition and the
        rest. A DFA is a special case of an NFA, so it can simply be copied in
        and its accepting states linked to the fragment's exit.
        """
        mirror = {state: self.new_state() for state in dfa.delta}
        self.link(start, mirror[dfa.start])
        for state, row in dfa.delta.items():
            for symbol, destination in row.items():
                self.link(mirror[state], mirror[destination], _Symbols(frozenset({symbol})))
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

    def closure(
        self,
        states: frozenset[int],
        boundary: bool | None = None,
        empty_subject: bool = False,
        context: tuple[bool, bool] | None = None,
    ) -> frozenset[int]:
        """Follow epsilon edges, and boundary edges when the context allows.

        `boundary` is None when the context is not yet known — after consuming
        a character, when the next one has not been read — and otherwise says
        whether a word boundary exists at this position. Assertions are held
        back rather than guessed at, and taken on the next transition once both
        sides are known.

        `empty_subject` marks the one position in a zero-length string, where
        Python refuses `\\B` even though no boundary exists there and `\\b`
        fails too. That is a quirk rather than a consequence — most engines
        match — but this package is scoring patterns that will be run by `re`,
        so it models `re`.
        """
        stack = list(states)
        seen = set(states)
        while stack:
            state = stack.pop()
            for on, dst in self.transitions[state]:
                if dst in seen:
                    continue
                if on is None:
                    passable = True
                elif isinstance(on, Assert):
                    passable = boundary is not None and boundary != on.negated
                    if on.negated and empty_subject:
                        passable = False
                elif isinstance(on, _Context):
                    passable = context is not None and context == (
                        on.previous_is_word,
                        on.at_start,
                    )
                else:
                    passable = False
                if passable:
                    seen.add(dst)
                    stack.append(dst)
        return frozenset(seen)

    def step(self, states: frozenset[int], symbol: str) -> frozenset[int]:
        out: set[int] = set()
        for state in states:
            for on, dst in self.transitions[state]:
                if on is not None and not isinstance(on, (Assert, _Context)) and on.accepts(symbol):
                    out.add(dst)
        return frozenset(out)


@dataclass
class DFA:
    alphabet: tuple[str, ...]
    delta: dict[int, dict[str, int]] = field(default_factory=dict)
    accepting: set[int] = field(default_factory=set)
    start: int = 0

    def accepts(self, text: str) -> bool:
        state = self.start
        for ch in text:
            row = self.delta[state]
            if ch in row:
                symbol = ch
            elif is_word_symbol(ch) and OTHER_WORD in row:
                symbol = OTHER_WORD
            else:
                symbol = OTHER_NONWORD
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


def build_dfa(
    node: Node,
    alphabet: tuple[str, ...],
    *,
    previous_is_word: bool = False,
    at_start: bool = True,
) -> DFA:
    """Subset-construct a complete DFA over `alphabet`.

    A state is a set of NFA states *plus* whether the character just consumed
    was a word character. That extra bit is what makes `\\b` decidable: a
    boundary exists between two positions exactly when their word-ness differs,
    so the machine only has to remember one side and read the other.
    """
    nfa = _NFA(alphabet)
    start, accept = nfa.build(node)

    # Without an assertion anywhere, the word-ness of the previous character is
    # not observable, so folding it into the state key would only double the
    # machine.
    tracking = uses_assertions(node)
    if not tracking:
        previous_is_word, at_start = False, False

    # Before the first character, the "previous character" is the start of the
    # string, which counts as a non-word position — so `\\bx` matches "xy".
    # The third component marks "nothing consumed yet", which is tracked rather
    # than inferred from state identity: a later position can reach the same
    # NFA states with the same word-ness, and it is not the empty string.
    initial = (nfa.closure(frozenset({start})), previous_is_word, at_start)
    index: dict[tuple[frozenset[int], bool, bool], int] = {initial: 0}
    dfa = DFA(alphabet=alphabet, start=0)
    queue: deque[tuple[frozenset[int], bool, bool]] = deque([initial])

    while queue:
        current = queue.popleft()
        states, previous_is_word, at_start = current  # noqa: PLW2901 - per-state context
        sid = index[current]
        dfa.delta[sid] = {}

        # At the end of the string the far side of the position is out of the
        # string, which is non-word: a boundary exists iff the last character
        # was a word character. Accepting here with nothing consumed is the
        # empty-string case, where `\\B` does not hold.
        if accept in nfa.closure(
            states,
            boundary=previous_is_word,
            empty_subject=at_start,
            context=(previous_is_word, at_start),
        ):
            dfa.accepting.add(sid)

        for symbol in alphabet:
            next_is_word = is_word_symbol(symbol) if tracking else False
            # The assertion sits between the previous character and this one,
            # so now both sides are known and it can be resolved. The subject
            # is non-empty on this path, so `\\B` is available again.
            reachable = nfa.closure(
                states,
                boundary=previous_is_word != next_is_word,
                context=(previous_is_word, at_start),
            )
            moved = nfa.closure(nfa.step(reachable, symbol))
            nxt = (moved, next_is_word, False)
            if nxt not in index:
                if len(index) > _MAX_STATES:
                    raise Unsupported("pattern expands to too many DFA states")
                index[nxt] = len(index)
                queue.append(nxt)
            dfa.delta[sid][symbol] = index[nxt]

    return dfa


def find_distinguishing_string(left: DFA, right: DFA, fillers: dict[str, str]) -> str | None:
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
                queue.append((nxt, word + fillers.get(symbol, symbol)))
    return None
