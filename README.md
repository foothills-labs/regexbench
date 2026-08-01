# regexbench

**Evaluate a regex the way a benchmark should.**

Scoring generated regular expressions by string comparison is wrong: `[0-9]+`
and `\d+` are the same language and different strings. And a pattern that
passes every test can still hang a production server.

`regexbench` answers the three questions that actually matter — is it the same
language, does it behave, and is it safe to run.

```bash
pip install regexbench
```

## Semantic equivalence

Both patterns compile to DFAs and the automata are compared, which is the
DFA-EQ metric used in the regex generation literature.

```python
from regexbench import equivalent

bool(equivalent(r"[0-9]+", r"\d+"))       # True
bool(equivalent(r"(ab)+", r"ab(ab)*"))    # True

result = equivalent(r"a+", r"a*")
result.verdict     # <Verdict.DIFFERENT>
result.witness     # '' — the shortest string telling them apart
```

Witnesses are shortest-first and real: every one is a string you can paste
into `re.fullmatch` to see the difference yourself.

**When it says it doesn't know.** Backreferences and lookaround make a pattern
non-regular, and equivalence is then formally undecidable. Rather than guess,
the verdict is `UNDECIDABLE`:

```python
equivalent(r"(a)\1", r"aa").verdict    # <Verdict.UNDECIDABLE>
```

Equivalence is defined over **full matches**, like `re.fullmatch`.

## ReDoS safety

Two passes. Structural analysis finds the shapes that backtrack
catastrophically and says *why*; an empirical pass then runs the pattern
against attack strings under a timeout to catch what the structural pass
doesn't model.

```python
from regexbench import screen

screen(r"(a+)+").risk        # <Risk.EXPONENTIAL>
screen(r"(a+)+").reason      # 'a quantifier wraps a quantified group...'
screen(r"\d{3}-\d{4}").risk  # <Risk.SAFE>
```

`SAFE` means "no known-bad shape and no blow-up on what we tried". That is a
screening result, not a proof.

## Running untrusted patterns

Python's `re` has no timeout, and a pathological pattern spins inside a single
C call that no signal or thread can interrupt. The only reliable escape is a
separate process:

```python
from regexbench import safe_search, MatchTimeout

try:
    safe_search(r"(a+)+$", "a" * 40 + "!", timeout=0.5)
except MatchTimeout:
    ...   # the process was killed; your server is still up
```

Costs milliseconds per call. Use it for model output and user input; use `re`
directly for patterns you wrote.

## Scoring against examples

```python
from regexbench import Task, evaluate

task = Task(
    prompt="three digits, a hyphen, four digits",
    positives=["123-4567"],
    negatives=["123-456", "abc"],
    reference=r"\d{3}-\d{4}",
)

report = evaluate(r"[0-9]{3}-[0-9]{4}", task)
report.correctness.accuracy   # 1.0
report.equivalence.verdict    # <Verdict.EQUIVALENT>
report.usable                 # True — correct *and* safe
```

`usable` is the one to gate on. A pattern that scores perfectly but can hang
is still not shippable, and it reports `False`.

## CLI

```bash
regexbench eq '[0-9]+' '\d+'          # equivalent
regexbench safety '(a+)+'             # exponential
regexbench check '\d{3}' task.json    # 5/5 (100%)
```

Exit codes are meaningful, so these compose in CI: `0` on equivalent/safe/all
passing, `1` otherwise.

## Supported syntax

The equivalence engine covers the genuinely regular subset: literals, escapes
(`\d \w \s` and negations), `.`, character classes with ranges and negation,
`*` `+` `?` `{m,n}`, alternation, and grouping. Anything else returns
`UNSUPPORTED` or `UNDECIDABLE` rather than a wrong answer.

Correctness scoring and ReDoS screening have no such limit — they run the real
`re` engine and work on any pattern it compiles.

## Status

Alpha. The API will change. Stdlib only, no dependencies.

## License

Apache-2.0.
