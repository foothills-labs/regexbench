# Changelog

Notable changes to `regexbench`. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html), with the caveat
that a 0.x line makes no stability promise.

## Unreleased

### Added

- **Lookaround is decided, not refused.** `(?=…)`, `(?!…)`, fixed-width
  `(?<=…)` and `(?<!…)` now build into the automata instead of coming back
  `UNSUPPORTED`. The pattern is compiled with each assertion reduced to an
  edge on a private marker symbol, intersected with one constraint automaton
  per assertion — a pending-set machine for lookaheads (a suffix property,
  so each fired marker defers its check to the end) and a sliding-window
  machine for lookbehinds (a prefix property, certified on the spot) — and
  the markers are then projected away. An assertion nested at the start of
  another's body fires at the same position, so it chains its marker onto the
  outer's edge; nested past the start it fires somewhere else and is refused
  instead.

  The semantics are pinned to Python's `re` by a differential generator over
  the lookaround closure — assertions crossed with anchors, boundaries,
  nesting and quantifiers, under both semantics — and by the corpus tasks
  whose references lean on lookaround. Combined with backreferences a
  lookaround still leaves the regular languages, and now stays `UNDECIDABLE`
  for that reason rather than being confused with what this engine can answer.

  Four shapes are refused rather than answered, because the marker
  construction cannot represent them: a lookaround nested past the start of
  another's body, a `\b`/`\B` immediately in front of one (a marker fires
  before any character is consumed, and a boundary is only crossed while
  consuming one), a lookaround inside a dk.brics `&`/`~` operand (the context
  gate cannot carry the preceding text a lookbehind needs), and a
  variable-width lookbehind, which Python refuses too.

- **An anchored `^`/`$` behind zero-width atoms folds under SEARCH.** The
  anchor folding that handled the literal first and last characters of a
  pattern now also looks through a leading run of lookarounds, boundaries
  and empty groups: `(?!^0*$)(?!^0*\.0*$)^\d{1,5}(…)$` keeps its search
  semantics. An anchor that a consuming atom separates from the edge is still
  refused rather than mis-answered.

### Fixed

Found by auditing the feature above against `re` before it shipped. Each was
a wrong verdict rather than a refusal, and the differential generator that
now covers them fails on all twelve of its seeds without these.

- **A collapsed region kept its `\b` but dropped its lookaround.**
  `_epsilon_restrict` had no `Lookaround` case and fell through to the
  complement branch, deleting the assertion: `(?!a?)a` came back equivalent
  to `a`, when it matches nothing at all.

- **A lookaround counted as a consuming atom.** `_nullable` read the body's
  nullability rather than reporting the zero width of the assertion itself,
  so anchor resolution collapsed `(?=a)^a` and `a$(?!b)` to the empty
  language although Python matches `"a"` with both.

- **A constraint body assumed it started at the string start.** Both
  constraint machines enter the body mid-string — a lookahead at each firing,
  a lookbehind at each window start — but it was built once as "position
  zero, preceded by a non-word character", so `aa(?<=\ba)` and `aa(?<=^a)`
  came back equivalent to `aa`. The body is now built once per entry context
  and entered on the real one, the way `&`/`~` operands already were.

- **Folding both edge anchors away crashed.** `(^)($)` under SEARCH emptied
  the parts list, built a `Concat(())`, and raised `IndexError` out of the
  automata layer instead of returning a verdict.

### Changed

- **Benchmark coverage grows.** 669/762 = 87.8% of Re(gEx|DoS)Eval's SEARCH
  references parse now, up from 629/762 = 82.5% (FULLMATCH 740/762 = 97.1%).
  Three of the newly parseable references carry a ReDoS shape the structural
  pass can now see, so `vulnerable@1` on that corpus moves from 12.7% to
  13.1% — a property of the dataset that was previously invisible, not a
  change in what counts as vulnerable.
  The remaining lookaround refs are refused because they combine the assertion
  with a backreference, because a non-edge anchor makes the SEARCH reduction
  impossible, or for one of the four shapes listed above. `equivalent()`
  reports the new verdicts in its docstring.

## 0.3.0 — 2026-08-03

### Fixed

- **Anchors away from the pattern ends were dropped, not resolved.** `^` at the
  end and `$` at the start folded into the empty string, so `a^` came back
  equivalent to `a` when `re` matches nothing at all with it. Anchors are now
  resolved wherever they appear: `^` holds only where everything before it is
  empty, making `a^` the empty language, `a?^c` just `c`, and `(^a)*` exactly
  `a?`. Under `SEARCH` semantics an anchor off the ends is refused instead —
  the `.*p.*` rewrite cannot express it.

