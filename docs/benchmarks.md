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
| `dfa-eq@1` | 100% | reflexivity — identical patterns, no automaton consulted |
| `vulnerable@1` | 14.2% | 108 of the corpus's own reference expressions are ReDoS-vulnerable |

`dfa-eq@1` of 100% here is not a coverage measurement. Identical text denotes
identical languages, so `equivalent()` short-circuits before parsing — which is
also what the reference tooling does, and what keeps scores comparable. To
measure how much of a corpus this engine can actually analyze, parse the
references instead:

```python
from regexbench import is_regular
from regexbench.datasets import load_regexeval

tasks = load_regexeval("RegexEval.json")
analyzable = sum(is_regular(t.reference, dialect=t.dialect) for t in tasks)
print(f"{analyzable}/{len(tasks)}")     # 604/762 = 79.3%
```

| Corpus | References this engine can parse |
| --- | --- |
| Re(gEx|DoS)Eval | 82.5% |
| KB13 | 100% |
| NL-RX-Synth / NL-RX-Turk | 100% |

Treat that as an **upper bound** on comparability rather than a guarantee. Both
sides of a comparison contribute to the alphabet, so a reference that parses on
its own can still exceed the determinization limit against a particular
candidate — 604 of Re(gEx|DoS)Eval's references parse, and 590 survived being
compared against themselves before reflexivity made that check trivial. The
`undecided` count in an actual run is the number that applies to that run.

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
| Parseable today | 100% | 100% | 100% |

**They carry no worked examples.** A record is a description and a gold
pattern. `pass@k` over these corpora is `None`, not zero — the only available
score is `dfa-eq@k`, which is exactly the metric this literature reports.

**They are dk.brics syntax, not Python.** `&` is intersection and `~` is
complement; `re` compiles both as literals and would silently mis-score a
quarter to two fifths of each corpus. The loader sets `Dialect.BRICS`.

**Every pattern in all three corpora is analyzable.** Word boundaries used to
be refused, which capped KB13 at 51.1% and NL-RX at 81.0%; they are supported
now, and nothing else in these corpora is rejected. Whatever `dfa-eq` reports
on them is a statement about the model, not about this engine.

Two notes on the `\b` reading. It deviates from the dk.brics spec, which
escapes `\b` to the literal character `b` — the corpora mean a boundary, and
their descriptions say so. And 14.6% of KB13 puts a boundary inside a `&` or
`~`, where the operand gets determinized and so bakes in an assumption about
what precedes it; the sub-machine is built once per possible context and
entered on the real one, because `x((\bab)&(ab))` matches nothing and a
sub-machine that thought it began the string would say otherwise.

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
corpus is loaded correctly: a `pass@1` far under 100% means the match semantics
are wrong. It will report `dfa-eq@1` of 100% by reflexivity, which says nothing
about coverage — use the `is_regular` count above for that.

`dfa-eq` is reported twice, because there are two honest questions and one
number cannot answer both:

* **`dfa-eq@k`** counts undecidable comparisons as failures. "How much of this
  corpus did we verify as correct" — a lower bound that cannot flatter, and the
  one to quote.
* **`dfa-eq@k (decided)`** drops undecidable tasks from the denominator. "Of
  what we could check, how much was correct" — the model on its own, blind to
  engine coverage.

KB13 makes the spread concrete: only 51.1% of its references can be analyzed
at all, so on the other 48.9% every candidate that is not textually identical
comes back undecidable and scores zero under the first reading. Watch both, and
treat a gap between them as a statement about this engine rather than about
whatever you are scoring.

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
pass over all 762 tasks takes about 20 seconds.

Equivalence on the dk.brics corpora costs more, and varies by an order of
magnitude between them:

| Corpus | Per comparison | Whole corpus, single-threaded |
| --- | --- | --- |
| NL-RX-Turk | 17 ms | ~3 minutes |
| KB13 | 609 ms | ~8 minutes |

KB13 is the expensive one because its patterns are: `[A-Za-z]` names 52
characters, so the alphabet is an order of magnitude wider than NL-RX's, and
48.9% of them also carry a word boundary and 14.6% an operator whose operand
has to be determinized once per entry context. Half that corpus used to return
UNSUPPORTED in microseconds, which is cheaper only in the sense that not
answering is cheaper than answering. Use `--workers`.

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

## References

The claims in this document were checked against these sources rather than
inferred from the data alone.

- **dk.brics.automaton `RegExp` syntax** —
  <https://www.brics.dk/automaton/doc/dk/brics/automaton/RegExp.html>. The
  grammar and its precedence (union < intersection < concatenation <
  repetition < complement), `#` for the empty language, `@` for any string, and
  the fact that `&` and `~` are optional syntax flags rather than always-on
  operators. `regexbench`'s BRICS dialect implements this grammar.
- **Chen and others, *Evaluating Large Language Models Trained on Code*, 2021**
  — <https://arxiv.org/abs/2107.03374>. The unbiased pass@k estimator
  `1 - C(n-c, k) / C(n, k)`, and the numerically stable product form used here
  because binomial coefficients overflow at benchmark-sized `n`.
- **Siddiq and others, *Re(gEx|DoS)Eval*, ICSE-NIER 2024** —
  <https://github.com/s2e-lab/RegexEval>. Source of the corpus, and of pass@k
  and vulnerable@k as a paired metric. Their `Evaluation/DFA_Equ_Evaluation.py`
  returns true on string equality before invoking `regex_dfa_equals.jar`, which
  is why `equivalent()` here short-circuits on identical patterns.
- **Siddiq and others, *Understanding ReDoS: Insights from LLM-Generated
  Regexes and Developer Forums*, ICPC 2024** —
  <https://dl.acm.org/doi/10.1145/3643916.3644424>. The five vulnerability
  families (nested quantifiers, exponential overlapping disjunction,
  exponential overlapping adjacency, polynomial overlapping adjacency, starting
  with large quantifier), and the finding that LLM-generated regexes skew
  polynomial. The structural pass here covers the first, second and fourth.
- **Gelade and Neven, *Succinctness of the Complement and Intersection of
  Regular Expressions*** —
  <https://www.cs.umd.edu/~gasarch/TOPICS/desc/regexpcompint.pdf>. Equivalence
  of plain regular expressions is PSPACE-complete, but with *both* complement
  and intersection it is non-elementary — which is why the BRICS dialect refuses
  deeply nested `&`/`~` at a state budget instead of trying.
- **Locascio and others, EMNLP 2016 (NL-RX)** and **Kushman and Barzilay, NAACL
  2013 (KB13)** — <https://github.com/nicholaslocascio/deep-regex>. Corpus
  sizes confirmed: KB13 is 824 expert-written pairs, NL-RX-Turk 10,000
  crowdsourced ones.

One thing this document does **not** claim, because no source was found for it:
how the reference tooling interprets `\b` in these corpora. The dk.brics grammar
has no word-boundary construct and escapes `\b` to a literal `b`, while the
descriptions plainly mean a word boundary. `regexbench` implements the
described intent and says so here, rather than the spec's letter — if the
reference tooling turns out to preprocess these patterns some third way, the
KB13 numbers would need re-basing against it.
