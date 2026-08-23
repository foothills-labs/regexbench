# Handoff — regexbench

> **Historical document.** Written 2026-08-01, when the lab traded as
> Foothills Labs (renamed Plicara Labs 2026-08-20; this repo is now
> `plicara/regexbench`). Moved here from `foundation_lab/docs/handoffs/` on
> 2026-08-23 — a project's handoff lives in its own repository. Kept as the
> record of the handoff, not as guidance: verify anything below against this
> repo's README and CHANGELOG before relying on it.

Context for a fresh session working on `foothills-labs/regexbench`.
Written 2026-08-01. Everything below is verified unless marked otherwise.

---

## 1. What it is

`regexbench` evaluates generated regular expressions on the three axes that
matter and that string comparison cannot see:

1. **Semantic equivalence** — `[0-9]+` and `\d+` are the same language and
   different strings. Exact-match scoring is simply the wrong metric.
2. **Correctness** against worked examples.
3. **ReDoS safety** — a pattern that passes every test can still hang a
   production server.

It exists because Foothills Labs wants to fine-tune a model that writes regex,
and the lab's sequencing is **benchmarks before training**. Here the eval *is*
the training signal, so it comes first.

## 2. State

| Fact | Value |
| --- | --- |
| Repo | `foothills-labs/regexbench`, public |
| Branch | `main` |
| Head | `54698e6` — "Initial commit: regexbench 0.1.0" |
| CI | **Green.** Lint + tests on Python 3.10/3.11/3.12/3.13, plus a build job running `twine check` |
| Published | **No.** Nothing on PyPI yet |
| PyPI name | `regexbench` — verified free 2026-08-01 with controls (also free on npm) |
| License | Apache-2.0, real `LICENSE` file, declared in `pyproject.toml` |
| Dependencies | **None.** Stdlib only. `pytest` and `ruff` are dev-only |
| Tests | 63, all passing |

## 3. Layout

```
src/regexbench/
  types.py         Verdict, Risk, Task, Report and the result dataclasses
  _parse.py        recursive-descent parser for the REGULAR subset -> AST
  _automata.py     Thompson NFA -> subset construction -> DFA; product-BFS compare
  equivalence.py   public equivalent() / is_regular()
  safety.py        two-pass ReDoS screening
  execute.py       subprocess-isolated matching with a real timeout
  correctness.py   check() against examples, evaluate() for the full Report
  cli.py           regexbench eq | safety | check
tests/
  test_equivalence.py   equivalence pairs, undecidability, witnesses
  test_safety.py        ReDoS shapes, timeout behaviour
  test_correctness.py   scoring, false positives/negatives
  test_differential.py  randomized cross-check against Python's re
```

`_parse` and `_automata` are private on purpose. The public surface is
`equivalent`, `screen`, `check`, `evaluate`, `safe_fullmatch`, `safe_search`.

## 4. How equivalence works, and why it's trustworthy

Both patterns parse to an AST, compile via Thompson construction to an NFA,
subset-construct to a complete DFA, and compare by breadth-first search over
the product automaton. BFS means the returned witness is a **shortest**
counterexample, which is what makes failure reports readable.

**The alphabet trick matters.** The alphabet is every character named in
either pattern, plus an `OTHER` sentinel standing for everything else.
Equivalence over that finite alphabet is exactly equivalence over all of
Unicode, because unnamed characters are indistinguishable to both patterns.
When a witness needs `OTHER`, a concrete filler character not named in either
pattern is substituted, so **every witness is a real string you can paste into
`re.fullmatch`**.

**Verification.** Beyond unit tests, the engine was fuzzed against Python's
own `re`: **4000 random pattern pairs, zero disagreements.** Every
`EQUIVALENT` verdict held across the corpus; every `DIFFERENT` verdict came
with a witness `re` agreed on. That is preserved as `test_differential.py`
with seeded generators, so failures reproduce.

## 5. Design decisions — settled, don't relitigate

