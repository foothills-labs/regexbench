# Benchmarks

Where to get each corpus, what shape it is, and what to expect when you score
it. Every number here was measured on the published files, not estimated.

None of these datasets are redistributed with `regexbench`. Download them
yourself; the loaders take a path.

---

## Re(gEx|DoS)Eval

762 regex prompts written by real users, each with a reference expression and
hand-built matching and non-matching strings.

> Siddiq, Zhang, Santos and others, *Re(gEx|DoS)Eval: Evaluating Generated
> Regular Expressions and their Proneness to DoS Attacks*, ICSE-NIER 2024.
> <https://github.com/s2e-lab/RegexEval>

```bash
curl -O https://raw.githubusercontent.com/s2e-lab/RegexEval/master/DatasetCollection/RegexEval.json
```

```python
from regexbench.datasets import load_regexeval

tasks = load_regexeval("RegexEval.json")                    # raw user prompts
tasks = load_regexeval("RegexEval.json", prompt="refined")  # with examples appended
```

| | |
| --- | --- |
| Tasks | 762 |
| Examples per task | 25 median, 6 to 63, always at least one of each |
| Semantics | `SEARCH` |
| Dialect | `PYTHON` |
| Reference | always present |

**It is a search corpus.** The reference expressions pass 100% of their own
tests under `re.search` and 94.0% under `re.fullmatch`. Loaded full-match, 46
gold patterns would be scored as failing the tests they were written for.

Scoring the corpus against itself — `--use-reference` — gives:

| Metric | Value | What it means |
| --- | --- | --- |
| `pass@1` | 99.9–100% | the references pass their own tests, so the corpus is loaded correctly |
| `dfa-eq@1` | 77.4% | the engine's ceiling: 172 tasks are undecidable, mostly lookaround and `\b` |
| `dfa-eq@1 (decided)` | 100.0% | of what could be checked, the gold answers are of course all correct |
| `vulnerable@1` | 14.2% | 108 of the corpus's own reference expressions are ReDoS-vulnerable |

`pass@1` moves between 99.9% and 100% run to run, and the cause is not the
loader. One record, `regexeval/1660`, has a gold reference that is itself a
ReDoS pattern — `^((\.)?([a-zA-Z0-9_-]?)(\.)?([a-zA-Z0-9_-]?)(\.)?)+$` — and it
backtracks on its own non-matching example `'.....444fef454#'` for long enough
to trip the one-second budget on a loaded machine. A timeout on a negative
example counts as a false positive, so that single task drops in and out.

The distinction matters when you use `pass@1` as a load check: a *wholesale*
drop, to around 94%, means the semantics are wrong. A wobble of one or two
tasks means a vulnerable gold pattern raced the timeout.

The `vulnerable@1` row is a property of the dataset rather than of this tool,
and is roughly the point the paper is making — `regexeval/1660` above is one of
the 108.

---

## KB13 and NL-RX

Natural-language descriptions paired with a gold regular expression. Three
corpora, one format, distributed together.

> Kushman and Barzilay, *Using Semantic Unification to Generate Regular
> Expressions from Natural Language*, NAACL 2013 (KB13).
> Locascio and others, *Neural Generation of Regular Expressions from Natural
> Language with Minimal Domain Knowledge*, EMNLP 2016 (NL-RX).
> <https://github.com/nicholaslocascio/deep-regex>

```bash
git clone https://github.com/nicholaslocascio/deep-regex
```

```python
from regexbench.datasets import load_deep_regex

tasks = load_deep_regex("deep-regex/datasets/KB13")
tasks = load_deep_regex("deep-regex/datasets/NL-RX-Turk")
```

Each directory holds `src.txt` (descriptions) and `targ.txt` (patterns), line
aligned, with no trailing newline.

| | KB13 | NL-RX-Synth | NL-RX-Turk |
| --- | --- | --- | --- |
| Tasks | 824 | 10,000 | 10,000 |
| Examples | none | none | none |
| Semantics | `FULLMATCH` | `FULLMATCH` | `FULLMATCH` |
| Dialect | `BRICS` | `BRICS` | `BRICS` |
| Patterns using `&` | 22.8% | 27.3% | 27.3% |
| Patterns using `~` | 7.6% | 17.2% | 17.2% |
| Patterns using `\b` | 48.9% | 19.0% | 19.0% |
| Parseable today | 51.1% | 81.0% | 81.0% |

