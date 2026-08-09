import itertools
import re

import pytest

from regexbench import Dialect, Semantics, Verdict, equivalent, is_regular
from regexbench._automata import build_dfa
from regexbench._parse import ATOMIC_GROUP_SUPPORTED, POSSESSIVE_SUPPORTED, parse

# Both arrived in CPython 3.11, and the parser refuses what the running `re`
# cannot compile — so on 3.10 these are `UNSUPPORTED` rather than decided, and
# asserting the decided answer there would be asserting against the
# interpreter the verdicts are measured on.
needs_possessive = pytest.mark.skipif(
    not POSSESSIVE_SUPPORTED, reason="possessive quantifiers need CPython 3.11+"
)
needs_atomic = pytest.mark.skipif(
    not ATOMIC_GROUP_SUPPORTED, reason="atomic groups need CPython 3.11+"
)


@pytest.mark.parametrize(
    "left,right",
    [
        (r"[0-9]+", r"[0-9][0-9]*"),
        (r"abc", r"abc"),
        (r"a|b", r"[ab]"),
        (r"(ab)+", r"ab(ab)*"),
        (r"a?", r"|a"),
        (r"[a-c]", r"a|b|c"),
        (r"a{2}", r"aa"),
        (r"a{1,3}", r"a|aa|aaa"),
        (r"(?:xy)*", r"(xy)*"),
        (r"\d{2,}", r"\d\d\d*"),
        (r"[^a]", r"[^a]"),
        (r"a*a*", r"a*"),
        (r"(a|b)*", r"[ab]*"),
    ],
)
def test_equivalent_pairs(left, right):
    result = equivalent(left, right)
    assert result.verdict is Verdict.EQUIVALENT, f"{left} vs {right}: {result.reason}"
    assert bool(result) is True


@pytest.mark.parametrize(
    "left,right",
    [
        (r"a+", r"a*"),
        (r"[0-9]", r"[0-9a]"),
        (r"abc", r"abd"),
        (r"a{2}", r"a{3}"),
        (r"\d+", r"\w+"),
        (r"a", r"ab"),
        (r"[^a]", r"."),
    ],
)
def test_different_pairs_report_a_real_witness(left, right):
    result = equivalent(left, right)
    assert result.verdict is Verdict.DIFFERENT, f"{left} vs {right}"
    assert result.witness is not None

    # The witness must genuinely separate them under Python's own engine,
    # otherwise the automata and re disagree and the result is worthless.
    in_left = re.fullmatch(left, result.witness) is not None
    in_right = re.fullmatch(right, result.witness) is not None
    assert in_left != in_right, (
        f"witness {result.witness!r} does not distinguish {left} from {right}"
    )


@pytest.mark.parametrize("pattern", [r"(a)\1", r"a\1"])
def test_backreferences_are_undecidable_not_guessed(pattern):
    """Backreferences leave the regular languages, so equivalence is undecidable."""
    result = equivalent(pattern, r"a")
    assert result.verdict is Verdict.UNDECIDABLE
    assert not is_regular(pattern)


@pytest.mark.parametrize("pattern", [r"(?=a)b", r"(?!a)b", r"(?<=a)b", r"(?<!a)b"])
def test_lookaround_is_decided_not_undecidable(pattern):
    """Lookaround alone stays in the regular languages, so equivalence with a
    plain pattern must be decided, not refused — and not escaped to
    undecidable, which would be a claim about the problem rather than this
    engine."""
    result = equivalent(pattern, r"a")
    assert result.verdict is Verdict.DIFFERENT
    assert result.witness is not None
    assert is_regular(pattern)


def test_undecidable_is_reported_for_either_side():
    assert equivalent(r"a", r"(a)\1").verdict is Verdict.UNDECIDABLE


def test_plain_patterns_are_regular():
    assert is_regular(r"\d{3}-\d{4}")
    assert is_regular(r"[a-z]+@[a-z]+\.[a-z]{2,}")


def test_anchors_at_the_ends_are_ignored_under_fullmatch():
    assert equivalent(r"^abc$", r"abc").verdict is Verdict.EQUIVALENT


