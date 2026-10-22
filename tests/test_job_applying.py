"""Pure answer matching and terminal approval tests; no browser is started."""
from __future__ import annotations

import pytest

from linkedin_interaction.job_applying import TerminalReviewer, find_answer


def test_find_answer_longest_key_wins() -> None:
    answers = {"years of experience": "5", "years of experience with python": "3"}
    assert find_answer("How many years of experience with Python do you have?", answers) == "3"


def test_find_answer_normalises_case_and_whitespace() -> None:
    assert find_answer("How MANY\n years   OF experience?", {"  Years\tOF  Experience ": "7"}) == "7"


def test_find_answer_normalised_length_determines_specificity() -> None:
    answers = {"years                    experience": "general", "python years experience": "specific"}
    assert find_answer("Python years experience", answers) == "specific"


def test_find_answer_no_match() -> None:
    assert find_answer("Are you authorised to work here?", {"salary": "100000"}) is None


def test_find_answer_empty_answers() -> None:
    assert find_answer("Years of experience?", {}) is None


def test_find_answer_ignores_empty_keys() -> None:
    assert find_answer("Years of experience?", {" \t": "not an answer"}) is None


@pytest.mark.parametrize("response", ["y", "yes", "Y", "YES", " yes "])
def test_terminal_reviewer_approves_only_explicit_yes(monkeypatch: pytest.MonkeyPatch, response: str) -> None:
    monkeypatch.setattr("builtins.input", lambda _: response)
    assert TerminalReviewer().approve("Engineer", "Example", [("Experience", "5")]) is True


@pytest.mark.parametrize("response", ["", "n", "no", "anything", "yeah", "true", "1"])
def test_terminal_reviewer_declines_other_answers(monkeypatch: pytest.MonkeyPatch, response: str) -> None:
    monkeypatch.setattr("builtins.input", lambda _: response)
    assert TerminalReviewer().approve("Engineer", "Example", []) is False


@pytest.mark.parametrize("response, expected", [("", True), ("s", False), (" S ", False)])
def test_terminal_reviewer_manual_fill(monkeypatch: pytest.MonkeyPatch, response: str, expected: bool) -> None:
    monkeypatch.setattr("builtins.input", lambda _: response)
    assert TerminalReviewer().fill_manually("Engineer", ["Work authorisation"]) is expected
