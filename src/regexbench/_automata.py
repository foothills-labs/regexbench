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
    NEGATED_BOUNDARY_MATCHES_EMPTY,
    UNNAMED_DIGIT,
    UNNAMED_OTHER,
    UNNAMED_SPACE,
    UNNAMED_WORD,
    Alternate,
    Anchor,
    Assert,
    Atomic,
    CharSet,
    Complement,
    Concat,
    Empty,
    Intersect,
    Lookaround,
    Node,
    Poss,
    Repeat,
    Unsupported,
    in_class,
    is_word_symbol,
    uses_assertions,
)

__all__ = ["build_dfa", "find_distinguishing_string", "DFA"]

_MAX_STATES = 20_000

# The entry contexts an embedded sub-machine may find itself in. "At the start
# of the string" implies the previous character is non-word, so there are three
# rather than four.
_CONTEXTS = ((False, True), (False, False), (True, False))


def _sentinels_for(ch: str) -> tuple[str, ...]:
    """Which sentinel an unnamed character falls under, most specific first."""
    if in_class(ch, "d"):
        return (UNNAMED_DIGIT, UNNAMED_WORD, UNNAMED_OTHER)
    if in_class(ch, "w"):
        return (UNNAMED_WORD, UNNAMED_OTHER)
    if in_class(ch, "s"):
        return (UNNAMED_SPACE, UNNAMED_OTHER)
    return (UNNAMED_OTHER,)


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


@dataclass(frozen=True)
class _Marker:
    """An edge that consumes exactly one symbol of the alphabet.

    A lookaround is compiled as an edge on a private marker symbol: the edge
    records where in the pattern the assertion fires, the marker stands for
    nothing the two patterns compare. `build_dfa` later quantifies the marker
    away, after a constraint automaton per assertion has certified that the
    position it fired at is legal. The same class carries the catch-all
    self-loops of the constraint automata, where any given symbol is consumed
    without disturbing the machine.
    """

    symbol: str

    def accepts(self, symbol: str) -> bool:
        return symbol == self.symbol


class _NFA:
    """Thompson construction: epsilon transitions, one start, one accept."""

    def __init__(self, alphabet: tuple[str, ...], marker_for: dict[int, str] | None = None) -> None:
        # A label is None for an epsilon edge, an Assert for a zero-width
        # boundary, an Anchor for a start/end position, or something with
        # .accepts() for a consuming edge (a CharSet, a _Symbols row, or a
        # _Marker symbol).
        self.transitions: dict[int, list[tuple[object, int]]] = {}
        self.count = 0
        # Needed because complement and intersection are not Thompson
        # constructions: they are computed on determinized sub-machines, which
        # requires knowing the alphabet.
        self.alphabet = alphabet
        # Marker symbol per lookaround node, keyed by identity: two equal
        # lookarounds in different places are different assertions and must
        # fire and be checked separately. Symbols are assigned up front by
        # build_dfa, which collects every lookaround reachable in the tree.
        self.marker_for = marker_for or {}
        self.marker_symbols = frozenset(self.marker_for.values())
        self.start: int = 0
        self.accepting: set[int] = set()

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
        self.accepting.add(accept)
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

        elif isinstance(node, (Assert, Anchor)):
            self.link(start, accept, node)

        elif isinstance(node, Lookaround):
            # The marker edge records where the assertion fires. Assertions
            # nested inside its body fire at that same position, so their
            # markers chain along here too — the inner one is invisible to the
            # marked machine otherwise, and its constraint would have nothing
            # to certify.
            chain = [id(node)]
            nested: list[Lookaround] = []
            _collect_lookarounds(node.body, nested)
            chain.extend(id(body) for body in nested)
            for node_id in chain:
                symbol = self.marker_for.get(node_id)
                if symbol is None:  # pragma: no cover - build_dfa assigns these up front
                    symbol = f"\x01mk{node_id}"
                    self.marker_for[node_id] = symbol
                self.link(start, accept, _Marker(symbol))

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

        Marker symbols are zero-width anywhere, so every spliced state also
        self-loops on them — a lookaround firing inside a compiled operand is
        no more an event than one firing between two characters.
        """
        markers = frozenset(self.marker_for.values())
        mirror = {state: self.new_state() for state in dfa.delta}
        self.link(start, mirror[dfa.start])
        for state, row in dfa.delta.items():
            for symbol, destination in row.items():
                self.link(mirror[state], mirror[destination], _Symbols(frozenset({symbol})))
            if markers:
                for symbol in markers:
                    self.link(mirror[state], mirror[state], _Marker(symbol))
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
        at_start: bool = False,
        at_end: bool = False,
    ) -> frozenset[int]:
        """Follow epsilon edges, and boundary edges when the context allows.

        `boundary` is None when the context is not yet known — after consuming
        a character, when the next one has not been read — and otherwise says
        whether a word boundary exists at this position. Assertions are held
        back rather than guessed at, and taken on the next transition once both
        sides are known.

        `empty_subject` marks the one position in a zero-length string, which
        is where the two boundary assertions stop being exact opposites — on
        interpreters that refuse `\\B` there. CPython 3.14 stopped refusing it
        (gh-124130), so which behaviour is right depends on the interpreter and
        is probed rather than assumed.

        Anchors are position properties, not boundary ones: `^` holds at this
        position exactly when `at_start`, `$` exactly when `at_end` — which is
        only true in the closure at the very end of the string, and no
        interpreter quirk attaches to either.
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
                    if on.negated and empty_subject and not NEGATED_BOUNDARY_MATCHES_EMPTY:
                        passable = False
                elif isinstance(on, Anchor):
                    passable = at_start if on.is_start else at_end
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
                if on is None or isinstance(on, (Assert, Anchor, _Context)):
                    continue
                if isinstance(on, _Marker):
                    if on.symbol == symbol:
                        out.add(dst)
                    continue
                # A character set must not swallow a marker: `.` matches any
                # *character*, and a marker is not one.
                if symbol not in self.marker_symbols and on.accepts(symbol):
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
            else:
                symbol = next(
                    (s for s in _sentinels_for(ch) if s in row), UNNAMED_OTHER
                )
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