@pytest.mark.parametrize(
    "left,right",
    [
        # An anchor that cannot hold — `^` not at the start, `$` not at the
        # end — matches nothing, exactly as Python's engine treats it. The
        # parser used to fold it away and declare `a^` equivalent to `a`.
        (r"a^", r"a"),
        (r"$a", r"a"),
        (r"a^b", r"ab"),
        (r"a|b^", r"a|b"),
        (r"$a$", r"a"),
        (r"^a^", r"a"),
        (r"a$b", r"ab"),
        (r"(a|$b)", r"a|b"),
        # An anchor inside a group is positioned by the surrounding text, so
        # `x(^a)` can never match: `^` needs absolute position zero.
        (r"x(^a)", r"xa"),
        (r"(a$)x", r"ax"),
        (r"x(a|^b)", r"x(a|b)"),
        # A repetition after the first cannot place `^` at position zero.
        (r"(^a){2}", r"aa"),
        (r"(^a){3}", r"aaa"),
        (r"(a$){2}", r"aa"),
        (r"(a$){3}", r"aaa"),
        # At most one iteration can carry the anchor, so two anchored
        # alternatives are no better than one: both branches would need to
        # start at position zero.
        (r"(^a|^b){2}", r"a|b"),
    ],
)
def test_anchors_that_cannot_hold_match_nothing(left, right):
    result = equivalent(left, right)
    assert result.verdict is Verdict.DIFFERENT, f"{left} vs {right}: {result.reason}"
    assert result.witness is not None
    in_left = re.fullmatch(left, result.witness) is not None
    in_right = re.fullmatch(right, result.witness) is not None
    assert in_left != in_right, (
        f"witness {result.witness!r} does not distinguish {left} from {right}"
    )


@pytest.mark.parametrize(
    "left,right",
    [
        # Anchors at branch boundaries are real anchors, so these are valid
        # patterns — `a|^b` is `a` or `b` at position zero, `(^a)` is `a`.
        (r"(^a)", r"a"),
        (r"(a$)", r"a"),
        (r"a|^b", r"a|b"),
        (r"(a$|b)", r"a|b"),
        (r"a|$", r"a?"),
        # A nullable prefix may shrink to the empty string, so the anchor
        # still holds: `a?^c` is `c`, `a*^b` is `b`, `b$a*` is `b`.
        (r"a?^c", r"c"),
        (r"a*^b", r"b"),
        (r"b$a*", r"b"),
        (r"^^", r""),
        (r"$^", r""),
        (r"a?^", r""),
        # Inside a repetition only one iteration can ever carry the anchor,
        # and the rest match the empty string: `(^a)*` is `a?`.
        (r"(^a)*", r"a?"),
        (r"(^a?){2}", r"a?"),
        (r"(a$)*", r"a?"),
        (r"(a?$)*", r"a?"),
        (r"(^a$)*", r"a?"),
        (r"(a$)+", r"a"),
        (r"(^a)+", r"a"),
        (r"x(^a)*", r"x"),
        (r"x(^a)*y", r"xy"),
        # With an exact count of two, the anchored iteration must be the first
        # and the rest empty: `(^a?$){2}` is the empty pattern, `(a?$){2}` is
        # `a?` (the `$` can ride the empty second iteration).
        (r"(^a?$){2}", r""),
        (r"(^(^a?$)$){2}", r""),
        (r"((^a?)$){2}", r""),
        (r"(^(a?$)){2}", r""),
        (r"(a?$){2}", r"a?"),
        # A nullable body repeats freely around the anchored iteration: the
        # `^`-bearing one is first, the `$`-bearing one last.
        (r"(^a?$)*", r"a?"),
        (r"(^(^a?){1,2})*", r"a?"),
        (r"(^(^a?){1,2})+", r"a?"),
        # A nested `^` demands that the text before the group actually be
        # empty, not merely nullable: `a?(^a)` is `a`, not `a?a`.
        (r"a?(^a)", r"a"),
        (r"(a$)(b?)", r"a"),
        (r"a?(^a?$)", r"a?"),
        (r"a?(^a?$)(b?)", r"a?"),
    ],
)
def test_anchors_that_can_hold_are_still_real_anchors(left, right):
    """The anchor side must not collapse to the unanchored pattern.

    `(^a)*` is `a?`, not `a*`: a second iteration could not place `^` at the
    start of the string. And `a*^b` is `b`, not `a*b` — the anchor forbids a
    non-empty prefix.
    """
    result = equivalent(left, right)
    assert result.verdict is Verdict.EQUIVALENT, f"{left} vs {right}: {result.reason}"


def test_assertions_survive_an_intersection_forced_to_the_empty_string():
    r"""The empty-string part of `((\B\B)&(\b))` still carries both boundaries.

    `\B\B` and `\b` cannot both hold at one position, so the intersection is
    the empty language — including at position zero, where `\B` fails. The
    parser used to collapse the intersection to the empty string, which turned
    `x*((\B\B)&(\b))` into `x*` and wrongly matched "".
    """
    result = equivalent(r"x*((\B\B)&(\b))", r"\d+a|b*a|b", dialect=Dialect.BRICS)
    assert result.verdict is Verdict.DIFFERENT
    assert result.witness is not None
    assert result.witness != ""
    in_left = re.fullmatch(r"x*(?=(?:\B\B)$)(?:\b)", result.witness) is not None
    in_right = re.fullmatch(r"\d+a|b*a|b", result.witness) is not None
    assert in_left != in_right, (
        f"witness {result.witness!r} does not distinguish the two"
    )


