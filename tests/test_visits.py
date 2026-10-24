from datetime import datetime, timedelta, timezone

import pytest

from linkedin_interaction.visits import VisitHistory, profile_key


@pytest.mark.parametrize(
    "value",
    [
        "jane-doe",
        "Jane-Doe",
        "https://www.linkedin.com/in/jane-doe/",
        "https://www.linkedin.com/in/Jane-Doe?miniProfileUrn=urn%3Ali%3Afs_miniProfile%3AACoAAB",
        "linkedin.com/in/jane-doe/details/experience/",
        "https://uk.linkedin.com/in/jane-doe",
    ],
)
def test_profile_variants_share_one_key(value):
    # One key per person is what keeps a search run and a URL list from viewing someone twice.
    assert profile_key(value) == "jane-doe"


def test_member_id_urls_keep_their_case():
    # /in/ACoAA… ids from search results are case-sensitive; lower-casing them opens a 404.
    assert profile_key("https://www.linkedin.com/in/ACoAABcdEFg") == "ACoAABcdEFg"


def test_percent_encoded_usernames_are_decoded():
    assert profile_key("https://www.linkedin.com/in/j%C3%B6rg-m%C3%BCller/") == "jörg-müller"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "https://www.linkedin.com/company/anthropic/",
        "https://www.linkedin.com/in/",
        "https://evil.example/in/jane-doe",
        "https://notlinkedin.com/in/jane-doe",
    ],
)
def test_non_profiles_are_rejected(value):
    with pytest.raises(ValueError):
        profile_key(value)


def test_history_skips_only_inside_the_window(tmp_path):
    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    with VisitHistory(tmp_path / "visits.sqlite3") as history:
        history.record("jane-doe", "Jane Doe", when=now - timedelta(days=29, hours=23))
        history.record("old-friend", "Old Friend", when=now - timedelta(days=30))
        assert history.recently_visited("jane-doe", 30, now=now) is not None
        assert history.recently_visited("old-friend", 30, now=now) is None
        assert history.recently_visited("stranger", 30, now=now) is None
        assert history.recently_visited("jane-doe", 0, now=now) is None  # 0 = always revisit


def test_history_persists_and_keeps_the_latest_visit(tmp_path):
    path = tmp_path / "visits.sqlite3"
    first = datetime(2026, 9, 1, tzinfo=timezone.utc)
    later = datetime(2026, 10, 20, tzinfo=timezone.utc)
    with VisitHistory(path) as history:
        history.record("jane-doe", "Jane Doe", when=first)
        history.record("jane-doe", "Jane Doe", when=later)
    with VisitHistory(path) as history:
        assert history.last_visit("jane-doe") == later
