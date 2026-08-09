"""Lookaround: parsing, regularity, and exact semantics.

Lookaround alone stays inside the regular languages, so the engine must
decide it rather than refuse it. These tests pin the semantics to Python's
``re``, which is the other half of every verdict this tool produces.
"""

import itertools
import re

import pytest

from regexbench import Semantics, Task, Verdict, check, equivalent, is_regular
from regexbench._automata import build_dfa
from regexbench._parse import parse

# Every pair here was verified against `re.fullmatch` over small alphabets:
# the two patterns describe exactly the same language.
EQUIVALENT_PAIRS = [
    (r"(?=b)b", r"b"),
    (r"a(?=b)b", r"ab"),
    (r"a(?<=a)b", r"ab"),
    (r"a(?<!x)b", r"ab"),
    (r"(?!b)*a", r"a"),
    (r"(?:(?!b).)*", r"[^b]*"),
    (r"(?=.*\d).*", r".*\d.*"),
    (r"abc(?<!x)", r"abc"),
    (r"(?=b)..", r"b."),
    (r"(?=(?=^a))a", r"a"),
    # The lookahead fires at every iteration boundary, so `(?:a(?=b))*`
    # matches only "": after one iteration consumed the 'a', the star needs
    # another 'a' where the lookahead just demanded a 'b'.
    (r"(?:a(?=b))*", r""),
    (r"(?:(?=b)b){2}", r"bb"),
    (r"(?:(?=c)c)*", r"c*"),
]


@pytest.mark.parametrize("left,right", EQUIVALENT_PAIRS)
def test_lookaround_equivalence_pairs(left, right):
    result = equivalent(left, right)
    assert result.verdict is Verdict.EQUIVALENT, f"{left} vs {right}: {result.reason}"


@pytest.mark.parametrize("left,right", EQUIVALENT_PAIRS)
def test_lookaround_pairs_agree_with_python(left, right):
    """The equivalence verdict must not be contradicted by `re` itself."""
    for n in range(4):
        for tup in itertools.product("ab", repeat=n):
            text = "".join(tup)
            in_left = re.fullmatch(left, text) is not None
            in_right = re.fullmatch(right, text) is not None
            assert in_left == in_right, f"{text!r} distinguishes {left} from {right}"


def test_lookaround_is_regular_and_decidable():
    assert is_regular(r"(?=a)b")
    assert is_regular(r"(?!a)b")
    assert is_regular(r"(?<=a)b")
    assert is_regular(r"(?<!a)b")


def test_lookaround_combined_with_backrefs_stays_undecidable():
    assert not is_regular(r"(?=(a))\1")


def test_different_lookaround_patterns_report_a_witness():
    result = equivalent(r"(?=a)b", r"a")
    assert result.verdict is Verdict.DIFFERENT
    assert result.witness is not None
    in_left = re.fullmatch(r"(?=a)b", result.witness) is not None
    in_right = re.fullmatch(r"a", result.witness) is not None
    assert in_left != in_right


def test_variable_width_lookbehind_is_refused_like_python():
    assert equivalent(r"(?<=a+)b", r"ab").verdict is Verdict.UNSUPPORTED


def test_negative_lookahead_binds_every_iteration_position():
    # `(?!b)*a` is `a` alone: the zero-width lookahead fires at the match
    # position only, and a `b` anywhere kills the one path that could finish.
    assert equivalent(r"(?!b)*a", r"a").verdict is Verdict.EQUIVALENT
    assert not re.fullmatch(r"(?!b)*a", "ba")
    assert re.fullmatch(r"(?!b)*a", "a")


def test_tempered_dot_forbids_the_marker_everywhere():
    # `(?:(?!b).)*` rejects any string containing `b`; `[^b]*` is the same set.
    result = equivalent(r"(?:(?!b).)*", r"[^b]*")
    assert result.verdict is Verdict.EQUIVALENT, result.reason
    assert re.fullmatch(r"(?:(?!b).)*", "aba") is None
    assert re.fullmatch(r"(?:(?!b).)*", "aaa") is not None