- **`UNDECIDABLE` is a correct answer, not a failure.** Backreferences and
  lookaround make a pattern non-regular and equivalence formally undecidable.
  Returning a guess would be worse than returning nothing. It is kept distinct
  from `UNSUPPORTED`, which means "syntax this parser doesn't implement" —
  those are different problems with different fixes.
- **Equivalence is defined over full matches**, like `re.fullmatch`.
- **`SAFE` is a screening result, not a proof.** It means no known-bad shape
  and no blow-up on the strings tried. The docstrings say this; keep them
  honest.
- **Timeouts need a subprocess.** Python's `re` has no timeout and spins
  inside a single C call that no signal or thread can interrupt. `spawn` is
  used rather than `fork` so it stays usable from threads. It costs
  milliseconds per call — that is the price of the only mechanism that works.
- **`Report.usable` requires correct *and* safe.** A pattern that scores
  perfectly but can hang is not shippable, and reports `False`. There is a
  test asserting exactly that.
- **A pattern that doesn't compile is not "dangerous"** — `screen()` returns
  `SAFE` with a reason, because a broken pattern is a correctness problem, not
  a security one.

## 6. Research grounding

Worth knowing so the next round of work stays anchored:

- **DFA-EQ** is the established metric in the regex-generation literature;
  exact match is known to be insufficient.
  [RegexPSPACE](https://arxiv.org/abs/2510.09227) built a million-instance
  benchmark on equivalence and minimization.
- [Re(gEx|DoS)Eval](https://dl.acm.org/doi/pdf/10.1145/3639476.3639757)
  evaluates generated regex for correctness *and* ReDoS together.
- LLM-generated regexes
  [skew toward polynomial ReDoS patterns](https://dl.acm.org/doi/10.1145/3643916.3644424).
- Existing tooling is scattered and mostly JS-centric
  ([vuln-regex-detector](https://github.com/davisjam/vuln-regex-detector)) or
  research artifacts (Rescue, ReDoSHunter, Revealer). A maintained,
  installable library covering all of correctness + equivalence + safety is
  the gap this fills.

## 7. What's deliberately not done

1. **Dataset adapters — the highest-value next step.** There is no loader for
   the published benchmarks (KB13, NL-RX-Turk, Re(gEx|DoS)Eval). Adding them
   makes scores directly comparable to the literature, which is what you want
   the moment you start scoring a fine-tuned model.
2. **A scoring harness** — `evaluate()` handles one pattern against one task.
   No batch runner, no aggregate report, no per-model comparison table.
3. **Regex minimization**, the other half of RegexPSPACE.
4. **Wider syntax.** Unicode property escapes (`\p{...}`), named-group
   backreferences, and conditionals all return `UNSUPPORTED`. Widening the
   parser widens equivalence coverage directly.
5. **An npm twin.** ReDoS is overwhelmingly a JavaScript problem, and
   `regexbench` is free on npm too. The `@foothills` scope is already owned.
   Deferred deliberately — Python first.

## 8. Gotchas

- **The session integration cannot create or delete GitHub repos** — `403
  Resource not accessible by integration`. Branch deletion is blocked the same
  way. The user does those in the UI.
- **Don't "simplify" `_automata.py` by dropping the `OTHER` sentinel.** It is
  what makes a finite alphabet sound. Removing it silently breaks equivalence
  for any pattern involving negated classes or `.`.
- **The empirical ReDoS pass runs real subprocesses.** It is the slow part of
  the test suite (~3s). Pass `empirical=False` to `screen()` for the
  structural pass alone.
- **A PyPI pending publisher does not reserve the name**, and PyPI version
  numbers are permanent. Rehearse on TestPyPI.

## 9. House style

Full brand guide is in `foundation_lab/docs/brand.md`. What matters here:
plain and specific prose, numbers over adjectives, claim only what is
measured, publish negative results. Comments explain *why*, not *what*.
The org is always plural — "Foothills", never "Foothill".