def _collect_lookarounds(node: Node, out: list[Lookaround]) -> None:
    """Every distinct lookaround whose marker fires in this machine.

    Bodies are walked into — an assertion inside another assertion's body is
    still an assertion that must be checked. Complement and intersection
    operands are not: their sub-machines are determinized by a recursive
    build_dfa that resolves and projects its own markers.

    Anchor resolution can put the *same* node in several terms of an
    alternate, so the collection deduplicates by identity: one marker, one
    constraint, no matter how many resolutions the node survived.
    """
    seen: set[int] = set()

    def walk(node: Node) -> None:
        if isinstance(node, Lookaround):
            if id(node) not in seen:
                seen.add(id(node))
                out.append(node)
            walk(node.body)
        elif isinstance(node, Alternate):
            for option in node.options:
                walk(option)
        elif isinstance(node, (Concat, Intersect)):
            for part in node.parts:
                walk(part)
        elif isinstance(node, (Repeat, Complement, Poss, Atomic)):
            walk(node.node)

    walk(node)


def _subset_construct(
    nfa: _NFA,
    alphabet: tuple[str, ...],
    *,
    markers: frozenset[str],
    tracking: bool,
    previous_is_word: bool,
    at_start: bool,
    accepting_test=None,
) -> DFA:
    """Subset-construct a complete DFA over `alphabet`.

    A state is a set of NFA states *plus* whether the character just consumed
    was a word character. That extra bit is what makes `\\b` decidable: a
    boundary exists between two positions exactly when their word-ness differs,
    so the machine only has to remember one side and read the other.

    Marker symbols are zero-width: consuming one carries both the word-ness and
    the at-start bit over untouched, and no boundary is resolved across it —
    a `\\b` between two real characters is decided from those two characters
    alone, whichever markers sit between them.

    `accepting_test` decides which final state sets accept; it defaults to
    "the closure reached one of `nfa.accepting`", which is all the pattern
    machines need. The constraint automata of negative lookarounds need
    "reached no accepting state", which cannot be expressed as a set of NFA
    states, so they supply a predicate.
    """
    initial = (nfa.closure(frozenset({nfa.start})), previous_is_word, at_start)
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
        # empty-string case, where `\\B` does not hold. Anchors are resolved
        # here too: `$` holds exactly at the end, `^` at the start.
        reached = nfa.closure(
            states,
            boundary=previous_is_word,
            empty_subject=at_start,
            context=(previous_is_word, at_start),
            at_start=at_start,
            at_end=True,
        )
        if accepting_test is None:
            accept = not nfa.accepting.isdisjoint(reached)
        else:
            accept = accepting_test(reached)
        if accept:
            dfa.accepting.add(sid)

        for symbol in alphabet:
            if symbol in markers:
                # Zero-width: nothing consumed, nothing resolved. Assert edges
                # stay held for the next real character, and the word-ness and
                # at-start bits ride along.
                next_is_word, new_at_start = previous_is_word, at_start
                reachable = nfa.closure(
                    states,
                    context=(previous_is_word, at_start),
                    at_start=at_start,
                )
            else:
                next_is_word = is_word_symbol(symbol) if tracking else False
                new_at_start = False
                # The assertion sits between the previous character and this
                # one, so now both sides are known and it can be resolved. The
                # subject is non-empty on this path, so `\\B` is available
                # again.
                reachable = nfa.closure(
                    states,
                    boundary=previous_is_word != next_is_word,
                    context=(previous_is_word, at_start),
                    at_start=at_start,
                )
            moved = nfa.closure(nfa.step(reachable, symbol))
            nxt = (moved, next_is_word, new_at_start)
            if nxt not in index:
                if len(index) > _MAX_STATES:
                    raise Unsupported("pattern expands to too many DFA states")
                index[nxt] = len(index)
                queue.append(nxt)
            dfa.delta[sid][symbol] = index[nxt]

    return dfa


