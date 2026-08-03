# Changelog

Notable changes to `regexbench`. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html), with the caveat
that a 0.x line makes no stability promise.

## 0.2.1 — unreleased

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

- **Bounded repeats are screened as polynomial.** `(a+){2}` was reported
  exponential; it is not. `(a+){10}` is not safe either — it takes seconds on a
  few dozen characters — so both now report `POLYNOMIAL`, and a bounded repeat
  over an unambiguous body like `(ab){10}` stays `SAFE`.

- **`positives` and `negatives` must be lists of strings.** `"abc"` was
  silently accepted and became `["a", "b", "c"]`.

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

- **Escape and misplaced-anchor atoms in the differential generator.** Its lack
  of them is why the anchor and escape families shipped at all: a generator
  that cannot emit a construct is not evidence about it.

### Changed

- **RegexEval references the engine can parse: 629/762 to 707/762.** The anchor
  work closed the mid-pattern `^`/`$` gap, which was the largest single limit
  on coverage.

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
