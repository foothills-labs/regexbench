# Test data

## `real_world_patterns.txt`

355 regular expressions, one JSON-encoded string per line, sampled from the
three corpora in the LinguaFranca ESEC/FSE'19 artifact — regexes extracted from
PyPI packages, from Stack Overflow posts, and from regexlib.com.

> Davis, Michael IV, Coghlan, Servant and Lee, *Why Aren't Regular Expressions
> a Lingua Franca? An Empirical Study on the Re-use and Portability of Regular
> Expressions*, ESEC/FSE 2019.
> <https://github.com/VTLeeLab/LinguaFranca-FSE19> — MIT licensed.

This is the one place a third-party corpus is checked in, and it is a test
fixture rather than a benchmark: no prompts, no references, nothing to score a
model against. The loaders in `regexbench.datasets` still redistribute nothing
and take a path to files you download.

It is here because every wrong-answer bug this engine has shipped was a
construct nobody thought to write a test for, and a corpus of patterns nobody
curated is the cheapest way to keep meeting those. The first ten lines are
the patterns that exposed the six bugs found this way; the rest is a
deterministic sample, 90 crosscheckable and 25 refused from each corpus, so
the suite exercises both the answers and the refusals.

Sampled with `crosscheck` at the commit that fixed those six bugs. To rebuild
it, or to run the full half-million rather than this sample:

```bash
regexbench crosscheck uniq-regexes-8.json --registry pypi
```