@pytest.mark.parametrize(
    "pattern",
    [
        r"a|^b",
        r"(a$|b)",
        r"a^",
        r"$a",
    ],
)
def test_anchors_off_the_pattern_edges_are_refused_under_search(pattern):
    """SEARCH widens patterns to `.*p.*`, which cannot respect a `^`/`$` that
    is not at the very edges of the pattern — so those are refused rather than
    mis-answered. A transparent group around the anchor is still the edge,
    which the fold below covers."""
    result = equivalent(pattern, r"a|b", semantics=Semantics.SEARCH)
    assert result.verdict is Verdict.UNSUPPORTED, f"{pattern!r}"


def test_anchors_behind_a_transparent_group_still_fold_under_search():
    # `(^a)` is `^a`: the group consumes nothing, so the anchor is the true
    # pattern edge and search must respect it.
    result = equivalent(r"(^a)", r"a|b", semantics=Semantics.SEARCH)
    assert result.verdict is Verdict.DIFFERENT
    assert result.witness is not None
    in_left = re.search(r"(^a)", result.witness) is not None
    in_right = re.search(r"a|b", result.witness) is not None
    assert in_left != in_right


def test_anchors_at_the_edges_still_survive_under_search():
    result = equivalent(r"^a", r"a", semantics=Semantics.SEARCH)
    assert result.verdict is Verdict.DIFFERENT


def test_a_trailing_anchor_under_search_binds_to_the_last_branch():
    # `a|b$` has the `$` on a true pattern edge, so the search reduction can
    # respect it: `b` is only found at the end of the subject.
    result = equivalent(r"a|b$", r"a|b", semantics=Semantics.SEARCH)
    assert result.verdict is Verdict.DIFFERENT
    assert result.witness is not None
    in_left = re.search(r"a|b$", result.witness) is not None
    in_right = re.search(r"a|b", result.witness) is not None
    assert in_left != in_right


def test_dot_excludes_newline():
    result = equivalent(r".", r"[^\n]")
    assert result.verdict is Verdict.EQUIVALENT


def test_unsupported_syntax_is_flagged_separately_from_undecidable():
    result = equivalent(r"a{2,1}", r"a")
    assert result.verdict is Verdict.UNSUPPORTED


def test_a_pattern_is_equivalent_to_itself_even_when_undecidable():
    """Reflexivity needs no automaton.

    A pattern's language is a function of its text, so identical text denotes
    identical languages — including for backreferences and lookaround, where
    nothing else here can reach a verdict. The reference tooling agrees:
    Re(gEx|DoS)Eval's DFA_Equ_Evaluation returns true on string equality before
    invoking regex_dfa_equals.jar, so matching it keeps scores comparable.
    """
    for pattern in (r"(a)\1", r"(?=a)ab", r"\bword\b", r"\d+"):
        result = equivalent(pattern, pattern)
        assert result.verdict is Verdict.EQUIVALENT, f"{pattern!r} differs from itself"
        assert result.witness is None


def test_reflexivity_holds_under_every_setting():
    for kwargs in (
        {},
        {"semantics": Semantics.SEARCH},
        {"dialect": Dialect.BRICS},
        {"semantics": Semantics.SEARCH, "dialect": Dialect.BRICS},
    ):
        assert equivalent(r"~(a)\1", r"~(a)\1", **kwargs).verdict is Verdict.EQUIVALENT


def test_an_intractable_intersection_is_refused_rather_than_attempted():
    """Equivalence with both & and ~ is non-elementary, so the guard matters.

    Sixteen intersected patterns is well inside what a corpus can contain and
    well past what determinizing can afford. It must come back UNSUPPORTED
    quickly rather than exhaust memory.
    """
    pattern = "&".join(f"(.*{c}.*)" for c in "abcdefghijklmnop")
    result = equivalent(pattern, "x", dialect=Dialect.BRICS)
    assert result.verdict is Verdict.UNSUPPORTED
    assert "too many" in result.reason


@pytest.mark.parametrize(
    "left,right,witness",
    [
        (r"\d", "[0-9]", "٣"),
        (r"\w", "[A-Za-z0-9_]", None),
        (r"\s", "[ \t\n\r\f\v]", None),
    ],
)
def test_shorthand_classes_are_unicode_aware(left, right, witness):
    """`\\d` is not `[0-9]`, and `re` is the reason.

    Python matches every Unicode digit with `\\d`, so the two are different
    languages — and `check()` runs the real `re`, so an engine that called them
    equivalent would disagree with the tool it lives in.
    """
    import re

    result = equivalent(left, right)
    assert result.verdict is Verdict.DIFFERENT
    assert result.witness is not None
    assert (re.fullmatch(left, result.witness) is not None) != (
        re.fullmatch(right, result.witness) is not None
    ), f"witness {result.witness!r} does not reproduce the difference"
    if witness is not None:
        assert result.witness == witness


