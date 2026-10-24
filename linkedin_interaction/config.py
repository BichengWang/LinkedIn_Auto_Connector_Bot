"""Settings loaded from a TOML file (see config.example.toml); credentials come from the environment."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from enum import StrEnum
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("config.toml")


class Action(StrEnum):
    """Actions with a per-run cap. Values are the keys of the [limits] table."""

    CONNECTION = "connections"
    MESSAGE = "messages"
    FOLLOW = "follows"
    REACTION = "reactions"
    COMMENT = "comments"
    POST = "posts"
    APPLICATION = "applications"
    VISIT = "visits"  # profile views: not a write, but LinkedIn restricts accounts that view too many


DEFAULT_LIMITS: dict[Action, int] = {
    Action.CONNECTION: 20,
    Action.MESSAGE: 10,
    Action.FOLLOW: 20,
    Action.REACTION: 30,
    Action.COMMENT: 10,
    Action.POST: 2,
    Action.APPLICATION: 10,
    Action.VISIT: 40,
}


@dataclass(frozen=True)
class BrowserSettings:
    name: str = "chrome"  # "chrome" or "firefox"
    profile_dir: Path | None = None  # None -> ~/.linkedin-interaction/<name>-profile
    binary: Path | None = None  # None -> let Selenium Manager locate the browser
    headless: bool = False

    @property
    def resolved_profile_dir(self) -> Path:
        if self.profile_dir is not None:
            return self.profile_dir
        return Path.home() / ".linkedin-interaction" / f"{self.name}-profile"


@dataclass(frozen=True)
class Pacing:
    min_delay: float = 2.0
    max_delay: float = 6.0


@dataclass(frozen=True)
class ConnectionSettings:
    search_url: str = ""
    note: str = ""  # empty -> send invitations without a note


@dataclass(frozen=True)
class JobSearchSettings:
    keywords: str = ""
    location: str = ""  # place name; LinkedIn's newer jobs search ignores it, so set geo_id too
    geo_id: str = ""  # LinkedIn geoId from a jobs search URL, e.g. 103644278 = United States
    easy_apply_only: bool = True
    remote_only: bool = False
    posted_within: str = "week"  # "any", "day", "week" or "month"
    max_results: int = 50


@dataclass(frozen=True)
class MatchingSettings:
    resume: Path | None = None
    titles: tuple[str, ...] = ()
    skills: dict[str, float] = field(default_factory=dict)  # skill -> weight
    exclude: tuple[str, ...] = ()
    min_keyword_score: float = 0.3
    rerank_top: int = 15
    claude_command: str = "claude"
    claude_model: str = ""  # empty -> Claude Code's default model
    output: Path = Path("matches.json")


@dataclass(frozen=True)
class ApplyingSettings:
    min_score: int = 70
    follow_company: bool = False
    answers: dict[str, str] = field(default_factory=dict)  # question substring -> answer


@dataclass(frozen=True)
class VisitSettings:
    revisit_days: int = 30  # skip anyone this tool viewed within this many days; 0 = never skip
    min_dwell: float = 8.0  # seconds spent on each profile, scrolling
    max_dwell: float = 20.0
    history: Path = field(default_factory=lambda: Path.home() / ".linkedin-interaction" / "visits.sqlite3")


@dataclass(frozen=True)
class Settings:
    browser: BrowserSettings = field(default_factory=BrowserSettings)
    pacing: Pacing = field(default_factory=Pacing)
    limits: dict[Action, int] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    connection: ConnectionSettings = field(default_factory=ConnectionSettings)
    jobs: JobSearchSettings = field(default_factory=JobSearchSettings)
    matching: MatchingSettings = field(default_factory=MatchingSettings)
    applying: ApplyingSettings = field(default_factory=ApplyingSettings)
    visits: VisitSettings = field(default_factory=VisitSettings)
    username: str | None = None
    password: str | None = None
    cookies_file: Path | None = None  # JSON cookie export holding a signed-in session (LINKEDIN_COOKIES)


class ConfigError(ValueError):
    pass


def load_settings(path: Path | None = None) -> Settings:
    """Load settings from `path`, or from ./config.toml when it exists, else defaults."""
    if path is None:
        path = DEFAULT_CONFIG_PATH if DEFAULT_CONFIG_PATH.exists() else None
    elif not path.exists():
        raise ConfigError(f"config file not found: {path}")
    raw = tomllib.loads(path.read_text(encoding="utf-8")) if path else {}
    return parse_settings(raw)


def parse_settings(raw: dict) -> Settings:
    known = {"browser", "pacing", "limits", "connection", "jobs", "matching", "applying", "visits"}
    _reject_unknown("top level", raw, known)

    browser_raw = dict(raw.get("browser", {}))
    for key in ("profile_dir", "binary"):
        if browser_raw.get(key):
            browser_raw[key] = _path(browser_raw[key])
        else:
            browser_raw.pop(key, None)
    browser = _build(BrowserSettings, browser_raw, "browser")
    if browser.name not in ("chrome", "firefox"):
        raise ConfigError(f"browser.name must be 'chrome' or 'firefox', got {browser.name!r}")

    pacing = _build(Pacing, raw.get("pacing", {}), "pacing")
    if not 0 <= pacing.min_delay <= pacing.max_delay:
        raise ConfigError("pacing needs 0 <= min_delay <= max_delay")

    limits_raw = raw.get("limits", {})
    _reject_unknown("limits", limits_raw, {a.value for a in Action})
    limits = dict(DEFAULT_LIMITS)
    for key, value in limits_raw.items():
        if not isinstance(value, int) or value < 0:
            raise ConfigError(f"limits.{key} must be a non-negative integer")
        limits[Action(key)] = value

    jobs = _build(JobSearchSettings, raw.get("jobs", {}), "jobs")
    if jobs.posted_within not in ("any", "day", "week", "month"):
        raise ConfigError("jobs.posted_within must be one of: any, day, week, month")

    matching_raw = dict(raw.get("matching", {}))
    if matching_raw.get("resume"):
        matching_raw["resume"] = _path(matching_raw["resume"])
    else:
        matching_raw.pop("resume", None)
    if "output" in matching_raw:
        matching_raw["output"] = _path(matching_raw["output"])
    for key in ("titles", "exclude"):
        if key in matching_raw:
            matching_raw[key] = tuple(matching_raw[key])
    if "skills" in matching_raw:
        skills = matching_raw["skills"]
        if isinstance(skills, list):  # plain list -> equal weights
            skills = {name: 1.0 for name in skills}
        matching_raw["skills"] = {str(k): float(v) for k, v in skills.items()}

    applying_raw = dict(raw.get("applying", {}))
    if "answers" in applying_raw:
        applying_raw["answers"] = {str(k): str(v) for k, v in applying_raw["answers"].items()}

    visits_raw = dict(raw.get("visits", {}))
    if visits_raw.get("history"):
        visits_raw["history"] = _path(visits_raw["history"])
    else:
        visits_raw.pop("history", None)
    visits = _build(VisitSettings, visits_raw, "visits")
    if not isinstance(visits.revisit_days, int) or visits.revisit_days < 0:
        raise ConfigError("visits.revisit_days must be a non-negative integer")
    if not 0 <= visits.min_dwell <= visits.max_dwell:
        raise ConfigError("visits needs 0 <= min_dwell <= max_dwell")

    return Settings(
        browser=browser,
        pacing=pacing,
        limits=limits,
        connection=_build(ConnectionSettings, raw.get("connection", {}), "connection"),
        jobs=jobs,
        matching=_build(MatchingSettings, matching_raw, "matching"),
        applying=_build(ApplyingSettings, applying_raw, "applying"),
        visits=visits,
        username=os.getenv("LINKEDIN_USERNAME") or None,
        password=os.getenv("LINKEDIN_PASSWORD") or None,
        cookies_file=_path(cookies) if (cookies := os.getenv("LINKEDIN_COOKIES")) else None,
    )


def _build(cls, raw: dict, section: str):
    _reject_unknown(section, raw, {f.name for f in fields(cls)})
    return cls(**raw)


def _reject_unknown(section: str, raw: dict, known: set[str]) -> None:
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"unknown key(s) in {section}: {', '.join(sorted(unknown))}")


def _path(value: str) -> Path:
    return Path(value).expanduser()