**They carry no worked examples.** A record is a description and a gold
pattern. `pass@k` over these corpora is `None`, not zero — the only available
score is `dfa-eq@k`, which is exactly the metric this literature reports.

**They are dk.brics syntax, not Python.** `&` is intersection and `~` is
complement; `re` compiles both as literals and would silently mis-score a
quarter to two fifths of each corpus. The loader sets `Dialect.BRICS`.

**`\b` is the coverage ceiling.** Every rejection in the table above is a word
boundary — no other construct in any of the three corpora is refused, and
nothing crashes. Those records still load, and count against `dfa-eq` as
undecidable, so the score stays a lower bound over the whole corpus. Supporting
`\b` would lift KB13 from 51.1% to essentially complete, and is the highest
value change available to this engine.

---

## Your own tasks

A JSON array, or one JSON object per line:

```json
{"name": "us-phone",
 "prompt": "a US phone number",
 "reference": "\\d{3}-\\d{4}",
 "positives": ["555-1234"],
 "negatives": ["5551234"],
 "semantics": "fullmatch",
 "dialect": "python"}
```

```python
from regexbench.datasets import load_tasks

tasks = load_tasks("my_eval.jsonl")
```

Every field is optional except that a task needs either examples or a
reference. `semantics` defaults to `fullmatch`, `dialect` to `python`, and an
unrecognised field is an error rather than a typo you find out about later.

---

## Reading a score

`--use-reference` first, always. It costs one run and tells you whether the
corpus is loaded correctly and what ceiling the engine puts on it. A `pass@1`
far under 100% means the semantics are wrong; the `dfa-eq@1` it reports is the
most any model could score.

`dfa-eq` is reported twice, because there are two honest questions and one
number cannot answer both:

* **`dfa-eq@k`** counts undecidable comparisons as failures. "How much of this
  corpus did we verify as correct" — a lower bound that cannot flatter, and the
  one to quote.
* **`dfa-eq@k (decided)`** drops undecidable tasks from the denominator. "Of
  what we could check, how much was correct" — the model on its own, blind to
  engine coverage.

On KB13 the gold answers themselves score 51.1% by the first reading and 100.0%
by the second. That 49-point spread is not a model result at all; it is `\b`.
Watch both, and treat a gap between them as a statement about this engine
rather than about whatever you are scoring.

`exact` is reported for contrast: where equivalence is decidable, `dfa-eq`
above `exact` is the share of answers that are right and would be marked wrong
by string comparison.

`exact` can also come out *above* `dfa-eq`, and that is not a contradiction. An
exactly-correct answer to an undecidable task still counts as undecided, so it
scores for `exact` and not for `dfa-eq`. When you see that, read it as the
undecidable share being larger than the semantic-credit share — check
`undecided` — rather than as string comparison outperforming automata.

## Performance

Measured on Re(gEx|DoS)Eval, one candidate costs about **75 ms**, split:

| Stage | Cost | Why |
| --- | --- | --- |
| ReDoS screening | 54 ms | one child process per probe length |
| Example scoring | 18 ms | one child process for the whole batch |
| Equivalence | 4 ms | no subprocess at all |

Screening dominates, and it is the part that has to start processes: the only
way to know a pattern hangs is to run it somewhere killable. A `--use-reference`
pass over all 762 tasks takes about 20 seconds, and NL-RX-Turk's 10,000
equivalence comparisons — no examples, no screening — take about a minute.

Vulnerable candidates cost more, and unavoidably so: confirming a hang means
waiting out the timeout, once per example. A candidate that hangs on all 25 of
a task's examples costs 25 seconds at the default one-second budget. That is
not a rare case — 14.2% of the corpus's own references are vulnerable, so a
model trained on this kind of data will produce plenty.

Two levers:

* **`--workers 8`.** The work is in child processes, so threads help.
* **`--timeout 0.1`.** A tenth of a second is still far more than a healthy
  match needs, and it cuts the cost of every hang by ten.

Set the timeout too low and slow-but-fine patterns start reporting as timeouts,
which show up as failed examples. It is a real effect and a small one: on a
2,286-candidate sweep, dropping the budget from 1 s to 0.1 s moved `pass@1`
from 77.8% to 77.0% and left `dfa-eq`, `exact` and `vulnerable` identical, while
taking the run from over sixteen minutes to under seven. Only example scoring
can move, since it is the only metric that runs the pattern against the
corpus's own strings.

So change it once, deliberately, and keep it fixed across runs you intend to
compare — a timeout is part of a score's definition, not a tuning knob to reach
for after seeing the number.
