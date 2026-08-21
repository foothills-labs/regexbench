# Contributing

## Getting set up

```bash
git clone https://github.com/plicara/regexbench
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

**Compare membership, not only verdicts.** A verdict about a pair only goes
wrong when the two patterns go wrong in *different* ways, so a rule the engine
applies uniformly cancels out of the pairwise tests entirely — folding `$` as
plain end-of-string produced one bad verdict in 21,000 generated pairs, and a
disagreement on the first pattern ending in `$` once membership was compared
directly. `test_every_generated_pattern_accepts_what_re_accepts` is that check
over the generators; `crosscheck()` is the same thing as a public function.

## Real-world corpora

`tests/test_real_world.py` runs 355 patterns from `tests/data/` — regexes
extracted from PyPI packages, Stack Overflow posts and regexlib.com — through
`crosscheck` under both semantics. Six wrong-answer bugs came out of those
corpora, and the first ten patterns in the fixture are the ones that found
them.

The fixture is a sample. To sweep the real thing, download a corpus from the
[LinguaFranca artifact](https://github.com/VTLeeLab/LinguaFranca-FSE19) and:

```bash
regexbench crosscheck uniq-regexes-8.json --registry pypi
regexbench crosscheck uniq-regexes-8.json --registry pypi --search
```

Both semantics, always: two of the six only ever showed up under one of them.
Worth doing before any release, and after any change to the parser or the
automata layer. A refusal is not a failure — the command counts those
separately and only exits non-zero on a disagreement.

## Style

- Comments explain **why**, not what.
- Plain, specific prose. Numbers over adjectives.
- Test names are sentences that state the claim.
- `ruff check .` must pass; line length is 100.

## Before opening a pull request

```bash
pytest -q          # all tests, including the differential and corpus suites
ruff check .
python -m build && twine check dist/*
```

If you touched the parser or the automata, sweep a full corpus too — see
*Real-world corpora* above. The checked-in fixture is 355 patterns; the corpora
are half a million, and that is where the last six bugs were.

If your change moves a documented number, update the number.
