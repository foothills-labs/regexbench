# Changelog

Notable changes to `regexbench`. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html), with the caveat
that a 0.x line makes no stability promise.

## 0.4.1 — 2026-08-20

### Fixed

- **`pass_at_k` scored any task with fewer than `k` samples as a full
  pass** ([#8](https://github.com/plicara/regexbench/issues/8)). The
  `n - c < k` shortcut — "so many samples succeeded that any k must include
  one" — is sound only for `n >= k`; below it, it fired unconditionally, so
  a task that lost samples to a refusal or a budget cap scored 1.0 on every
  metric whether or not anything succeeded (`pass_at_k(1, 0, 3) == 1.0`).
  Measured on a published eleven-model run, the inflation landed exactly on
  the two models that lost the most samples — the two at the top of the
  table — moving one of them two places.

  `pass_at_k` now raises `ValueError` for `n < k`, since the Chen et
  al. estimator is undefined there and the right treatment of a short task
  (exclude it, or score at `@n`) is the caller's call to make explicitly.
  `SuiteReport` excludes short tasks from every `@k` metric, and reports the
  exclusion: `summary()` gains a `short_of_k` count per `k`, and
  `SuiteReport.short_of(k)` is public, so a shrunken denominator is visible
  rather than silent.

## 0.4.0 — 2026-08-10

Lookaround is decided rather than refused, and fourteen wrong answers are
gone. Most of those were found by three things this release also adds: a
runtime check that every AST walker names every node type, a differential
generator whose alphabet is derived from the declared syntax surface instead
of maintained by hand, and `crosscheck` — comparing one pattern's automaton to
`re` string by string, over half a million regexes people actually wrote.

**Anyone on 0.3.0 should upgrade.** Every fix below is a wrong verdict, not a
crash: patterns that came back EQUIVALENT or DIFFERENT when the opposite was
true. Coverage moves both ways — lookaround adds a great deal, and the `$`
refusals take Re(gEx|DoS)Eval's SEARCH score from 669/762 to 660/762, because
those nine were being answered wrongly on any subject ending in a newline.

### Added

- **Walkers name every node type, and a test proves it.** Every recursive
  function over the AST used to end in a bare `return False`, so a node type
  a walker had never been taught about took the default silently. That is how
  `(?!a?)a` came back equivalent to `a` — `_epsilon_restrict` met a
  `Lookaround`, fell through to the complement branch, and deleted the
  assertion — and it is the same shape as every other wrong-answer family this
  engine has shipped.

  The 17 walkers now enumerate their leaves explicitly and end in
  `_unhandled()`, so "nothing to do here" reads differently from "nobody
  thought about it". `tests/test_walkers.py` puts all 12 node types through all
  17 walkers directly, rather than hoping some pattern reaches the branch;
  removing `Lookaround` from `_epsilon_restrict` fails it on exactly that
  pair. A new `Node` subclass fails the inventory test until it is added, and a
  new walker fails a meta-test until it is registered.

  Python's own `ast.NodeVisitor` defaults to a silent `generic_visit`, and
  `typing.assert_never` only bites under a type checker this project does not
  run — so the guarantee is a runtime raise plus a structural test, which
  needs neither a dependency nor a checker.

- **The differential generator draws its atoms from a declared syntax
  surface.** `_syntax.SYNTAX` lists all 56 constructs the engine claims to
  support, each with a fragment that exercises it, and the generator's alphabet
  is that list. Two tests keep it honest: one asserts every declared construct
  is actually emitted — grammar coverage, the adequacy criterion the
  grammar-fuzzing literature uses — and one asserts every escape letter, group
  opener and node type the parser accepts is declared, so support cannot be
  added without being generated.

  Hand-maintained atom lists are why escapes went a release and lookaround a
  whole branch with a generator structurally unable to emit them; both were
  only fixed after the wrong verdicts had shipped. Adding `\Q` to
  `_PATTERN_ESCAPE_LETTERS` now fails a test until it is declared, and
  declaring it feeds the generator with no further work.

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

  Nesting is decided only inside a positive lookahead. That is the one case
  where a nested assertion fires where the outer one does — so its marker can
  ride the outer's edge — and where the outer's condition is a conjunction
  the chain can take apart. Nested inside a lookbehind the position is wrong,
  because the body ends at the firing position and begins a body's width
  earlier; nested inside a negative assertion the chain is De Morgan run
  backwards, asking that neither conjunct hold rather than that the
  conjunction fail.

  Seven shapes are refused rather than answered, because the marker
  construction cannot represent them: a lookaround nested past the start of
  another's body, one nested inside a lookbehind or a negative assertion, one
  nested where the body can skip it, a `\b`/`\B` immediately in front of one
  (a marker fires before any character is consumed, and a boundary is only
  crossed while consuming one), a `\b`/`\B` at the right edge of a lookbehind
  body (the window ends there and cannot see the character after it), a
  lookaround inside a dk.brics `&`/`~` operand (the context gate cannot carry
  the preceding text a lookbehind needs), and a variable-width lookbehind,
  which Python refuses too. A `$` inside a body is refused under search for a
  separate reason, listed below.

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

### Fixed

Found by fuzzing the lookaround construction against `re`.

- **A nullable atom between a `\b` and a lookaround marker was read as a
  separator.** A marker fires before any character is consumed, so an atom
  that *can* be empty does not push it past the boundary: `\b(x?)(?=b)b` is
  exactly as unrepresentable as `\b(?=b)b`, and was decided anyway. The
  boundary-resolution helpers now ask whether an atom is nullable rather than
  whether it is zero-width, which also stops `(a|)` crashing one of them.

- **The sentinel alphabet could not tell `\n` from other whitespace.**
  Python's `.` excludes exactly the newline and nothing else, and the search
  reduction of `$` appends an optional `[\n]` — both need a newline symbol in
  the alphabet to mean anything, or there is nothing for them to match on.
  Without it `a.` came back equivalent to `ab|a[^b]`.

- **dk.brics' `.` matches the newline too.** Its patterns are over plain
  strings, with no notion of a line, so it parses as any-character there
  rather than as Python's "anything but a newline".

### Fixed

Found by running three real-world corpora through the engine — 43,896 regexes
used by PyPI packages, 495,135 from Stack Overflow posts and 3,838 from
RegExLib, all from the LinguaFranca artifact — with every pattern's automaton
crosschecked against `re` string by string, under both semantics. Six
wrong-answer bugs, none of which the suite could reach at the time.

- **An identity-keyed memo could read another node's answer.** Anchor
  resolution memoises on `id(node)` but kept no reference to the node, and
  resolution allocates and discards nodes constantly — so CPython handed a
  freed address to the next allocation and the new node inherited the old
  one's result. The symptom was a pattern that disagreed with `re` only when
  another pattern had been resolved first in the same process, which is why
  it took a 44,000-pattern run to surface and did not reproduce in isolation.
  Both memo tables now hold their keys, and `tests/test_identity_cache.py`
  asserts that directly with a weakref rather than trying to provoke a
  collision.

- **`$` was folded as plain end-of-string.** Without `re.MULTILINE`, Python's
  `$` matches at the end of the subject *and* immediately before a newline
  that ends it, so `re.fullmatch(r"a$\n", "a\n")` matches and
  `re.search(r"b$", "b\n")` finds one. The engine treated `$` as the end
  outright: `a$\n` came back as the empty language and so different from
  `a\n`, and `(a|\n)b` was reported different from `(a|\n)b$` on a witness
  `re` matches both ways.

  Under SEARCH the reduction now allows exactly that one trailing newline
  after the match, which is exact and costs no coverage. Under FULLMATCH
  there is no wrapper to widen, and folding the anchor would have to
  constrain the text after it, so a `$` in front of text that could be a
  newline is refused instead — a `$` at the end of the pattern, the common
  shape by far, is still decided. Nine of the 762 Re(gEx|DoS)Eval references
  move from decided to refused under FULLMATCH as a result; the SEARCH count
  the corpus is scored on is unchanged.

- **`$` inside a lookaround body was folded as end-of-string under search.**
  The same rule as the entry above, in the one path that fix did not cover.
  `_contains_anchor` deliberately does not look inside a lookaround body,
  because an anchor there is meaningful rather than misplaced — `(?=^a)` says
  the match begins the subject. But a `$` there is folded as plain
  end-of-string, and under a search the subject can always carry one more
  newline, so `a(?=b$)` missed "ab\n" and `(?!^0*$)\d{1,5}` accepted "0\n"
  that `re` rejects. Refused now, the way the full-match branch already
  refused it. Nine of Re(gEx|DoS)Eval's references move to UNSUPPORTED.

- **A word boundary at the right edge of a lookbehind body was answered.** A
  lookbehind is certified by a sliding window over the text already read, and
  the window ends where the assertion fires — so a `\b` on that edge needs the
  character *after* the window, the one thing the window cannot carry.
  `(?<=\b)a` was certified as "preceded by a word character, so no boundary"
  and rejected "a"; `(?<=\d\b)(?!,)` found a match in "0z" where `re` finds
  none. A boundary anywhere else in the body is still decided: at the left
  edge the entry context carries the preceding character, and in the middle
  both sides are inside the window.

- **Chained assertion markers fired as alternatives, not in series.** A
  lookaround nested at the start of another's body has its marker chained onto
  the outer's edge, and each was linked from the same state to the same state
  — which makes them alternatives. A run fires exactly one, every other
  constraint machine sees no firing, and a machine with no firing to check
  reads as vacuously satisfied. `(?=(?!b)a)` therefore matched the empty
  string: the run fired the inner marker and nothing ever asked whether an
  "a" followed. They now chain in series, so every assertion on the edge is
  certified.

  Two nestings the chain cannot represent at all are refused with it. Inside a
  lookbehind the firing position is wrong — the body ends where the assertion
  fires and begins a body's width earlier — so `(?<=(?<=a)b)` was certified
  against the text after the match and rejected "ab". Inside a negative
  assertion the chain is De Morgan run backwards, asking that neither conjunct
  hold rather than that the conjunction fail, so `(?!(?!a))a` matched nothing
  where `re` matches "a". Nesting stays decided inside a positive lookahead,
  which is where it is sound.

- **An anchor nested inside a region a `$` collapsed was dropped.** `a$`
  forces everything after it to be the empty string, and the resolver
  collapsed that tail — but empty text still has a position, and the `^` in
  `a$(^)+` demands position zero, which the `a` in front of it rules out. The
  scan that finds anchors only looks at a concatenation's own parts, so a `^`
  one group down went with the tail and `a$(^)+` came back matching `"a"`.
  The tail is now resolved in both cases the middle allows — occupied, where
  such an anchor cannot hold, and empty, where it holds exactly as the outer
  context allows — so `a?$(^)+` still matches the empty string.

### Added

- **`crosscheck` — this engine's automaton against `re`, string by string.**
  Everything else in this package compares two *patterns*, and a verdict about
  a pair only goes wrong when the two patterns go wrong in different ways, so
  a mistake the engine makes uniformly cancels out of it. Membership has no
  such cancellation, and every bug in the Fixed sections below was found that
  way.

  `crosscheck(pattern)` returns AGREES, DISAGREES with a witness, or UNCHECKED
  with the refusal that stopped it. `regexbench crosscheck FILE` runs it over a
  corpus and exits non-zero on a disagreement, with a breakdown of why the rest
  went unchecked.

- **`load_linguafranca` — half a million regexes people actually wrote.**
  The three corpora from the LinguaFranca FSE'19 artifact: patterns extracted
  from 193,524 projects in eight languages, from Stack Overflow posts, and from
  regexlib.com. They carry no prompts and no references, so they are not
  benchmarks and the loader returns patterns rather than tasks; what they are
  for is checking the engine on syntax nobody curated. `registry="pypi"` keeps
  the ones a Python result can honestly be stated over.

- **The suite runs real-world patterns on every run.** 355 of them, checked in
  under `tests/data/` with attribution — the one place a third-party corpus is
  redistributed here, and a test fixture rather than a benchmark. The first ten
  are the patterns that exposed the six bugs below. `REGEXBENCH_CORPUS` points
  the same test at a downloaded corpus when you want the whole thing.

### Changed

- **The differential suite compares membership, not only verdicts.** A
  verdict about a pair is only wrong when the two patterns are wrong in
  *different* ways, so a rule the engine applies uniformly cancels out: the
  `$` bug above produced one failure in 21,000 generated pairs. A new test
  runs each generated pattern's automaton against `re` string by string,
  which is the check the real-world corpora get, and it fails on the first
  seed without the fix. Run over the lookaround generator it also catches the
  marker-chaining bug above, which the pairwise version had run past for the
  length of a branch.

  Two supporting gaps closed with it. The generator attached quantifiers to
  every atom outside a hardcoded `\b`/`\B` list, so every draw of `^` or `$`
  became `^*` and was discarded as a syntax error — it now asks `re` whether
  the quantified atom compiles. And the corpus alphabet was `"ab"`, which
  cannot express a difference that only shows on a newline; it is now
  declared next to the atoms in `_syntax.CORPUS_ALPHABET` and pinned against
  the parser's own class tables, so every shorthand class has a member and a
  non-member in it.

### Fixed

- **Possessive quantifiers and atomic groups are refused where `re` refuses
  them.** Both arrived in CPython 3.11, so on 3.10 the engine was answering
  `(a*+)` and `(?>a)` — patterns the interpreter running `check()` and
  `screen()` cannot compile at all. Probed rather than version-tested, like
  the `\B` rule. Found by the syntax-surface test above on its first run
  against 3.10, which is the sort of thing it exists for.

### Changed

- **Benchmark coverage grows.** 669/762 = 87.8% of Re(gEx|DoS)Eval's SEARCH
  references parse now, up from 629/762 = 82.5% (FULLMATCH 740/762 = 97.1%).

  Since then the `$` refusals above have taken SEARCH to 660/762 = 86.6% and
  FULLMATCH to 731/762 = 95.9%. Those references were being answered, and
  answered wrongly on any subject ending in a newline.
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