# --------------------------------------------------------------------------
# Escape values: \\xHH, \\uHHHH, \\UHHHHHHHH, \\a, and octal.

@pytest.mark.parametrize(
    "left,right",
    [
        (r"\x2F", "/"),  # RegexEval 263
        (r"\x20", " "),  # RegexEval 704, 848
        (r"\x41", "A"),
        (r"\x41\x42", "AB"),
        (r"\x41\u0042\U00000043", "ABC"),
        (r"\u0041", "A"),
        (r"\U00000041", "A"),
        (r"\a", "\x07"),
        (r"\a\x07", "\x07\x07"),
        (r"\N{EM DASH}", "\u2014"),
        (r"\N{LATIN CAPITAL LETTER A}\x41", "AA"),
    ],
)
def test_hex_unicode_and_control_escapes_decode_to_their_characters(left, right):
    """`\\xHH` is one character, not the three-letter text `xHH`.

    The parser used to read `\\x41` as the literal text "x41", which made it
    equivalent to `x41` — a wrong verdict on real corpus patterns (`\\x20`,
    `\\x2F`) whose whole point is the decoded character.
    """
    result = equivalent(left, right)
    assert result.verdict is Verdict.EQUIVALENT, f"{left} vs {right}: {result.reason}"


@pytest.mark.parametrize(
    "left,right",
    [
        (r"\0", "\x00"),
        (r"\00", "\x00"),
        (r"\000", "\x00"),
        (r"\012", "\n"),
        (r"\037", "\x1f"),
        (r"\377", "\xff"),
        # Octal takes at most three digits; what follows stays literal.
        (r"\09", "\x009"),
        (r"\0123", "\n3"),
        (r"\0377", "\x1f7"),
    ],
)
def test_octal_escapes_take_up_to_three_digits(left, right):
    """`\\0123` is `\\n` + "3", not a backreference to group 123.

    The parser used to take only the `\\0` and then refuse the next digit as a
    backreference, so `\\0123` was not even analyzable.
    """
    result = equivalent(left, right)
    assert result.verdict is Verdict.EQUIVALENT, f"{left} vs {right}: {result.reason}"


@pytest.mark.parametrize(
    "left,right",
    [
        (r"[\x41]", "A"),
        (r"[\u0041]", "A"),
        (r"[\00]", "\x00"),
        (r"[\0123]", "[\n3]"),
        (r"[\12]", "[\n]"),
        (r"[\1]", "[\x01]"),
        (r"[\b]", "\x08"),  # in a class, \b is backspace
        (r"[\x41-\x43]", "[A-C]"),
        (r"[\x21-\x26]", "[!-&]"),
        (r"[^\x00-\x1f]", "[^\x00-\x1f]"),  # RegexEval 1625 style range
    ],
)
def test_escapes_decode_inside_character_classes(left, right):
    """The decoded character, not the escape text, belongs to the class."""
    result = equivalent(left, right)
    assert result.verdict is Verdict.EQUIVALENT, f"{left} vs {right}: {result.reason}"


def test_corpus_patterns_using_escapes_score_perfectly():
    """RegexEval patterns whose language is carried by `\\x20` and ranges.

    These are scored on their own match/non-match examples, so a parser that
    reads `\\x20` as "x20" fails every positive.
    """
    from regexbench import Task, check

    for pattern, positives, negatives in (
        # RegexEval 263: `\\x2F` is the date separator `/`.
        (
            r"(^((((0[1-9])|([1-2][0-9])|(3[0-1]))|([1-9]))\x2F(((0[1-9])|(1[0-2]))|([1-9]))\x2F(([0-9]{2})|(((19)|([2]([0]{1})))([0-9]{2}))))$)",
            ["31/12/2099", "1/1/1900", "10/12/2003"],
            ["05/11/3000", "11/13/2003", "32/04/2030"],
        ),
        # RegexEval 704: `\\x20` is the space in the timestamp.
        (
            r"^(19[0-9]{2}|[2-9][0-9]{3})-((0(1|3|5|7|8)|10|12)-(0[1-9]|1[0-9]|2[0-9]|3[0-1])|(0(4|6|9)|11)-(0[1-9]|1[0-9]|2[0-9]|30)|(02)-(0[1-9]|1[0-9]|2[0-9]))\x20(0[0-9]|1[0-9]|2[0-3])(:[0-5][0-9]){2}$",
            ["2004-07-12 14:25:59", "1900-01-01 00:00:00", "9999-12-31 23:59:59"],
            ["04-07-12 14:25:59", "20004-07-12 14:25", "2004/07/12 14:25:59"],
        ),
        # RegexEval 1625: `\\x00-\\x1f` and friends delimit the allowed set.
        (
            r"^[^\x00-\x1f\x21-\x26\x28-\x2d\x2f-\x40\x5b-\x60\x7b-\xff]+$",
            ["Sir. Isaac Newton", "Tom O'Leary", "hello"],
            ["Mar!y Ann", "Bob_1", "~!@#$%^&*()_+=-0987654321`{}[]"],
        ),
    ):
        report = check(pattern, Task(positives=positives, negatives=negatives))
        assert report.perfect, f"{pattern!r} failed: {report}"