- **An anchor collapsing a region discarded the assertions in it.** `($)\b`
  resolved to the empty string and reported equivalent to the empty pattern;
  `re` matches nothing, because `\b` consumes no characters but still
  constrains the position. An exhaustive sweep of anchor and assertion
  combinations had 114 wrong verdicts here, every one of them a false
  `EQUIVALENT`.

- **Escape sequences were read as literal text.** `\x41` parsed as "x41"
  rather than "A", and `[؀-ۿ]` — the Arabic block — became a range of
  ASCII. Witnesses drawn from such patterns did not reproduce under
  `re.fullmatch`, which the README promises they always do. `\xHH`, `\uHHHH`,
  `\UHHHHHHHH`, `\N{NAME}`, `\a` and octal now decode, in character classes and
  as range endpoints too.

- **`{,n}` was read as literal text.** Python reads an omitted lower bound as
  zero, so `a{,3}` is `a{0,3}` and matches the empty string. The neighbouring
  `a{}` really is literal, so the two are told apart.

- **Patterns `re` rejects now get no verdict.** `a**`, `a{2}{3}`, `\b*`, `\q`,
  `\p{L}` and `[\d-z]` all built languages here while `re` refused to compile
  them — a verdict on a pattern that cannot run contradicts the `re` that
  `check()` and `screen()` execute.

- **`(?P=name)` is `UNDECIDABLE`, not `UNSUPPORTED`.** It is a backreference,
  and the documented taxonomy has said so all along.

- **ReDoS screening reads repeat *width*, not just unboundedness.** `(a+){2}`
  was reported exponential; it is not. `(a+){10}` is not safe either — it takes
  seconds on a few dozen characters — so both now report `POLYNOMIAL`, while a
  bounded repeat over an unambiguous body like `(ab){10}` stays `SAFE`.

  What makes a body expensive to split is that it matches more than one
  *width*, which unboundedness only approximates. `^([1-9][0-9]{0,7})+$` hangs
  on two dozen characters with no unbounded repeat anywhere in it, and a run of
  single optionals does the same — `^((\.)?([\w-]?)(\.)?)+$` gives the outer
  `+` combinatorially many ways to divide one string. Both are now caught. A
  lone `\d?` is not: `(\d?)*` varies by one optional character and CPython's
  empty-loop guard keeps it linear.

  Measured over the Re(gEx|DoS)Eval references, this moves 26 patterns off
  `EXPONENTIAL` to `SAFE` — none of which blow up when probed — and finds two
  that were called `SAFE` and do.

- **`positives` and `negatives` must be lists of strings.** `"abc"` was
  silently accepted and became `["a", "b", "c"]`.

- **Anchor resolution no longer expands without bound.** Deciding which part of
  a concatenation carries an anchor multiplies through nested concatenations,
  so Re(gEx|DoS)Eval's reference 2284 — fifteen alternations inside `^...$` —
  turned 98 nodes into 48 million and never finished. It stalled a whole
  `--use-reference` run, because the structural ReDoS pass parses in-process
  with no timeout. The traversal is now memoised on the node and the two
  nullability flags, and the expansion is capped and refused past a node
  budget, the way the automata layer already caps states. That pattern now
  answers in 0.2s, and the whole corpus parses in 1.7s.

### Added

- **Escape and misplaced-anchor atoms in the differential generator.** Its lack
  of them is why the anchor and escape families shipped at all: a generator
  that cannot emit a construct is not evidence about it.

- **`is_regular()` takes `semantics`.** The answer depends on it — an anchor
  away from the pattern ends resolves under FULLMATCH and is refused under
  SEARCH — so a coverage count taken with the default overstates what the
  engine will decide for a search corpus.

### Changed

- **Benchmark numbers re-measured against the published corpora.** The
  `vulnerable@1` row for Re(gEx|DoS)Eval moves from 14.2% to 12.7% (97 of 762)
  with the screening fixes above. Coverage of that corpus stays at 629/762
  under the search semantics it is scored with — the anchor work raises the
  FULLMATCH figure to 705/762, but Re(gEx|DoS)Eval is a search corpus, and the
  docs previously quoted the full-match number for it. The stale claim that
  only 51.1% of KB13 is analyzable is gone; all three dk.brics corpora parse in
  full, 20,824 patterns.

