import tomllib
from pathlib import Path

import pytest

from linkedin_interaction.config import DEFAULT_LIMITS, Action, ConfigError, parse_settings

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.toml"


def test_example_config_parses():
    settings = parse_settings(tomllib.loads(EXAMPLE.read_text()))
    assert settings.matching.skills["c++"] == 1.0
    assert settings.applying.answers["sponsorship"] == "No"
    assert settings.matching.resume == Path("~/Documents/resume.pdf").expanduser()


def test_defaults_without_config():
    settings = parse_settings({})
    assert settings.limits == DEFAULT_LIMITS
    assert settings.browser.name == "chrome"
    assert settings.browser.resolved_profile_dir.name == "chrome-profile"


@pytest.mark.parametrize(
    "raw",
    [
        {"limits": {"conections": 5}},  # typo must not silently disable a cap
        {"browser": {"nmae": "chrome"}},
        {"matchin": {}},
    ],
)
def test_unknown_keys_are_rejected(raw):
    with pytest.raises(ConfigError, match="unknown key"):
        parse_settings(raw)


def test_partial_limits_keep_other_defaults():
    settings = parse_settings({"limits": {"connections": 5}})
    assert settings.limits[Action.CONNECTION] == 5
    assert settings.limits[Action.MESSAGE] == DEFAULT_LIMITS[Action.MESSAGE]


@pytest.mark.parametrize(
    "raw",
    [
        {"limits": {"connections": -1}},
        {"limits": {"connections": 2.5}},
        {"browser": {"name": "safari"}},
        {"pacing": {"min_delay": 5, "max_delay": 1}},
        {"jobs": {"posted_within": "year"}},
        {"visits": {"revisit_days": -1}},
        {"visits": {"min_dwell": 30, "max_dwell": 5}},
        {"visits": {"revisit_day": 7}},
    ],
)
def test_invalid_values_are_rejected(raw):
    with pytest.raises(ConfigError):
        parse_settings(raw)


def test_skill_list_means_equal_weights():
    settings = parse_settings({"matching": {"skills": ["python", "sql"]}})
    assert settings.matching.skills == {"python": 1.0, "sql": 1.0}


def test_credentials_come_from_environment(monkeypatch):
    monkeypatch.setenv("LINKEDIN_USERNAME", "me@example.com")
    monkeypatch.setenv("LINKEDIN_PASSWORD", "")
    settings = parse_settings({})
    assert settings.username == "me@example.com"
    assert settings.password is None
