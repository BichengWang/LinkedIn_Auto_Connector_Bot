"""Profile visits: open people's profiles as the signed-in member so they see you in "Who viewed your profile".

LinkedIn records a view when a signed-in member's browser renders the profile page, and shows the viewer's
name only when the member's "Profile viewing options" visibility is set to name and headline. A bare HTTP
call to LinkedIn's internal API does not reliably register a view, so visits go through the real browser.

A local history (SQLite, outside the repo) keeps each person from being viewed again within
`visits.revisit_days`, so repeated runs over the same search reach new people instead of the same page.
"""

from __future__ import annotations

import logging
import random
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

from .config import Action, VisitSettings
from .session import BASE_URL, ElementMissing, LinkedInError, Session

log = logging.getLogger(__name__)

__all__ = [
    "VisitStatus",
    "VisitResult",
    "VisitHistory",
    "profile_key",
    "profile_url",
    "visit_profiles",
    "visit_from_search",
]

SEARCH_CARD = "//main//*[@role='listitem' or self::li][.//a[contains(@href, '/in/')]]"
CARD_PROFILE_LINK = ".//a[contains(@href, '/in/')]"
# Pages LinkedIn sends a signed-in member to instead of a profile when it wants verification or a login.
BLOCKING_PATHS = ("/checkpoint/", "/authwall", "/login", "/uas/login")


class VisitStatus(StrEnum):
    VISITED = "visited"
    SKIPPED = "skipped"  # viewed within visits.revisit_days
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class VisitResult:
    key: str
    status: VisitStatus
    name: str = ""
    detail: str = ""

    @property
    def url(self) -> str:
        return profile_url(self.key)

    def line(self) -> str:
        detail = f"  ({self.detail})" if self.detail else ""
        return f"{self.status.value:>11}  {self.name or self.key}  {self.url}{detail}"


def profile_key(value: str) -> str:
    """Canonical profile id from a profile URL or a bare username, URL-decoded.

    `https://www.linkedin.com/in/Jane-Doe/?miniProfileUrn=…`, `linkedin.com/in/jane-doe` and `jane-doe`
    all give `jane-doe`. Vanity usernames are case-insensitive and get lower-cased; member-id URLs
    (`/in/ACoAA…`, common in search results) are case-sensitive and kept as they are.
    Raises ValueError for anything that is not a person's profile.
    """
    text = value.strip()
    if not text:
        raise ValueError("empty profile")
    if "/" not in text and "." not in text:
        slug = text
    else:
        parsed = urlparse(text if "://" in text else f"https://{text}")
        host = (parsed.hostname or "").lower()
        parts = [part for part in parsed.path.split("/") if part]
        if not (host == "linkedin.com" or host.endswith(".linkedin.com")) or len(parts) < 2 or parts[0] != "in":
            raise ValueError(f"not a LinkedIn profile URL (expected linkedin.com/in/<username>): {value}")
        slug = parts[1]
    slug = unquote(slug).strip()
    if not slug.startswith("ACo"):
        slug = slug.lower()
    if not slug or any(char in slug for char in "/?#") or any(char.isspace() for char in slug):
        raise ValueError(f"not a LinkedIn username: {value}")
    return slug


def profile_url(key: str) -> str:
    return f"{BASE_URL}/in/{key}/"