# --------------------------------------------------------------------------
# Parser strictness: refuse what Python refuses, accept what it accepts.
#
# These mirror `re` on 3.11: `\q` is a "bad escape", `\b*` is "nothing to
# repeat", `a**` is "multiple repeat", and `a{2, 3}` is literal text.

@pytest.mark.parametrize(
    "pattern",
    [
        r"\q", r"\p", r"\p{L}", r"\k", r"\e", r"\c", r"\i", r"\l", r"\P",
        r"\E", r"\é",
    ],
)
def test_bad_letter_escapes_are_refused(pattern):
    """Python rejects unknown letter escapes. Reading `\\q` as literal "q"
    would flip the verdict on the escaped letter."""
    result = equivalent(pattern, "x")
    assert result.verdict is Verdict.UNSUPPORTED, result.reason


@pytest.mark.parametrize(
    "pattern",
    [
        r"[\q]", r"[\e]", r"[\B]", r"[\A]", r"[\z]", r"[\G]", r"[\p]", r"[\8]",
        r"[\9]", r"[\é]",
    ],
)
def test_bad_class_escapes_are_refused(pattern):
    """In a class too, Python rejects unknown escapes instead of matching the
    escaped letter literally; `[\\B]` is not a literal backslash-B."""
    result = equivalent(pattern, "x")
    assert result.verdict is Verdict.UNSUPPORTED, result.reason


@pytest.mark.parametrize(
    "pattern", [r"[\d-z]", r"[a-\d]", r"[\w-a]", r"[\d-\w]", r"[^\d-z]", r"[a-\q]"]
)
def test_a_shorthand_class_cannot_bound_a_range(pattern):
    """`[\\d-z]` is "bad character range" in Python — a class has no code
    point to range from — so it is refused rather than read as `\\d`, `-`, `z`.
    """
    result = equivalent(pattern, "x")
    assert result.verdict is Verdict.UNSUPPORTED, result.reason


@pytest.mark.parametrize(
    "left,right",
    [
        (r"[\D]", r"[^\d]"),
        (r"[\S]", r"[^\s]"),
        (r"[\W]", r"[^\w]"),
        (r"[^\D]", r"\d"),
    ],
)
def test_negated_class_escapes_inside_classes(left, right):
    """`[\\D]` is the same language as `[^\\d]` — a negated shorthand is legal
    inside a class, and both spellings must agree."""
    result = equivalent(left, right)
    assert result.verdict is Verdict.EQUIVALENT, result.reason


def test_negated_class_escape_must_be_the_only_member():
    """`[\\D]` is representable; `[\\D0-9]` is not a single set, so it is
    refused rather than silently dropped."""
    assert equivalent(r"[\D]", r"\d").verdict is Verdict.DIFFERENT
    assert equivalent(r"[\d\D]", "x").verdict is Verdict.UNSUPPORTED
    assert equivalent(r"[\D0-9]", "x").verdict is Verdict.UNSUPPORTED
    assert equivalent(r"[\D-a]", "x").verdict is Verdict.UNSUPPORTED


@pytest.mark.parametrize(
    "left,right",
    [
        (r"a{2,2,3}", r"a\{2,2,3\}"),
        (r"a{2, 3}", r"a\{2, 3\}"),
        (r"a{2,3 }", r"a\{2,3 \}"),
        (r"a{2,3", r"a\{2,3"),
        (r"a{2,3,}", r"a\{2,3,\}"),
        (r"a{2,3}{2, 3}", r"a{2,3}\{2, 3\}"),
        (r"\b{2, 3}", r"\b\{2, 3\}"),
    ],
)
def test_malformed_quantifier_text_is_literal_text(left, right):
    """`a{2, 3}` is not a quantifier: `re` compiles the braces as literal
    text, and the parser must not read `{2` as a bound."""
    result = equivalent(left, right)
    assert result.verdict is Verdict.EQUIVALENT, result.reason
    assert equivalent(left, "aa").verdict is Verdict.DIFFERENT, result.reason


@pytest.mark.parametrize(
    "left,right",
    [
        (r"a{,3}", r"a{0,3}"),
        (r"a{,1}", r"a?"),
        (r"a{,0}", r""),
        (r"[ab]{,2}", r"[ab]{0,2}"),
    ],
)
def test_an_omitted_lower_bound_is_zero(left, right):
    """`{,n}` is `{0,n}` in Python, not literal text — `a{,3}` matches "".

    The neighbouring `a{}` really is literal, so the two cases have to be
    told apart rather than lumped together as "malformed".
    """
    assert equivalent(left, right).verdict is Verdict.EQUIVALENT


