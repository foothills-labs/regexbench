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

## Match semantics

Equivalence and scoring default to **full matches**, like `re.fullmatch`. Not
every benchmark means that, and the disagreement is silent — a reference
written for `re.search` simply looks wrong when full-matched.

```python
from regexbench import Semantics, equivalent

equivalent("a", ".*a.*").verdict                              # DIFFERENT
equivalent("a", ".*a.*", semantics=Semantics.SEARCH).verdict  # EQUIVALENT
```

Under `SEARCH`, `p` is rewritten to `.*p.*` around whatever it already
anchors, so there is still exactly one notion of equivalence underneath. The
wildcards distribute over a top-level alternation, because in `a|b$` the
anchor constrains `b` and says nothing about `a`.

Measured on Re(gEx|DoS)Eval: its reference expressions pass **100%** of their
own tests under `search` and **94.0%** under `fullmatch`. Choosing wrong there
would score 46 gold patterns as failing the tests they were written for.

## Dialects

The natural-language-to-regex corpora are not written in Python syntax. They
use `dk.brics.automaton` notation, where `&` is intersection and `~` is
complement — and Python's `re` compiles both as ordinary literals without
complaint.

```python
from regexbench import Dialect, equivalent

# In Python this is the literal "a&b". In dk.brics it is the empty language.
equivalent("(a)&(b)", r"a\&b").verdict                        # EQUIVALENT
equivalent("(a)&(b)", r"a\&b", dialect=Dialect.BRICS).verdict # DIFFERENT

equivalent("([0-9])&([0-4])", "[0-4]", dialect=Dialect.BRICS) # EQUIVALENT
```

`&` appears in 22.8% of KB13 and 27.3% of NL-RX, `~` in 7.6% and 17.2%. The
dialect is never sniffed, because both readings compile and only one is right.

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

`usable` is the one to gate on: never a ReDoS liability, never *proven*
different from the reference, and perfect on whatever examples exist. A pattern
that passes every example it was given can still be known-wrong —
`#[0-9a-f]{6}` passes a hex-colour task whose examples happen to be lowercase —
and the reference settles it.

## Benchmarks

Scores are comparable only when they are computed on the same problems, so
loaders are included for the corpora this literature reports on. No dataset is
redistributed; you download the files and pass the path.

```python
from regexbench.datasets import load_regexeval, load_deep_regex, load_tasks

tasks = load_regexeval("RegexEval.json")      # 762 real prompts, with tests
tasks = load_deep_regex("datasets/KB13")      # 824 gold patterns, no examples
tasks = load_tasks("my_eval.jsonl")           # your own
```

Each loader sets the semantics and dialect the corpus actually uses. Loaders
never filter: a record this engine cannot represent is still returned and
surfaces as `UNSUPPORTED` when scored, because a corpus quietly reduced to its
easy half reports a number nobody can interpret.

See [docs/benchmarks.md](docs/benchmarks.md) for where to download each one and
what coverage to expect.

## Scoring a whole model

```python
from regexbench import run

report = run(tasks, predictions, name="my-model", workers=8)
print(report.table(ks=(1, 5)))
```

```
my-model
762 tasks, 762 answered
  pass@1        100.0%
  dfa-eq@1       77.4%
  exact@1       100.0%
  usable@1       85.8%
  vulnerable@1   14.2%  (lower is better)
  172 task(s) undecidable — counted against dfa-eq
```

`predictions` is a mapping from task name to the pattern, or to a list of
sampled patterns, or a sequence aligned with the tasks. All metrics use the
unbiased pass@k estimator, so they line up with published numbers.

Two things are deliberately not smoothed over. **A metric no task can answer is
`None`, not zero** — KB13 ships no examples, and a 0% pass@1 would read as a
model failing a question nobody asked it. **An undecidable comparison counts
against `dfa-eq`**, making it a lower bound over the whole corpus rather than
an average over the analyzable subset, with `undecided` reporting the size of
that gap.

## CLI

```bash
regexbench eq '[0-9]+' '\d+'                # equivalent
regexbench eq --search 'a' '.*a.*'          # equivalent
regexbench eq --brics '([0-9])&([0-4])' '[0-4]'
regexbench safety '(a+)+'                   # exponential
regexbench check '\d{3}' task.json          # 5/5 (100%)

regexbench run regexeval RegexEval.json --predictions preds.json --k 1 5
```

Exit codes are meaningful, so these compose in CI: `0` on equivalent/safe/all
passing, `1` otherwise.

`run` also takes `--use-reference`, which scores every task against its own
gold answer. That sounds circular and is the most useful thing here: it
separates what the corpus can tell you from what your model did. A `pass@1`
below 100% means the dataset is loaded with the wrong match semantics, and the
`dfa-eq@1` it reports is the ceiling this engine imposes — no model can be
measured above it.

## Supported syntax

The equivalence engine covers the genuinely regular subset: literals, escapes
(`\d \w \s` and negations), `.`, character classes with ranges and negation,
`*` `+` `?` `{m,n}`, alternation, and grouping. In the `BRICS` dialect it also
covers intersection (`&`), complement (`~`), any-string (`@`) and the empty
language (`#`) — all regular operations, computed on the automata directly.
Anything else returns `UNSUPPORTED` or `UNDECIDABLE` rather than a wrong
answer.

Word boundaries (`\b`) are refused in both dialects. The corpora that use them
mean a word boundary; the dk.brics spec says a literal `b`. Both readings are
defensible, so neither is assumed — which is the single biggest limit on
coverage today, and the clearest thing to fix next.

Correctness scoring and ReDoS screening have no such limit — they run the real
`re` engine and work on any pattern it compiles.

## Status

Alpha. The API will change. Stdlib only, no dependencies.

## License

Apache-2.0.