def test_lookahead_body_sees_the_rest_of_the_string():
    # The body is matched at the current position against the remaining text:
    # after the 'a' the lookahead sees "bc", so "abc" is rejected.
    assert equivalent(r"(?:a(?!b)c)*", r"(ac)*").verdict is Verdict.EQUIVALENT


def test_lookbehind_checks_text_before_the_position():
    assert equivalent(r"a(?<=a)b", r"ab").verdict is Verdict.EQUIVALENT
    assert re.fullmatch(r"(?<=a)b", "ab") is None  # nothing precedes position 0


def test_lookahead_body_can_cross_an_iteration_boundary():
    # The lookahead is evaluated against the whole remaining suffix, which
    # extends beyond the current iteration of the star: at most one iteration
    # can complete, so the language is `(?:aa)?b`.
    result = equivalent(r"(?:a(?=ab)a)*b", r"(?:aa)?b")
    assert result.verdict is Verdict.EQUIVALENT, result.reason
    assert re.fullmatch(r"(?:a(?=ab)a)*b", "aab") is not None


# Reference expressions from Re(gEx|DoS)Eval, with their own labels. These
# are search-semantics tasks, exactly as that corpus scores them.
CORPUS_TASKS = [
    Task(
        name="regexeval/156",
        semantics=Semantics.SEARCH,
        reference=r"(?!^0*$)(?!^0*\.0*$)^\d{1,5}(\.\d{1,3})?$",
        positives=["1", "12345.123", "0.5", "2", "33098", "3.280", "619.8", "91", "25461.784"],
        negatives=["0", "0.0", "123456.1234", "45.456123", "0.000", "000.000", "354/243/542"],
    ),
    Task(
        name="regexeval/251",
        semantics=Semantics.SEARCH,
        reference=r"^(?![0-9]{6})[0-9a-zA-Z]{6}$",
        positives=["123a12", "a12345", "aaaaaa", "W0lkZQ", "LMCawM", "DUcHPR", "cnzkKl"],
        negatives=["111111", "123456", "89456", "9485632", "98561", "984651", "g98456gf"],
    ),
    Task(
        name="regexeval/402",
        semantics=Semantics.SEARCH,
        reference=r"^((Bob)|(John)|(Mary)).*$(?<!White)",
        positives=["Bob Jones", "John Smith", "Mary Jane Smith", "Bob Sdsfui", "Mary Ufsdui"],
        negatives=[
            "Bob White",
            "Mary Doe White",
            "Gina Smith",
            "afdsaf.adijs",
            "Jfsu White",
            "Bob sdfjio White",
        ],
    ),
    Task(
        name="regexeval/684",
        semantics=Semantics.SEARCH,
        reference=r"^(?=.*[0-9]+.*)(?=.*[a-zA-Z]+.*)[0-9a-zA-Z]{6,}$",
        positives=["a1b2c3", "abcdefg123", "12345a", "67gyihu", "r67ty8hu", "5rft6g7y"],
        negatives=["abcdefghij", "1234567890", "jsfkdhakjdfhbjkh", "uvv7", "67f", "i9", "u8"],
    ),
    Task(
        name="regexeval/751",
        semantics=Semantics.SEARCH,
        reference=r"/\*((?!\*/).)*\*/",
        # The corpus's own label with an embedded newline is a false negative
        # under Python semantics: `.` does not match `\n`. Kept out of the
        # positives so the reference actually passes its own labels.
        positives=["/* comments */", "/***********/", "/* adsfa */", "/* f2wef23 */"],
        negatives=["// comments", "///f34fvfv", "*//2346-2345-2435", "/5/5/2*0022"],
    ),
    Task(
        name="regexeval/848",
        semantics=Semantics.SEARCH,
        reference=r"(\S+)\x20{2,}(?=\S+)",
        positives=["Too  Many spaces.", "hdfu   fhhu", "bufy.  sd fuhi", "usdfi.  siudhfi"],
        negatives=["No extra spaces", "No Extra spaces Inside", "34f2vf42e", "99999@gmail"],
    ),
]