## 0.2.1 — 2026-08-02

### Fixed

- **Word boundaries on CPython 3.14.** 3.14 changed `\B` to match the empty
  string ([gh-124130](https://github.com/python/cpython/issues/124130)), making
  it exactly the negation of `\b`; 3.13 and earlier refuse it there, which made
  `\B` the empty language under a full match. 0.2.0 modelled only the older
  behaviour, so on 3.14 it disagreed with the `re` it was scoring against — 13
  of its own differential tests caught this, which is what they are for.

  The rule is now probed at import rather than compared against
  `sys.version_info`. The running interpreter is the authority, because
  `check()` executes patterns with that same `re`; a backported fix or a
  rebuilt interpreter would make a version test lie.

### Added

- **Python 3.14 is supported and tested.** The classifier and the CI matrix
  entry are back, and the suite passes on 3.10 through 3.14.

## 0.2.0 — 2026-08-02

First published release. 0.1.0 existed as a git tag's worth of code and was
never uploaded, so everything below is new to anyone installing this.

### Added

- **Dataset adapters** (`regexbench.datasets`) for the corpora this literature
  reports on: `load_regexeval` for Re(gEx|DoS)Eval, `load_deep_regex` for KB13
  and both NL-RX corpora, and `load_tasks` for your own problems. No dataset is
  redistributed; loaders take a path to files you download.
- **Batch scoring harness** (`regexbench.run`) producing `pass@k`, `dfa-eq@k`,
  `exact@k`, `vulnerable@k` and `usable@k` on the unbiased estimator from
  Chen and others (2021), plus `dfa-eq@k (decided)` over the analyzable subset.
- **`regexbench run`** on the command line, with `--use-reference`,
  `--workers`, `--limit`, `--k` and `--json`.
- **Match semantics** (`Semantics.FULLMATCH` / `SEARCH`) on `Task`, honoured by
  `check()` and by `equivalent()` through an anchor-aware reduction.
- **Syntax dialects** (`Dialect.PYTHON` / `BRICS`), adding intersection (`&`),
  complement (`~`), any-string (`@`) and the empty language (`#`).
- **Word boundaries** (`\b`, `\B`), which take KB13 and both NL-RX corpora from
  partial to complete analyzability.
- `match_many()` for scoring many strings against one pattern in one child
  process.
- `py.typed`: the package ships its type information.

### Changed

- **Shorthand classes are Unicode-aware, matching `re`.** `\d` is no longer
  equivalent to `[0-9]` — `re` matches every Unicode digit with `\d`, so they
  are different languages. Same for `\w` and `\s`. This is the change most
  likely to move an existing score.
- **`Report.usable` consults the reference.** A pattern proven DIFFERENT from
  the reference is never usable, even when it passes every example it was
  given. UNSUPPORTED and UNDECIDABLE are not held against a pattern.
- **Lookaround reports `UNSUPPORTED`, not `UNDECIDABLE`.** Lookaround alone
  preserves regularity; only combining it with backreferences leaves the
  regular languages. The old verdict claimed something about the problem that
  is true only of this engine.
- `equivalent()` short-circuits on identical patterns, matching the reference
  tooling in this field, which returns true on string equality before invoking
  its DFA checker.
- `Task` accepts a reference with no examples, which is what KB13 and NL-RX
  ship, and `regexbench check` shares its task-file parser with the loaders —
  so a task file may set `semantics` and `dialect`, and an unrecognised field
  is now an error.

### Fixed

- **Matching no longer crashes when called from a script.** `multiprocessing`'s
  spawn method re-imports the caller's `__main__`, so a two-line script that
  imported `safe_fullmatch` and called it died with a bootstrapping error. The
  child is now a standard-library-only subprocess.
- **A batch that hangs on every string is linear, not quadratic.** The timeout
  budget is per text, enforced as silence between streamed results.
- **The search reduction crosses newlines**, as `re.search` does. It was built
  from `.`, which excludes them.
- A child process that exits early raises instead of silently reporting the
  remaining texts as non-matching.
- ReDoS screening batches its probes: one child process per probe length rather
  than one per attack string.

## 0.1.0 — never published

Initial engine: DFA-based equivalence with shortest witnesses, two-pass ReDoS
screening, subprocess-isolated matching, and example-based scoring.
