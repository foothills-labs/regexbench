"""Word boundaries.

`\\b` is regular — it depends only on the two characters either side of a
position — but deciding it needs the alphabet to distinguish word characters
from the rest, which is why the "everything else" sentinel is split in two.

Getting it wrong is the expensive kind of wrong: `\\b` appears in 48.9% of KB13
and 19.0% of NL-RX, and refusing it used to cap KB13 coverage at 51.1%.
"""

from __future__ import annotations

import pytest

from regexbench import Dialect, Semantics, Verdict, equivalent

BRICS = Dialect.BRICS
SEARCH = Semantics.SEARCH


class TestBasics:
    def test_a_boundary_is_not_the_same_as_no_boundary(self):
        result = equivalent(r"\bab", "ab", semantics=SEARCH)
        assert result.verdict is Verdict.DIFFERENT
        # Something must precede `ab` without a boundary, e.g. another word char.
        assert result.witness is not None and result.witness.endswith("ab")

    def test_a_word_in_isolation_differs_from_the_same_letters_inside_a_word(self):
        result = equivalent(r"\bab\b", "ab", semantics=SEARCH)
        assert result.verdict is Verdict.DIFFERENT

    def test_boundaries_are_redundant_under_full_match(self):
        # Full-matching consumes the whole string, so a leading `\b` before a
        # word character always holds.
        assert equivalent(r"\bab\b", "ab").verdict is Verdict.EQUIVALENT

    def test_b_and_negated_b_are_opposites(self):
        # Between two word characters there is no boundary, so `a\bb` matches
        # nothing while `a\Bb` matches "ab".
        assert equivalent(r"a\bb", "#", dialect=BRICS).verdict is Verdict.EQUIVALENT
        assert equivalent(r"a\Bb", "ab").verdict is Verdict.EQUIVALENT

    def test_the_empty_string_is_pythons_special_case(self):
        """`\\B` does not match "" in Python, though no boundary exists there.

        A quirk rather than a consequence — most engines match — but patterns
        scored here are run by `re`, so `re` is what gets modelled.
        """
        import re

        assert re.fullmatch(r"\B", "") is None
        # `\B` consumes nothing, so under full match the empty string is the
        # only thing it could accept — and it does not. It is the empty
        # language, not the empty string.
        assert equivalent(r"\B", "#", dialect=BRICS).verdict is Verdict.EQUIVALENT
        differs = equivalent(r"\B", "")
        assert differs.verdict is Verdict.DIFFERENT
        assert differs.witness == "", "the empty string is exactly what separates them"
        # ...and `\B` still holds between two non-word characters.
        assert equivalent(r" \B ", "  ").verdict is Verdict.EQUIVALENT


class TestAlphabet:
    def test_unnamed_word_and_non_word_characters_are_distinguished(self):
        """The reason one sentinel is not enough.

        `\\bz` finds "z" after a space and not after a letter. If every unnamed
        character were one symbol, the two cases would be indistinguishable.
        """
        result = equivalent(r"\bz", "z", semantics=SEARCH)
        assert result.verdict is Verdict.DIFFERENT
        assert result.witness is not None

    def test_a_witness_is_a_real_string(self):
        import re

        result = equivalent(r"\bab", "ab", semantics=SEARCH)
        witness = result.witness
        assert witness is not None
        assert (re.search(r"\bab", witness) is not None) != (
            re.search("ab", witness) is not None
        ), f"witness {witness!r} does not reproduce the difference"

    def test_shorthand_classes_reach_beyond_their_ascii_members(self):
        # `\w` enumerates 63 ASCII characters and covers the rest of Unicode by
        # class, so it is not `[A-Za-z0-9_]` — and the witness proves it.
        result = equivalent(r"\w", r"[A-Za-z0-9_]")
        assert result.verdict is Verdict.DIFFERENT
        assert result.witness is not None and not result.witness.isascii()


class TestInsideOperators:
    """Assertions inside `&` and `~`, which determinize their operands.

    A determinized operand has baked in an assumption about what precedes it,
    and that assumption is wrong as soon as the operator is not at the start.
    """

    def test_an_assertion_inside_an_intersection_sees_its_real_context(self):
        # Between 'x' and 'a' both are word characters, so there is no boundary
        # and the whole pattern matches nothing.
        assert equivalent(r"x((\bab)&(ab))", "#", dialect=BRICS).verdict is Verdict.EQUIVALENT
        assert equivalent(r"x((\bab)&(ab))", "xab", dialect=BRICS).verdict is Verdict.DIFFERENT

    def test_the_same_operator_at_the_start_does_match(self):
        assert equivalent(r"((\bab)&(ab))", "ab", dialect=BRICS).verdict is Verdict.EQUIVALENT

    def test_a_variable_length_prefix_still_resolves_the_context(self):
        assert (
            equivalent(r".*((\bab)&(ab)).*", r".*\bab.*", dialect=BRICS).verdict
            is Verdict.EQUIVALENT
        )

    def test_an_assertion_inside_a_complement(self):
        # `~(\bZZ)` is everything that is not a boundary-preceded "ZZ".
        assert equivalent(r"~(\bZZ)", r"~(ZZ)", dialect=BRICS).verdict is Verdict.EQUIVALENT


class TestRealCorpusPatterns:
    @pytest.mark.parametrize(
        "pattern",
        [
            r".*\b[A-Za-z]*er\b.*",
            r".*\bdance\b.*",
            r".*((\b[A-Za-z]+\b)&(.*spoon)).*",
            r"(.*\bblack\b.*)&(.*z.*)",
            r"~(\b([A-Z])(.*)\b)",
            r"\b([A-Z])&([AEIOUaeiou])\b",
        ],
    )
    def test_kb13_and_nl_rx_patterns_are_analyzable(self, pattern: str):
        """Verbatim gold patterns that used to come back UNSUPPORTED."""
        result = equivalent(pattern, ".*", dialect=BRICS)
        assert result.verdict in (Verdict.EQUIVALENT, Verdict.DIFFERENT), (
            f"{pattern!r} was not analyzable: {result.reason}"
        )

    def test_the_kb13_gloss_matches_the_pattern(self):
        """KB13 reads `.*\\b[A-Za-z]*er\\b.*` as "lines using words ending in 'er'".

        Which is only true under the word-boundary reading — dropping the
        boundaries gives a different language, and that difference is the whole
        reason the dk.brics literal-'b' reading is not used.
        """
        boundary = r".*\b[A-Za-z]*er\b.*"
        assert equivalent(boundary, r".*[A-Za-z]*er.*", dialect=BRICS).verdict is Verdict.DIFFERENT