@pytest.mark.parametrize("task", CORPUS_TASKS, ids=lambda t: t.name)
def test_corpus_lookaround_reference_passes_its_own_labels(task):
    result = check(task.reference, task)
    assert result.perfect, (
        f"{task.reference!r}: "
        f"false negatives {result.false_negatives}, false positives {result.false_positives}"
    )


def test_corpus_lookahead_is_stronger_than_the_plain_number_pattern():
    # The all-zero guard is real: "0" is rejected by the lookahead version
    # and accepted without it, so the two are not equivalent.
    guarded = r"(?!^0*$)(?!^0*\.0*$)^\d{1,5}(\.\d{1,3})?$"
    plain = r"^\d{1,5}(\.\d{1,3})?$"
    result = equivalent(guarded, plain, semantics=Semantics.SEARCH)
    assert result.verdict is Verdict.DIFFERENT
    assert result.witness is not None and re.search(plain, result.witness) is not None
    assert re.search(guarded, result.witness) is None


# --------------------------------------------------------------------------
# Regressions from the audit of the lookaround feature. Each of these was a
# wrong verdict — not a refusal — before the fix named in its docstring.


@pytest.mark.parametrize(
    "left,right",
    [
        # `_epsilon_restrict` dropped the assertion when a region collapsed to
        # the empty string, so a negative lookahead that forbids everything
        # vanished and the branch became reachable.
        (r"(?!a?)a", r"a"),
        (r"(?!)a", r"a"),
        (r"(?!(?:ab)*)a", r"a"),
    ],
)
def test_a_collapsed_region_keeps_its_lookaround(left, right):
    result = equivalent(left, right)
    assert result.verdict is Verdict.DIFFERENT, result.reason
    assert re.fullmatch(left, "a") is None and re.fullmatch(right, "a") is not None


@pytest.mark.parametrize("left,right", [(r"(?=a)^a", r"a"), (r"a$(?!b)", r"a")])
def test_a_lookaround_is_zero_width_for_anchor_resolution(left, right):
    """`_nullable` read the body's nullability, so `(?=a)` looked like a
    consuming atom and the `^` behind it collapsed the branch to nothing."""
    assert equivalent(left, right).verdict is Verdict.EQUIVALENT
    assert re.fullmatch(left, "a") is not None


@pytest.mark.parametrize(
    "left,right",
    [
        # The constraint automaton enters the body mid-string, so the body has
        # to be built for the context it actually starts in. Built once as
        # "start of string", `\ba` inside the lookbehind wrongly held.
        (r"aa(?<=\ba)", r"aa"),
        (r"aa(?<=^a)", r"aa"),
        (r"a(?=\ba)a", r"aa"),
    ],
)
def test_an_assertion_body_sees_the_position_it_fires_at(left, right):
    result = equivalent(left, right)
    assert result.verdict is Verdict.DIFFERENT, result.reason
    assert re.fullmatch(left, "aa") is None


def test_a_lookaround_nested_at_the_body_start_is_still_decided():
    """It fires where the outer one does, so chaining the markers is right."""
    assert equivalent(r"(?=(?=^a))a", r"a").verdict is Verdict.EQUIVALENT
    assert equivalent(r"(?=(?!b))a", r"a").verdict is Verdict.EQUIVALENT


@pytest.mark.parametrize(
    "pattern",
    [
        r"(?=(?!b)a)",
        r"(?=(?!b)a)a",
        r"(?=(?=a)a)a",
        r"(?=(?![0-9])a)",
        r"(?=(?=aa)a)aa",
        r"(?=(?=a)(?=aa))aa",
        r"(?=(?<=a)b)",
        r"a(?=(?<=a)b)b",
    ],
)
def test_chained_markers_fire_in_series_not_as_alternatives(pattern):
    r"""Every assertion on a chained edge has to be certified, not one of them.

    Each marker in the chain was linked `start -> accept` on its own, which
    makes them *alternatives*: a run fires exactly one, and every other
    constraint machine sees no firing at all — which it reads as vacuously
    satisfied. `(?=(?!b)a)` therefore matched the empty string, because the
    run fired the inner marker and nothing ever asked whether an "a" followed.

    Found on 495,135 Stack Overflow patterns; the pairwise differential could
    not see it, because both patterns in a pair go wrong the same way.
    """
    alphabet = ("a", "b", "0")
    texts = [""] + [
        "".join(combo)
        for length in (1, 2, 3)
        for combo in itertools.product(alphabet, repeat=length)
    ]
    node, _ = parse(pattern, semantics=Semantics.FULLMATCH)
    dfa = build_dfa(node, alphabet)
    assert [t for t in texts if dfa.accepts(t)] == [
        t for t in texts if re.fullmatch(pattern, t)
    ]


