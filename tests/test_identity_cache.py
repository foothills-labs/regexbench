"""Caches keyed on `id(node)` have to hold the node.

Anchor resolution memoises on object identity because structural keys would
cost a tree walk per lookup on trees that resolution is already expanding.
Identity keys are only sound while the object exists: resolution builds nodes
and discards most of them, and CPython hands a freed address back to the next
allocation of the same size, so an entry whose key outlived its node is an
entry an unrelated node collides with and reads someone else's answer out of.

That is a wrong answer rather than a slow one, and it is invisible in isolation
— the address only gets reused once enough allocation has happened, so the
pattern that hit it in a real-world corpus disagreed with `re` only when
another pattern had been resolved first in the same process. Nothing in a
per-pattern test could have caught it, so these tests assert the invariant
directly instead: after the cache has seen a node, the node is still alive.
"""

from __future__ import annotations

import gc
import weakref

from regexbench._parse import (
    Alternate,
    Anchor,
    CharSet,
    Concat,
    _node_count,
    _resolve_cached,
    _ResolveState,
)


def _tree():
    """A throwaway tree — nothing outside the cache will reference it."""
    return Concat(
        (
            Anchor(is_start=True),
            Alternate((CharSet(frozenset("a")), CharSet(frozenset("b")))),
        )
    )


def test_the_size_memo_keeps_its_keys_alive():
    sizes: dict = {}
    node = _tree()
    ref = weakref.ref(node)
    assert _node_count(node, sizes) == 5

    del node
    gc.collect()
    assert ref() is not None, (
        "_node_count kept an id() key whose node has been freed; the next node "
        "allocated at that address reads this entry's count"
    )


def test_the_resolve_memo_keeps_its_keys_alive():
    state = _ResolveState()
    node = _tree()
    ref = weakref.ref(node)
    _resolve_cached(node, True, True, state)

    del node
    gc.collect()
    assert ref() is not None, (
        "_resolve_cached kept an id() key whose node has been freed; the next "
        "node allocated at that address resolves to this entry's answer"
    )


def test_a_reused_address_would_be_a_collision():
    """Guards the guards: the address really is handed straight back.

    If CPython did not recycle addresses this eagerly the two tests above
    would be pinning an invariant that nothing can violate. It does, so they
    are not.
    """
    addresses = set()
    for _ in range(20):
        node = CharSet(frozenset("a"))
        addresses.add(id(node))
        del node
    assert len(addresses) < 20, "no address was reused; the collision above cannot happen"
