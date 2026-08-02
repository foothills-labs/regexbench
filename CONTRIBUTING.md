# Contributing

## Getting set up

```bash
git clone https://github.com/foothills-labs/regexbench
cd regexbench
pip install -e ".[dev]"
pytest -q
ruff check .
```

No runtime dependencies, and that is deliberate — `regexbench` is meant to drop
into a training or CI pipeline without dragging anything with it. `pytest` and
`ruff` are the only dev tools. Python 3.10 and up.

## The one rule that matters

**A wrong answer is worse than no answer.** This is a measuring instrument. If
it cannot decide something, it must say so — `UNSUPPORTED` when the engine has
not implemented it, `UNDECIDABLE` when nothing could. Both are correct results.
Guessing is not.

Most of the bugs found in this package so far were not crashes. They were
confident wrong answers: reading one regex dialect as another, scoring a
search-semantics corpus as full-match, treating `\d` as `[0-9]`. Nothing failed
loudly in any of those cases, which is exactly what made them expensive.

So, in order:

1. **Never silently reinterpret input.** If two readings of a pattern are both
   defensible, the caller states which one — that is why `Dialect` and
   `Semantics` are explicit rather than sniffed.
2. **Report limits rather than hiding them.** Loaders do not drop records the
   engine cannot handle; the metric reports them as undecided. A corpus quietly
   reduced to its easy half produces a number nobody can interpret.
3. **Claim only what is measured.** Numbers in documentation and commit
   messages come from a run, not an estimate.

## Changing the automata layer

`_parse.py` and `_automata.py` are private and delicate. Two things there are
load-bearing and easy to "simplify" into being wrong:

- **The alphabet sentinels.** Unnamed characters are split by class — digit,
  word, space, other — and that split is what makes a finite alphabet sound.
  Collapsing them breaks `\b` and every shorthand class.
- **The context bits in the DFA state.** Whether the previous character was a
  word character, and whether anything has been consumed yet, are what make
  boundary assertions decidable.

If you change either, the differential tests are the safety net, not the unit
tests.

## Differential tests

`tests/test_differential.py` generates random patterns and cross-checks every
verdict against Python's own `re`. An EQUIVALENT verdict has to survive a whole
corpus; a DIFFERENT verdict has to come with a witness that ground truth agrees
separates the two.

Every serious bug in the automata layer was caught here first, usually within
minutes of writing the check and before the code shipped. When you add a
feature to the engine, add the scenario — a generator, a corpus that can
actually distinguish what you added, and the ground truth. Six scenarios share
one `cross_check`; what you write is the ground truth, because that is the part
that differs.

For constructs `re` has no equivalent of, ground truth can usually still be
built. Intersection is computable per operand, and lookaround expresses it with
the surrounding context intact.

## Style

- Comments explain **why**, not what.
- Plain, specific prose. Numbers over adjectives.
- Test names are sentences that state the claim.
- `ruff check .` must pass; line length is 100.

## Before opening a pull request

```bash
pytest -q          # all tests, including the differential suite
ruff check .
python -m build && twine check dist/*
```

If your change moves a documented number, update the number.