@pytest.mark.parametrize(
    "pattern",
    [r"\b*", r"\b?", r"\b{2}", r"\B*", r"^+", r"^?", r"^*", r"$?", r"a^+", r"a\b*"],
)
def test_quantifying_a_zero_width_assertion_is_refused(pattern):
    """`\b*` is "nothing to repeat" in Python; repeating an assertion changes
    what the assertion constrains."""
    result = equivalent(pattern, "x")
    assert result.verdict is Verdict.UNSUPPORTED, result.reason


def test_assertions_inside_groups_may_be_quantified():
    """The refusal is about the bare assertion: wrapped in a group, `(\b)?`
    compiles and matches the same empty string as repeating it. (Python's
    `\\b` fails on the empty subject, so `(\b)` itself matches nothing.)"""
    assert equivalent(r"(\b)?", "").verdict is Verdict.EQUIVALENT
    assert equivalent(r"(\b)*", "").verdict is Verdict.EQUIVALENT
    assert equivalent(r"()*", "").verdict is Verdict.EQUIVALENT


@pytest.mark.parametrize(
    "pattern",
    [r"a*{3}", r"a**", r"a*?*", r"a{2}?*", r"a{0}*", r"a{2,3}?+", r"a*+?", r"a{2,3}+{2}"],
)
def test_stacking_quantifiers_is_refused(pattern):
    """`a**` is "multiple repeat" in Python, including possessive and lazy
    stacks; the second quantifier changes what the first repeats."""
    result = equivalent(pattern, "x")
    assert result.verdict is Verdict.UNSUPPORTED, result.reason


def test_lazy_quantifiers_are_transparent():
    """Laziness changes match choice, not the language, so it is always
    analyzable."""
    assert equivalent(r"a+?", r"a+").verdict is Verdict.EQUIVALENT
    assert equivalent(r"a??", r"a?").verdict is Verdict.EQUIVALENT
    assert equivalent(r"a{2}?", r"a{2}").verdict is Verdict.EQUIVALENT
    assert equivalent(r"a{2,3}?", r"a{2,3}").verdict is Verdict.EQUIVALENT


def test_group_wrapped_repeats_may_be_requantified():
    """`(a+)+` compiles in Python — the quantifier applies to the group, not
    the inner repeat — and repeats the same language."""
    assert equivalent(r"(a+)+", r"a+").verdict is Verdict.EQUIVALENT
    assert equivalent(r"(a*)*", r"a*").verdict is Verdict.EQUIVALENT
    assert equivalent(r"(a+)?", r"a*").verdict is Verdict.EQUIVALENT
    assert equivalent(r"(a?){2}", r"a?a?").verdict is Verdict.EQUIVALENT


@pytest.mark.parametrize(
    "left,right",
    [
        (r"a{2,3}+", r"a{2,3}"),
        (r"a*+", r"a*"),
        (r"a++", r"a+"),
        (r"a?+", r"a?"),
    ],
)
@needs_possessive
def test_possessive_quantifier_at_branch_end(left, right):
    """A possessive quantifier seals the repetition count, which only matters
    when later text could backtrack into it; at the end of a branch it matches
    exactly what its plain form matches."""
    result = equivalent(left, right)
    assert result.verdict is Verdict.EQUIVALENT, result.reason


@pytest.mark.parametrize(
    "pattern",
    [r"a{2,3}+b", r"(a{2,3}+)*", r"a*+b", r"(a{2,3}+|b)c", r"(a|ab){2}+", r"(a|b){2,3}+"],
)
@needs_possessive
def test_possessive_quantifier_mid_pattern_is_refused(pattern):
    """Mid-pattern, `a{2,3}+b` genuinely differs from `a{2,3}b` — the sealed
    count cannot be given back — and over an ambiguous atom even a final
    `(a|ab){2}+` differs from `(a|ab){2}`. Both are refused rather than
    mis-scored."""
    result = equivalent(pattern, "x")
    assert result.verdict is Verdict.UNSUPPORTED, result.reason


@pytest.mark.parametrize(
    "left,right",
    [
        (r"(?>a)", r"a"),
        (r"(?>a)b", r"ab"),
        (r"(?>ab)", r"ab"),
        (r"(?>(ab))+", r"(ab)+"),
    ],
)
@needs_atomic
def test_atomic_groups_with_unique_matches(left, right):
    """An atomic group only changes the language when its content could match
    several ways; content that matches exactly one way is transparent."""
    result = equivalent(left, right)
    assert result.verdict is Verdict.EQUIVALENT, result.reason


