"""Lookaround: parsing, regularity, and exact semantics.

Lookaround alone stays inside the regular languages, so the engine must
decide it rather than refuse it. These tests pin the semantics to Python's
``re``, which is the other half of every verdict this tool produces.
"""

import itertools
import re

import pytest

from regexbench import Semantics, Task, Verdict, check, equivalent, is_regular

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