def _constraint_body(
    a: Node,
    symbol: str,
    alphabet: tuple[str, ...],
    marker_for: dict[int, str],
    markers: frozenset[str],
    absorb: bool,
) -> DFA:
    """The body compiled into the DFA a constraint automaton runs on.

    The body's own markers keep their edges — an assertion inside an assertion
    is as real as one in the pattern — while every other marker self-loops, a
    zero-width event that must not disturb the machine. Under `absorb` the
    body's accept keeps every symbol, which turns the machine into the
    prefix-closure `L(body)·Σ*` that a lookahead needs: the body only has to
    match a *prefix* of the remaining text.
    """
    nfa = _NFA(alphabet, marker_for=marker_for)
    before = nfa.count
    body_start, body_accept = nfa.build(a)
    nfa.accepting.add(body_accept)
    for state in range(before, nfa.count):
        if state == body_accept and absorb:
            for other in alphabet:
                nfa.link(body_accept, body_accept, _Marker(other))
        else:
            # Every other marker is a zero-width event that must not
            # disturb the machine — on the accept state too, where a
            # lookbehind observes the window it read so far.
            for other in markers - {symbol}:
                nfa.link(state, state, _Marker(other))
    return _subset_construct(
        nfa, alphabet, markers=markers, tracking=True, previous_is_word=False, at_start=True
    )


def _lookahead_constraint(pref: DFA, symbol: str, alphabet: tuple[str, ...], positive: bool) -> DFA:
    """A constraint automaton that checks a lookahead at every firing.

    A lookaround in a loop fires its marker at each iteration boundary, so the
    constraint has to be certified at every firing, not just the first. A
    lookahead is a suffix property, unsolvable on the way past, so each fired
    marker pushes a fresh check into a pending set, the text advances the set,
    and the end decides: every check satisfied (positive) or every one failed
    (negative). An annotation with no firing is vacuously fine.
    """
    start = frozenset()
    index: dict[frozenset[int], int] = {start: 0}
    dfa = DFA(alphabet=alphabet, start=0)
    queue: deque[frozenset[int]] = deque([start])

    while queue:
        pending = queue.popleft()
        sid = index[pending]
        dfa.delta[sid] = {}

        if positive:
            ok = pending <= pref.accepting
        else:
            ok = not pending.intersection(pref.accepting)
        if ok:
            dfa.accepting.add(sid)

        for other in alphabet:
            if other == symbol:
                nxt_pending = pending | {pref.start}
            else:
                nxt_pending = frozenset(pref.delta[state][other] for state in pending)
            if nxt_pending not in index:
                if len(index) > _MAX_STATES:
                    raise Unsupported("pattern expands to too many DFA states")
                index[nxt_pending] = len(index)
                queue.append(nxt_pending)
            dfa.delta[sid][other] = index[nxt_pending]

    return dfa