@pytest.mark.parametrize(
    "pattern",
    [r"(?>a|ab)b", r"(?>a+)b", r"(?>a|b)*", r"(?>a|ab)", r"(?>a+)", r"(?>a|b)"],
)
@needs_atomic
def test_atomic_groups_with_ambiguous_content_are_refused(pattern):
    """`(?>a|ab)` seals the alternation's greedy choice, so it fullmatches
    only "a" — not `a|ab`; `(?>a|ab)b` differs from `(a|ab)b` on "aab". Both
    are refused rather than answered with the plain-group reading."""
    result = equivalent(pattern, "x")
    assert result.verdict is Verdict.UNSUPPORTED, result.reason


@pytest.mark.parametrize("pattern", [r"(?P=a)a", r"(?P>name)", r"(a)\1"])
def test_backreferences_are_undecidable(pattern):
    """Backreferences put the language outside the regular ones, so the
    verdict is UNDECIDABLE rather than a guess — including the named and
    subroutine spellings, which used to fall through to UNSUPPORTED."""
    result = equivalent(pattern, "x")
    assert result.verdict is Verdict.UNDECIDABLE, result.reason


def test_anchor_resolution_does_not_explode_on_deep_nesting():
    """Resolving an anchor inside a concatenation picks which part carries it,
    and nested concatenations multiply those choices.

    Re(gEx|DoS)Eval's reference 2284 is fifteen alternations deep inside
    `^...$` and expands 98 nodes into 48 million — minutes of work for an
    answer nobody can use. It is refused against a node budget instead, the
    way the automata layer caps states, and the refusal is immediate.
    """
    import time

    pattern = (
        r"^(0|(\+)?[1-9]{1}[0-9]{0,8}|(\+)?[1-3]{1}[0-9]{1,9}|(\+)?[4]{1}([0-1]{1}"
        r"[0-9]{8}|[2]{1}([0-8]{1}[0-9]{7}|[9]{1}([0-3]{1}[0-9]{6}|[4]{1}([0-8]{1}"
        r"[0-9]{5}|[9]{1}([0-5]{1}[0-9]{4}|[6]{1}([0-6]{1}[0-9]{3}|[7]{1}([0-1]{1}"
        r"[0-9]{2}|[2]{1}([0-8]{1}[0-9]{1}|[9]{1}[0-5]{1}))))))))$"
    )
    started = time.monotonic()
    result = equivalent(pattern, "x")
    assert time.monotonic() - started < 10, "anchor resolution blew up again"
    assert result.verdict is Verdict.UNSUPPORTED, result.reason


@pytest.mark.parametrize(
    "pattern",
    [r"^(a|b)$", r"^((0[1-9])|(1[0-2]))$", r"^(ab|cd)$", r"^a$"],
)
def test_a_grouped_alternation_keeps_its_anchors_whole_under_search(pattern):
    """`(?:^(a|b)$)` is `^(a|b)$`, and both anchor the whole alternation.

    `_widen_for_search` distributes its wildcards over an alternation's
    branches, which is right for `^a|b$` — there the `^` binds to the first
    branch only — and wrong here. The parser folds an edge anchor to `Empty()`
    and leaves a `Concat`, so the distribution never fires; `_fold_edge_anchors`
    removed the anchor instead, collapsing to a bare `Alternate`, and
    `(?:^(a|b)$)` came back matching "aa".
    """
    wrapped = f"(?:{pattern})"
    result = equivalent(pattern, wrapped, semantics=Semantics.SEARCH)
    assert result.verdict is Verdict.EQUIVALENT, result.reason
    for text in ["", "a", "b", "aa", "ab", "01", "010", "12"]:
        assert (re.search(pattern, text) is not None) == (
            re.search(wrapped, text) is not None
        )


# --------------------------------------------------------------------------
# Python's `$` is not end-of-string
# --------------------------------------------------------------------------


DOLLAR_ALPHABET = "ab\n"
DOLLAR_TEXTS = [""] + [
    "".join(combo)
    for length in (1, 2, 3, 4)
    for combo in itertools.product(DOLLAR_ALPHABET, repeat=length)
]