class VisitHistory:
    """When each profile was last viewed by this tool. Holds personal data: keep it out of the repo."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS visits (key TEXT PRIMARY KEY, name TEXT NOT NULL, visited_at TEXT NOT NULL)"
        )

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> VisitHistory:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def last_visit(self, key: str) -> datetime | None:
        row = self._db.execute("SELECT visited_at FROM visits WHERE key = ?", (key,)).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def recently_visited(self, key: str, within_days: int, now: datetime | None = None) -> datetime | None:
        """The last visit if it falls inside the last `within_days` days, else None. 0 days never skips."""
        last = self.last_visit(key)
        if last is None or within_days <= 0:
            return None
        now = now or datetime.now(timezone.utc)
        return last if now - last < timedelta(days=within_days) else None

    def record(self, key: str, name: str, when: datetime | None = None) -> None:
        when = when or datetime.now(timezone.utc)
        with self._db:
            self._db.execute(
                "INSERT INTO visits (key, name, visited_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET name = excluded.name, visited_at = excluded.visited_at",
                (key, name, when.isoformat(timespec="seconds")),
            )


def visit_profiles(
    session: Session, keys: list[str], history: VisitHistory, settings: VisitSettings, again: bool = False
) -> list[VisitResult]:
    """View each profile once, in order, until the per-run visit cap is used up."""
    results: list[VisitResult] = []
    for key in keys:
        if session.remaining(Action.VISIT) <= 0:
            log.info("Per-run visit cap reached (%d).", session.settings.limits[Action.VISIT])
            break
        result = _visit_one(session, key, history, settings, again)
        results.append(result)
        log.info("%s", result.line())
    return results


def visit_from_search(
    session: Session, search_url: str, history: VisitHistory, settings: VisitSettings, max_pages: int = 5
) -> list[VisitResult]:
    """View people from a people-search result, page by page, skipping anyone viewed recently.

    Every page's profile links are read before leaving it, and the next page is opened through the URL's
    `page` parameter, because visiting a profile navigates away from the results.
    """
    if "/search/results/people" not in urlparse(search_url).path:
        raise ValueError("visit search needs a people-search URL (linkedin.com/search/results/people/?…)")
    results: list[VisitResult] = []
    seen: set[str] = set()
    first_page = int(parse_qs(urlparse(search_url).query).get("page", ["1"])[0])
    for page in range(first_page, first_page + max_pages):
        if session.remaining(Action.VISIT) <= 0:
            break
        session.open(_with_page(search_url, page))
        _raise_if_blocked(session)
        session.find_optional(SEARCH_CARD, timeout=10)
        session.scroll_to_bottom(times=2)
        keys = [key for key in _search_keys(session) if key not in seen]
        if not keys:
            log.info("Search page %d has no new profiles; stopping.", page)
            break
        seen.update(keys)
        fresh = [key for key in keys if history.recently_visited(key, settings.revisit_days) is None]
        log.info("Search page %d: %d profile(s), %d not viewed recently.", page, len(keys), len(fresh))
        results += visit_profiles(session, fresh, history, settings)
    return results


def _visit_one(session: Session, key: str, history: VisitHistory, settings: VisitSettings, again: bool) -> VisitResult:
    if not again:
        last = history.recently_visited(key, settings.revisit_days)
        if last is not None:
            return VisitResult(key, VisitStatus.SKIPPED, detail=f"viewed {last.date().isoformat()}")
    session.spend(Action.VISIT)
    session.open(profile_url(key))
    _raise_if_blocked(session)
    if "/in/" not in urlparse(session.driver.current_url or "").path:
        return VisitResult(key, VisitStatus.UNAVAILABLE, detail=f"LinkedIn redirected to {session.driver.current_url}")
    try:
        name = session.heading()
    except ElementMissing:
        return VisitResult(key, VisitStatus.UNAVAILABLE, detail="profile page did not load")
    _dwell(session, settings)
    history.record(key, name)
    return VisitResult(key, VisitStatus.VISITED, name=name)


def _dwell(session: Session, settings: VisitSettings) -> None:
    """Stay on the profile for a random, reader-like time, scrolling down in a few steps."""
    deadline = time.monotonic() + random.uniform(settings.min_dwell, settings.max_dwell)
    while (left := deadline - time.monotonic()) > 0:
        session.driver.execute_script(f"window.scrollBy(0, {random.randint(300, 800)});")
        time.sleep(min(left, random.uniform(1.5, 4.0)))


def _raise_if_blocked(session: Session) -> None:
    path = urlparse(session.driver.current_url or "").path
    if path.startswith(BLOCKING_PATHS):
        raise LinkedInError(
            f"LinkedIn sent the browser to {path} (sign-in or verification); finish it in the window, then rerun"
        )


def _search_keys(session: Session) -> list[str]:
    keys: list[str] = []
    for card in session.find_all(SEARCH_CARD):
        link = session.find_optional(CARD_PROFILE_LINK, timeout=0, root=card)
        try:
            key = profile_key(link.get_attribute("href") or "") if link is not None else None
        except ValueError:
            continue
        if key and key not in keys:
            keys.append(key)
    return keys


def _with_page(url: str, page: int) -> str:
    parsed = urlparse(url)
    query = {name: values[-1] for name, values in parse_qs(parsed.query).items()}
    query["page"] = str(page)
    return urlunparse(parsed._replace(query=urlencode(query)))

