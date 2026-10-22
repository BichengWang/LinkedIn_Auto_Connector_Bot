from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from linkedin_interaction.config import JobSearchSettings, MatchingSettings
from linkedin_interaction.job_matching import (
    Job,
    KeywordScore,
    Match,
    RerankError,
    build_search_url,
    keyword_score,
    load_matches,
    parse_rerank_output,
    save_matches,
    select_for_applying,
    term_in_text,
)


def test_search_url_includes_geo_id_and_preserves_filters() -> None:
    search = JobSearchSettings(
        keywords="Software Engineer", location="Germany", geo_id="101282230",
        easy_apply_only=True, remote_only=True, posted_within="week",
    )
    url = urlparse(build_search_url(search, start=25))
    assert url.path == "/jobs/search/"
    assert parse_qs(url.query) == {
        "keywords": ["Software Engineer"], "location": ["Germany"], "geoId": ["101282230"],
        "f_AL": ["true"], "f_WT": ["2"], "f_TPR": ["r604800"], "start": ["25"],
    }


def test_search_url_omits_empty_geo_id() -> None:
    query = parse_qs(build_search_url(JobSearchSettings(location="Germany")).split("?", 1)[1])
    assert query["location"] == ["Germany"]
    assert "geoId" not in query


@pytest.fixture
def job() -> Job:
    return Job("123", "https://www.linkedin.com/jobs/view/123/", "Software Engineer", "Example", "Remote", True)


@pytest.mark.parametrize(("term", "text", "expected"), [
    ("go", "Google engineer", False),
    ("go", "Go developer", True),
    ("ml", "HTML specialist", False),
    ("ml", "Applied ML engineer", True),
    ("c++", "Develop in C++.", True),
    ("c++", "C+++ is not C", False),
    ("c#", "C# developer", True),
    ("c#", "C## developer", False),
    (".net", ".NET services", True),
    (".net", "asp.net developer", False),
    ("node.js", "Node.js developer", True),
    ("node.js", "nodeXjs developer", False),
    ("python", "Experience with Python.", True),
    ("PYTHON", "python programming", True),
    ("go", "django engineer", False),
])
def test_technology_term_boundaries(term: str, text: str, expected: bool) -> None:
    assert term_in_text(term, text) is expected


def test_exclusion_wins_over_matching_skills(job: Job) -> None:
    scored = keyword_score(replace(job, description="Python required. Security clearance required."),
                           MatchingSettings(skills={"Python": 1}, exclude=("clearance", "intern")))
    assert scored.score == 0
    assert scored.excluded_by == ("clearance",)
    assert scored.matched_skills == ("Python",)


def test_weighted_coverage_and_skill_order(job: Job) -> None:
    matching = MatchingSettings(skills={"SQL": 1, "Python": 3, "Rust": 6})
    scored = keyword_score(replace(job, description="Python and SQL"), matching)
    assert scored.score == pytest.approx(0.4)
    assert scored.matched_skills == ("SQL", "Python")
    assert not scored.title_match


def test_title_matches_only_title_not_description(job: Job) -> None:
    matching = MatchingSettings(titles=("Data Scientist",))
    scored = keyword_score(replace(job, description="Collaborate with a Data Scientist"), matching)
    assert scored.score == 0
    assert not scored.title_match
    title_scored = keyword_score(replace(job, title="Senior Data Scientist"), matching)
    assert title_scored.score == 1
    assert title_scored.title_match


@pytest.mark.parametrize(("titles", "skills", "expected"), [
    (("Software Engineer",), {"Python": 1, "Rust": 1}, 0.65),
    (("Data Scientist",), {"Python": 1, "Rust": 1}, 0.35),
    ((), {"Python": 1, "Rust": 1}, 0.5),
    (("Software Engineer",), {}, 1.0),
    (("Data Scientist",), {}, 0.0),
    ((), {}, 1.0),
])
def test_keyword_formula_branches(job: Job, titles: tuple[str, ...], skills: dict[str, float],
                                  expected: float) -> None:
    scored = keyword_score(replace(job, description="Python"), MatchingSettings(titles=titles, skills=skills))
    assert scored.score == pytest.approx(expected)


def test_parse_valid_rerank_envelope() -> None:
    envelope = {"is_error": False, "result": "text is not the structured answer", "structured_output": {
        "score": 81, "reasons": ["Relevant experience", "Python"], "gaps": ["No Go experience"],
    }}
    assert parse_rerank_output(json.dumps(envelope)) == (
        81, ("Relevant experience", "Python"), ("No Go experience",),
    )


def test_parse_rerank_error_reports_reason() -> None:
    with pytest.raises(RerankError, match="usage limit"):
        parse_rerank_output(json.dumps({"is_error": True, "result": "usage limit"}))


@pytest.mark.parametrize("structured", [None, "not a dict", {},
    {"score": True, "reasons": [], "gaps": []},
    {"score": 80.5, "reasons": [], "gaps": []},
    {"score": 80, "reasons": "reason", "gaps": []},
    {"score": 80, "reasons": [], "gaps": [42]},
])
def test_parse_rejects_invalid_structured_output(structured: object) -> None:
    with pytest.raises(RerankError):
        parse_rerank_output(json.dumps({"is_error": False, "structured_output": structured}))


def test_parse_requires_structured_output() -> None:
    with pytest.raises(RerankError, match="structured_output"):
        parse_rerank_output(json.dumps({"is_error": False, "result": '{"score": 99}'}))


@pytest.mark.parametrize(("score", "expected"), [(-10, 0), (101, 100)])
def test_parse_clamps_scores(score: int, expected: int) -> None:
    envelope = {"is_error": False, "structured_output": {"score": score, "reasons": [], "gaps": []}}
    assert parse_rerank_output(json.dumps(envelope)) == (expected, (), ())


def test_matches_round_trip_all_fields(job: Job, tmp_path: Path) -> None:
    matches = [
        Match(replace(job, description="Python — distributed systems"),
              KeywordScore(0.75, ("Python", "SQL"), True, ()), 86,
              ("Relevant systems experience",), ("No Go",)),
        Match(replace(job, id="124", url="https://www.linkedin.com/jobs/view/124/", easy_apply=False),
              KeywordScore(0, (), False, ("clearance",))),
    ]
    path = tmp_path / "matches.json"
    save_matches(matches, path)
    assert load_matches(path) == matches


def test_select_easy_apply_and_keyword_fallback(job: Job) -> None:
    fallback = Match(job, KeywordScore(0.805, ("Python",), False, ()))
    reranked = Match(replace(job, id="124"), KeywordScore(0.4, (), False, ()), 90)
    external = Match(replace(job, id="125", easy_apply=False), KeywordScore(1, (), True, ()), 100)
    below = Match(replace(job, id="126"), KeywordScore(1, (), True, ()), 60)
    unranked_below = Match(replace(job, id="127"), KeywordScore(0.3, (), False, ()))
    assert select_for_applying([fallback, below, external, reranked, unranked_below], 70) == [reranked, fallback]
    assert fallback.score == round(0.805 * 100)
    assert select_for_applying([fallback], fallback.score) == [fallback]