def _lookbehind_constraint(
    window: DFA, symbol: str, alphabet: tuple[str, ...], positive: bool
) -> DFA:
    """A constraint automaton that checks a lookbehind at every firing.

    A lookbehind is a prefix property, decidable while reading: the set of
    states that the suffixes of the read prefix end in is carried along — the
    restart term keeps a window starting at each character — and each fired
    marker is certified on the spot by the window it sees. One failed firing
    poisons the annotation; none at all leaves it fine.
    """
    start = (frozenset({window.start}), False)
    index: dict[tuple[frozenset[int], bool], int] = {start: 0}
    dfa = DFA(alphabet=alphabet, start=0)
    queue: deque[tuple[frozenset[int], bool]] = deque([start])

    while queue:
        states, poisoned = queue.popleft()
        sid = index[(states, poisoned)]
        dfa.delta[sid] = {}
        if not poisoned:
            dfa.accepting.add(sid)

        for other in alphabet:
            if other == symbol:
                complete = not states.isdisjoint(window.accepting)
                poison = complete if not positive else not complete
                nxt = (states, poisoned or poison)
            else:
                advanced = frozenset(window.delta[state][other] for state in states)
                nxt = (advanced | {window.start}, poisoned)
            if nxt not in index:
                if len(index) > _MAX_STATES:
                    raise Unsupported("pattern expands to too many DFA states")
                index[nxt] = len(index)
                queue.append(nxt)
            dfa.delta[sid][other] = index[nxt]

    return dfa


def _project(dfa: DFA, real_symbols: tuple[str, ...], markers: frozenset[str]) -> DFA:
    """Existentially eliminate the markers from a DFA over the annotated alphabet.

    A pattern matches some text iff some annotation of it does, so the markers
    are quantified away with a subset construction in which each marker
    transition acts as an epsilon edge: the resulting DFA over the real
    alphabet accepts exactly the text that has one accepted annotation.
    """
    def closure(states: frozenset[int]) -> frozenset[int]:
        stack = list(states)
        seen = set(states)
        while stack:
            state = stack.pop()
            for marker in markers:
                dst = dfa.delta[state][marker]
                if dst not in seen:
                    seen.add(dst)
                    stack.append(dst)
        return frozenset(seen)

    start = closure(frozenset({dfa.start}))
    index = {start: 0}
    projected = DFA(alphabet=real_symbols, start=0)
    queue: deque[frozenset[int]] = deque([start])

    while queue:
        states = queue.popleft()
        sid = index[states]
        projected.delta[sid] = {}
        if states.intersection(dfa.accepting):
            projected.accepting.add(sid)
        for symbol in real_symbols:
            nxt = closure(frozenset(dfa.delta[state][symbol] for state in states))
            if nxt not in index:
                if len(index) > _MAX_STATES:
                    raise Unsupported("pattern expands to too many DFA states")
                index[nxt] = len(index)
                queue.append(nxt)
            projected.delta[sid][symbol] = index[nxt]

    return projected


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

    With lookarounds present the construction is three stages: the pattern is
    compiled with each assertion reduced to an edge on a private marker symbol,
    that annotated machine is intersected with one constraint automaton per
    assertion, and the markers are then projected away. The result is a DFA
    over the original alphabet whose language is the pattern's.
    """
    lookarounds: list[Lookaround] = []
    _collect_lookarounds(node, lookarounds)

    # Without an assertion anywhere, the word-ness of the previous character is
    # not observable, so folding it into the state key would only double the
    # machine.
    tracking = uses_assertions(node)
    if not tracking:
        previous_is_word, at_start = False, False

    if not lookarounds:
        nfa = _NFA(alphabet)
        nfa.build(node)
        return _subset_construct(
            nfa,
            alphabet,
            markers=frozenset(),
            tracking=tracking,
            previous_is_word=previous_is_word,
            at_start=at_start,
        )

    marker_symbols = [f"\x01mk{id(lookaround)}" for lookaround in lookarounds]
    markers = frozenset(marker_symbols)
    marker_for = {
        id(lookaround): symbol
        for lookaround, symbol in zip(lookarounds, marker_symbols, strict=True)
    }
    full_alphabet = alphabet + tuple(marker_symbols)
    nfa = _NFA(full_alphabet, marker_for=marker_for)
    nfa.build(node)
    combined = _subset_construct(
        nfa,
        full_alphabet,
        markers=markers,
        tracking=tracking,
        previous_is_word=previous_is_word,
        at_start=at_start,
    )
    for lookaround, symbol in zip(lookarounds, marker_symbols, strict=True):
        if lookaround.lookahead:
            pref = _constraint_body(
                lookaround.body, symbol, full_alphabet, marker_for, markers, absorb=True
            )
            constraint = _lookahead_constraint(pref, symbol, full_alphabet, lookaround.positive)
        else:
            window = _constraint_body(
                lookaround.body, symbol, full_alphabet, marker_for, markers, absorb=False
            )
            constraint = _lookbehind_constraint(window, symbol, full_alphabet, lookaround.positive)
        combined = _intersect(combined, constraint)
    return _project(combined, alphabet, markers)


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
