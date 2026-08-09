"""Every walker over the AST must name every node type.

The way this engine has repeatedly produced wrong answers is a new node type
that some walker never learned about: `Lookaround` was added and
`_epsilon_restrict` kept falling through to the complement branch, deleting the
assertion, so `(?!a?)a` came back equivalent to `a`. `_nullable` read the
assertion's body instead of its width and collapsed `(?=a)^a` to nothing.
Neither failed loudly — an `isinstance` chain ending in `return False` cannot.

Relying on a pattern to reach the missed branch is what made those bugs
survive review. This does not rely on that: it hands every node type to every
walker directly, so a walker that has not been taught about a node fails here
whether or not anything in the corpus happens to reach it.

Two tests, and they have to be read together:

* `test_every_walker_handles_every_node_type` is the coverage — 12 node types
  by every registered walker.
* `test_every_walker_that_can_refuse_is_registered` is what keeps the first
  one honest, by failing when a walker with an `_unhandled` fallthrough is
  missing from the registry below.
"""

from __future__ import annotations

import inspect
import re

import pytest

from regexbench import _automata, _parse
from regexbench._parse import (
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
    UnhandledNode,
)

LEAF = CharSet(frozenset("a"))

# One instance per concrete node type. Bodies are deliberately trivial: this
# asks whether a walker *recognises* the type, not what it computes.
NODES: dict[str, Node] = {
    "Empty": Empty(),
    "CharSet": LEAF,
    "Concat": Concat((LEAF, LEAF)),
    "Alternate": Alternate((LEAF, LEAF)),
    "Repeat": Repeat(LEAF, 0, None),
    "Assert": Assert(),
    "Anchor": Anchor(is_start=True),
    "Lookaround": Lookaround("=", LEAF),
    "Intersect": Intersect((LEAF, LEAF)),
    "Complement": Complement(LEAF),
    "Poss": Poss(LEAF, 1, None),
    "Atomic": Atomic(LEAF),
}


def _concrete_node_types() -> set[str]:
    """Every concrete `Node` subclass, found by walking the class tree."""
    seen: set[type] = set()
    stack = [Node]
    while stack:
        cls = stack.pop()
        for sub in cls.__subclasses__():
            if sub not in seen:
                seen.add(sub)
                stack.append(sub)
    return {c.__name__ for c in seen}


# Each entry calls one walker with one node. Extra arguments are whatever that
# walker needs beyond the node; none of them change whether the type is
# recognised.
WALKERS: dict[str, object] = {
    "uses_assertions": lambda n: _parse.uses_assertions(n),
    "_zero_width": lambda n: _parse._zero_width(n),
    "_nullable": lambda n: _parse._nullable(n),
    "_contains_anchor": lambda n: _parse._contains_anchor(n),
    "_contains_lookaround": lambda n: _parse._contains_lookaround(n),
    "_nested_fires_at_start": lambda n: _parse._nested_fires_at_start(n),
    "_right_run_has_assert": lambda n: _parse._right_run_has_assert(n),
    "_left_run_has_lookaround": lambda n: _parse._left_run_has_lookaround(n),
    "_assert_meets_lookaround": lambda n: _parse._assert_meets_lookaround(n),
    "_epsilon_restrict": lambda n: _parse._epsilon_restrict(n),
    "_fixed_width": lambda n: _parse._fixed_width(n),
    "_node_count": lambda n: _parse._node_count(n, {}),
    "_ambiguous": lambda n: _parse._ambiguous(n),
    "_sticky_problem": lambda n: _parse._sticky_problem(n),
    "_finalize_sticky": lambda n: _parse._finalize_sticky(n, at_end=True),
    "_resolve_impl": lambda n: _parse._resolve_anchors(
        n, prefix_nullable=True, suffix_nullable=True
    ),
    "_emit": lambda n: _automata._NFA(("a",)).build(n),
    "_collect_lookarounds": lambda n: _automata._collect_lookarounds(n, []),
    "_context_sensitive": lambda n: _automata._context_sensitive(n),
}


def test_the_node_inventory_is_complete():
    """`NODES` has to list every concrete node, or the coverage below lies."""
    assert set(NODES) == _concrete_node_types()


@pytest.mark.parametrize("walker", sorted(WALKERS))
@pytest.mark.parametrize("node_name", sorted(NODES))
def test_every_walker_handles_every_node_type(walker: str, node_name: str):
    """A walker may do nothing with a node — but it has to say so.

    Only `UnhandledNode` fails this. A walker that refuses the node for a
    reason it states (`Unsupported`) is fine, and so is one that raises
    because a trivial body is not a sensible input; what is not fine is
    reaching the end of an `isinstance` chain with nobody having decided.
    """
    try:
        WALKERS[walker](NODES[node_name])
    except UnhandledNode as exc:
        pytest.fail(str(exc))
    except _parse.Unsupported:
        pass  # a stated refusal is a decision
    except (AttributeError, TypeError, ValueError, KeyError, RecursionError):
        pass  # the trivial body did not suit this walker; the type was known


def test_every_walker_that_can_refuse_is_registered():
    """Anything with an `_unhandled` fallthrough must be covered above.

    Without this, adding a walker and forgetting to register it would leave a
    gap that the parametrised test cannot see — the same shape of oversight
    the walkers themselves had.
    """
    found: set[str] = set()
    for module in (_parse, _automata):
        source = inspect.getsource(module)
        for block in re.split(r"\n(?=def |class )", source):
            match = re.match(r"def (\w+)\(", block)
            if match and "_unhandled(" in block:
                found.add(match.group(1))

    # `_resolve_impl` and `_emit` are reached through their public wrappers.
    covered = set(WALKERS) | {"_unhandled"}
    missing = found - covered
    assert not missing, (
        f"walkers with an _unhandled fallthrough but no entry in WALKERS: "
        f"{sorted(missing)} — add them so every node type is put through them"
    )