@pytest.mark.parametrize(
    "left,right",
    [
        (r"b$", r"b"),
        (r"(a|\n)b$", r"(a|\n)b"),
        (r"^$", r"\n?"),
        (r"\s$", r"\s"),
        (r"^b$", r"b"),
        (r"a$", r"a"),
    ],
)
def test_search_verdicts_about_a_trailing_dollar_match_re(left, right):
    r"""`re.search("b$", "b\n")` finds a match, so the reduction must too.

    Without `re.MULTILINE`, `$` matches at the end of the subject *and* just
    before a newline that ends it. The search reduction folded a trailing `$`
    into "drop the wildcard on that end", which said the subject had to stop
    where the match did — so `(a|\n)b` and `(a|\n)b$` were reported different
    on the witness "\nb\n", which `re` matches both ways. Several of these
    pairs really are different; what the bug produced was a *wrong reason*,
    so the witness is checked against `re` rather than only the verdict.
    """
    result = equivalent(left, right, semantics=Semantics.SEARCH)
    assert result.verdict in (Verdict.EQUIVALENT, Verdict.DIFFERENT), result.reason

    def separates(text):
        return (re.search(left, text) is not None) != (
            re.search(right, text) is not None
        )

    truth = next((t for t in DOLLAR_TEXTS if separates(t)), None)
    if result.verdict is Verdict.EQUIVALENT:
        assert truth is None, f"claimed equivalent, but `re` differs on {truth!r}"
    else:
        assert result.witness is not None, "DIFFERENT without a witness"
        assert separates(result.witness), (
            f"witness {result.witness!r} does not separate them under `re`"
        )


@pytest.mark.parametrize("text", ["", "b", "b\n", "\nb\n", "b\n\n", "\n", "ab\n"])
@pytest.mark.parametrize("pattern", [r"b$", r"^b$", r"(a|\n)b$", r"\s$", r"^$"])
def test_the_search_reduction_agrees_with_re_on_newline_endings(pattern, text):
    """The reduction itself, checked against `re` on subjects ending in "\n"."""
    node, literals = parse(pattern, semantics=Semantics.SEARCH)
    alphabet = tuple(sorted(set(literals) | set(text) | {"a", "b", "\n"}))
    dfa = build_dfa(node, alphabet)
    assert dfa.accepts(text) == (re.search(pattern, text) is not None), (
        f"{pattern!r} on {text!r}"
    )


@pytest.mark.parametrize(
    "pattern",
    [r"a$\n", r"$\n", r"[ab\n]$(a|\n)", r"\s$\s", r"(?=a$)a\n", r"(a$\n)+"],
)
def test_a_dollar_before_a_possible_newline_is_refused_under_fullmatch(pattern):
    r"""Full-match verdicts cannot fold `$` when a newline may follow it.

    `re.fullmatch(r"a$\n", "a\n")` matches — the `$` sits before the subject's
    final newline — but anchor resolution folds `$` to plain end-of-string and
    made the branch the empty language, so `a$\n` came back different from
    `a\n`. Unlike the search reduction there is no wrapper to widen here, so
    the answer is refused rather than guessed.
    """
    result = equivalent(pattern, "x", semantics=Semantics.FULLMATCH)
    assert result.verdict is Verdict.UNSUPPORTED, result.reason
    assert "newline" in result.reason


@pytest.mark.parametrize(
    "pattern",
    [r"a$", r"^a$", r"\s*$", r"^(a|b)$", r"a$|b$", r"(^a$)*", r"a$b"],
)
def test_a_dollar_with_no_newline_after_it_is_still_decided(pattern):
    """The refusal above is drawn tight: a `$` nothing can follow still works.

    A trailing `$` is the overwhelmingly common shape and stays decided, and
    so does `a$b`, where the text after the anchor cannot be a newline.
    """
    result = equivalent(pattern, pattern, semantics=Semantics.FULLMATCH)
    assert result.verdict is Verdict.EQUIVALENT, result.reason


@pytest.mark.parametrize(
    "pattern,matches",
    [
        (r"a$(^)+", []),
        (r"a?$(^)+", [""]),
        (r"(a$)+$(^)+", []),
        (r"a$(b|^)", []),
        (r"a?$(b|^)", [""]),
        (r"[ab]($)(^)+", []),
        (r"a$(^)*", ["a"]),
        (r"$(^)+", [""]),
        (r"(^)+^a", ["a"]),
        (r"^(a|$)b", ["ab"]),
    ],
)
def test_an_anchor_nested_in_a_collapsed_region_still_binds(pattern, matches):
    r"""A `^` after a `$` is still a `^`, even once the `$` empties the tail.

    `a$` forces everything after it to be the empty string, and the resolver
    collapses that tail — but "empty text" is not "no constraint": the `^` in
    `a$(^)+` demands position zero, and the `a` in front of it makes that
    impossible, so `re` matches nothing. The scan that finds anchors only
    looks at the concatenation's own parts, so a `^` one group down was
    dropped with the rest of the tail and the pattern came back matching "a".

    The tail can hold an anchor whenever the middle is empty too, which is
    why `a?$(^)+` still matches the empty string.
    """
    alphabet = ("a", "b")
    texts = [""] + [
        "".join(combo)
        for length in (1, 2, 3)
        for combo in itertools.product(alphabet, repeat=length)
    ]
    assert [t for t in texts if re.fullmatch(pattern, t)] == matches, "bad expectation"

    node, _ = parse(pattern, semantics=Semantics.FULLMATCH)
    dfa = build_dfa(node, alphabet)
    assert [t for t in texts if dfa.accepts(t)] == matches