@pytest.mark.parametrize(
    "pattern",
    [
        # De Morgan: `(?!X Y)` fails when the *conjunction* fails, but chaining
        # the markers asks that neither conjunct hold. `(?!(?!a))a` matches
        # "a" under `re` and matched nothing here.
        r"(?!(?!a))",
        r"(?!(?!a))a",
        r"(?!(?=a))",
        r"(?<!(?<!a)b)c",
        # A lookbehind's body ends where the assertion fires and begins a
        # body's width earlier, so a nested marker on the outer's edge is
        # certified at the wrong position: `(?<=(?<=a)b)` rejected "ab".
        r"(?<=(?<=a)b)",
        r"(?<=(?<!a)b)",
        r"ab(?<=(?<=a)b)",
        r"(?<=(?!a)b)c",
        # The chain certifies a nested assertion whenever the outer fires, so
        # one the body can skip is one it over-enforces: `re` may take the
        # branch where `(?![0-9])?` never runs.
        r"(?=(?![0-9])?a)",
        r"(?=(?=a)*b)",
        r"(?=(?=a)|(?=b))",
        r"(?=(?=a)?b)",
    ],
)
def test_nesting_the_chain_cannot_represent_is_refused(pattern):
    """Chaining is sound only inside a positive lookahead.

    That is the one case where the nested assertion fires where the outer one
    does *and* the outer's condition is a conjunction the chain can take
    apart. Everything else is refused rather than certified at the wrong
    position or with the negation on the wrong side.
    """
    result = equivalent(pattern, "x")
    assert result.verdict is Verdict.UNSUPPORTED, result.reason
    assert "nested" in result.reason


@pytest.mark.parametrize("pattern", [r"(?=a(?=b))ab", r"(?<=a(?=b))b"])
def test_a_lookaround_nested_past_the_body_start_is_refused(pattern):
    """The inner assertion fires a character into the body, not where the
    outer one does, so its marker cannot ride the outer's edge."""
    assert equivalent(pattern, "x").verdict is Verdict.UNSUPPORTED


@pytest.mark.parametrize("pattern", [r"\b(?=a)a", r"\B(?=a)", r"(\b(?=a))*"])
def test_a_boundary_immediately_before_a_lookaround_is_refused(pattern):
    """A marker fires before any character is consumed, and a `\\b` is only
    crossed while consuming one, so the boundary could never be crossed and
    the branch silently matched nothing."""
    assert equivalent(pattern, "x").verdict is Verdict.UNSUPPORTED


@pytest.mark.parametrize("left,right", [(r"(?=a)\ba", r"a"), (r"\ba(?=b)b", r"ab")])
def test_a_boundary_elsewhere_around_a_lookaround_still_decides(left, right):
    """Only the boundary-then-marker order is broken; the refusal must not
    swallow the orders that work."""
    assert equivalent(left, right).verdict is Verdict.EQUIVALENT


def test_folding_both_edge_anchors_away_does_not_crash():
    """`_fold_edge_anchors` emptied its parts list and built `Concat(())`,
    which `_emit` indexed with `parts[-1]`."""
    result = equivalent("(^)($)", "a", semantics=Semantics.SEARCH)
    assert result.verdict is Verdict.DIFFERENT


def test_a_lookaround_inside_a_brics_operand_is_refused():
    """The operand is entered through a context gate that cannot carry the
    preceding text a lookbehind needs."""
    from regexbench import Dialect

    result = equivalent(r"a(((?<=a)b)&(b))", "ab", dialect=Dialect.BRICS)
    assert result.verdict is Verdict.UNSUPPORTED
