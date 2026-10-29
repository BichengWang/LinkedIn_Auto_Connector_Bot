#!/usr/bin/env python3
"""Ledger and guard rails for the LinkedIn outreach and catch-up rounds.

`.workflow/linkedin-daily-outreach.md` and `.workflow/linkedin-catchup.md` are
the runbooks. A round reads LinkedIn in Chrome and writes through the LinkedIn
MCP server (reactions and comments through Chrome); this ledger decides who is
due and enforces the caps, the invite pause and per-person dedupe, so nobody is
invited, warmed up, reacted to or commented on twice for the same thing even
when a run dies halfway.

Exit codes: 0 ok, 1 precheck/start says skip, 2 refused for this person, 3 stop
this kind of write for now (cap reached or invites paused).
"""

import argparse
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / ".workflow" / "linkedin-outreach.json").read_text())
TZ = ZoneInfo(CONFIG["timezone"])
HOME = Path(os.environ.get("LINKEDIN_OUTREACH_HOME", Path.home() / ".linkedin-outreach"))
SEARCH_PAGES = 100  # LinkedIn serves at most 100 pages of people-search results
LIVE = ("claimed", "sent", "unknown")  # writes that may have reached LinkedIn
AGE_DAYS = {"m": 1 / 1440, "h": 1 / 24, "d": 1, "w": 7, "mo": 30, "yr": 365, "y": 365}
THREAD_CHECK_MAX_AGE = timedelta(hours=2)  # a message needs a recent "no conversation yet" check
WINDOW = timedelta(hours=CONFIG["window_hours"])
# An unfinished round younger than this is still running; an older one died without `finish`.
ROUND_STALE = WINDOW + timedelta(hours=1)
DAILY_CAPS = {"message": "messages_per_day", "react": "reactions_per_day", "comment": "comments_per_day"}
OCCASION_KINDS = ("catchup", "react", "comment")  # deduped per person and occasion, not per person
FRONTIER = list(CONFIG["frontier_labs"].values())

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    username     TEXT PRIMARY KEY,
    name         TEXT,
    headline     TEXT,
    degree       TEXT,
    connected_on TEXT,  -- date from the Connections page
    viewed_at    TEXT,  -- latest profile view, from the profile-views page
    candidate_at TEXT,  -- queued for a cold invite by a search page
    thread_clear_at TEXT,  -- last check that found no conversation with them
    first_seen   TEXT NOT NULL,
    core         INTEGER NOT NULL DEFAULT 0,  -- works at a frontier lab (config frontier_labs)
    posts_checked_at TEXT  -- last read of their recent activity for post engagement
);
CREATE TABLE IF NOT EXISTS actions (
    id       INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    kind     TEXT NOT NULL,  -- invite | message | catchup | react | comment
    reason   TEXT NOT NULL,  -- search | viewer | connection | core | birthday | job_change | work_anniversary | education
    occasion TEXT,           -- catch-ups: e.g. birthday:2026; reactions and comments: the post key
    day      TEXT NOT NULL,
    at       TEXT NOT NULL,
    text     TEXT,
    outcome  TEXT NOT NULL,  -- claimed | sent | unknown | skipped | failed
    status   TEXT            -- LinkedIn MCP or page status
);
CREATE INDEX IF NOT EXISTS actions_person ON actions (username, kind);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
# Columns added after a ledger was first created.
MIGRATIONS = {
    "actions": {"occasion": "TEXT"},
    "people": {"core": "INTEGER NOT NULL DEFAULT 0", "posts_checked_at": "TEXT"},
}


def now():
    return datetime.now(TZ)


def today():
    return now().date()


def stamp():
    return now().isoformat(timespec="seconds")


def open_db():
    HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = sqlite3.connect(HOME / "ledger.sqlite3", isolation_level=None)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    for table, columns in MIGRATIONS.items():
        have = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        for name, declaration in columns.items():
            if name not in have:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
    return db


def get(db, key, default=None):
    row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def put(db, key, value):
    db.execute(
        "INSERT INTO meta VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=1))


def stop(code, message):
    print(message)
    raise SystemExit(code)


def used_today(db, kind):
    return db.execute(
        "SELECT count(*) FROM actions WHERE kind = ? AND day = ? AND outcome IN (?, ?, ?)",
        (kind, today().isoformat(), *LIVE),
    ).fetchone()[0]


def used_since(db, kind, since):
    rows = db.execute(
        "SELECT at FROM actions WHERE kind = ? AND day >= ? AND outcome IN (?, ?, ?)",
        (kind, (since.date() - timedelta(days=1)).isoformat(), *LIVE),
    )
    return sum(datetime.fromisoformat(row["at"]) >= since for row in rows)


def invites_used(db):
    return used_since(db, "invite", now() - WINDOW)


def invites_left(db):
    return 0 if invites_paused(db) else max(0, CONFIG["invites_per_window"] - invites_used(db))


def left_today(db, kind):
    return max(0, CONFIG[DAILY_CAPS[kind]] - used_today(db, kind))


def blocked(db, username, kind, occasion=None):
    """One write per person and kind (per occasion for catch-ups and posts); two proven failures retire it."""
    live, failed = db.execute(
        "SELECT count(*) FILTER (WHERE outcome != 'failed'), count(*) FILTER (WHERE outcome = 'failed')"
        " FROM actions WHERE username = ? AND kind = ? AND occasion IS ?",
        (username, kind, occasion),
    ).fetchone()
    return live > 0 or failed >= 2


def invites_paused(db):
    until = get(db, "invites_paused_until")
    return until if until and today().isoformat() < until else None


def upsert(db, row):
    db.execute(
        "INSERT INTO people (username, name, headline, degree, first_seen) VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT (username) DO UPDATE SET name = coalesce(excluded.name, name),"
        " headline = coalesce(excluded.headline, headline), degree = coalesce(excluded.degree, degree)",
        (row["username"], row.get("name"), row.get("headline"), row.get("degree"), now().isoformat()),
    )


def catchup_occasion(row):
    """Dedupe key of a catch-up card. Birthdays and work anniversaries come once a year."""
    if row["reason"] in ("birthday", "work_anniversary"):
        year = today().year
        # "Celebrate Joyce's recent birthday on Oct 22": a December date read in January is last year's.
        if match := re.search(r"\bon ([A-Z][a-z]{2}) (\d{1,2})\b", row.get("detail") or ""):
            month = datetime.strptime(match[1], "%b").month
            if (month, int(match[2])) > (today().month, today().day):
                year -= 1
        return f"{row['reason']}:{year}"
    return f"{row['reason']}:{row['text']}"  # the text names the new company or school


def viewed_at(text):
    match = re.search(r"(\d+)\s*(mo|yr|y|m|h|d|w)\b", text or "")
    days = int(match[1]) * AGE_DAYS[match[2]] if match else 0
    return (now() - timedelta(days=days)).isoformat(timespec="seconds")


def connections_since(db):
    """Oldest Connected-on date today's Connections scan has to reach."""
    last = get(db, "connections_scan_day")
    start = date.fromisoformat(last) if last else today()
    return start - timedelta(days=CONFIG["new_connection_lookback_days"])


def search_cursor(db, name):
    return get(db, "search_cursors", {}).get(name, {"keyword": 0, "page": 1})


def people_search_url(keyword, page, facets):
    query = {"keywords": keyword} if keyword else {}
    query["origin"] = "FACETED_SEARCH"
    for facet, values in facets.items():
        query[facet] = json.dumps(values, separators=(",", ":"))
    query["page"] = page
    return "https://www.linkedin.com/search/results/people/?" + urlencode(query)


def search_facets(search):
    facets = {facet: search[facet] for facet in ("network", "geoUrn", "industry") if facet in search}
    if search.get("frontier"):
        facets["currentCompany"] = FRONTIER
    return facets


def round_blocked(db):
    """Why a new round must not start now, or None."""
    started, finished = get(db, "round_started_at"), get(db, "round_finished_at")
    if not started:
        return None
    age = now() - datetime.fromisoformat(started)
    if (not finished or finished < started) and age < ROUND_STALE:
        return f"the round started at {started} is still running"
    if age < timedelta(hours=CONFIG["round_min_gap_hours"]):
        return f"the last round started at {started}, less than {CONFIG['round_min_gap_hours']} h ago"
    return None


def precheck(db, args):
    if reason := round_blocked(db):
        stop(1, f"skip: {reason}")
    if subprocess.run(["pgrep", "-x", "Google Chrome"], capture_output=True).returncode:
        stop(1, "Google Chrome is not running; the round reads LinkedIn through it")


def snapshot(db):
    return {
        "day": today().isoformat(),
        "round": {"started_at": get(db, "round_started_at"), "finished_at": get(db, "round_finished_at")},
        "go_live": get(db, "go_live"),
        "invites": {
            "used": invites_used(db), "cap": CONFIG["invites_per_window"], "window_hours": CONFIG["window_hours"],
            "paused_until": invites_paused(db),
        },
        **{kind + "s": {"used": used_today(db, kind), "cap": CONFIG[cap]} for kind, cap in DAILY_CAPS.items()},
        "connections_since": connections_since(db).isoformat(),
        "queued_candidates": len(queued(db, 10**6)),
        "core_pages_per_round": CONFIG["core_pages_per_round"],
        "engagements_per_round": CONFIG["engagements_per_round"],
        "post_max_age_days": CONFIG["post_max_age_days"],
    }


def status(db, args):
    emit(snapshot(db))


def start(db, args):
    if reason := round_blocked(db):
        stop(1, f"skip: {reason}")
    put(db, "round_started_at", stamp())
    emit(snapshot(db))


def ingest(db, args):
    rows = [row for row in json.load(sys.stdin) if row.get("username")]
    queued_now = 0
    if args.kind == "connections":
        if not get(db, "go_live"):
            put(db, "go_live", today().isoformat())
        for row in rows:
            if not row.get("connected_on"):
                continue
            upsert(db, {**row, "degree": "1st"})
            connected = datetime.strptime(row["connected_on"], "%B %d, %Y").date().isoformat()
            db.execute("UPDATE people SET connected_on = ? WHERE username = ?", (connected, row["username"]))
        put(db, "connections_scan_day", today().isoformat())
    elif args.kind == "viewers":
        for row in rows:
            upsert(db, row)
            db.execute(
                "UPDATE people SET viewed_at = max(coalesce(viewed_at, ''), ?) WHERE username = ?",
                (viewed_at(row.get("viewed")), row["username"]),
            )
    elif args.kind == "core":
        page = get(db, "core_page", 1) + 1
        put(db, "core_page", page if rows and page <= SEARCH_PAGES else 1)
        for row in rows:
            upsert(db, {**row, "degree": "1st"})
            db.execute("UPDATE people SET core = 1 WHERE username = ?", (row["username"],))
    else:
        search = next(s for s in CONFIG["searches"] if s["name"] == args.search)
        cursor = search_cursor(db, search["name"])
        cursor = {"keyword": cursor["keyword"], "page": cursor["page"] + 1}
        if not rows or cursor["page"] > SEARCH_PAGES:
            cursor = {"keyword": (cursor["keyword"] + 1) % len(search["keywords"]), "page": 1}
        put(db, "search_cursors", {**get(db, "search_cursors", {}), search["name"]: cursor})
        for row in rows:
            upsert(db, row)
            if search.get("frontier"):
                db.execute("UPDATE people SET core = 1 WHERE username = ?", (row["username"],))
            if row.get("action") == "connect" and not blocked(db, row["username"], "invite"):
                queued_now += db.execute(
                    "UPDATE people SET candidate_at = coalesce(candidate_at, ?) WHERE username = ? AND connected_on IS NULL",
                    (stamp(), row["username"]),
                ).rowcount
    emit({"ingested": len(rows), "queued": queued_now})


def next_search(db, args):
    """Next people-search page; a search with `weight` n gets n turns per rotation."""
    turn = get(db, "search_turn", 0)
    rotation = [search for search in CONFIG["searches"] for _ in range(search.get("weight", 1))]
    search = rotation[turn % len(rotation)]
    put(db, "search_turn", turn + 1)
    cursor = search_cursor(db, search["name"])
    keyword = search["keywords"][cursor["keyword"] % len(search["keywords"])]
    url = people_search_url(keyword, cursor["page"], search_facets(search))
    emit({"search": search["name"], "keyword": keyword, "page": cursor["page"], "url": url})


def next_core_page(db, args):
    """Next page of my 1st-degree connections who work at a frontier lab."""
    page = get(db, "core_page", 1)
    emit({"page": page, "url": people_search_url(None, page, {"network": ["F"], "currentCompany": FRONTIER})})


def post_search(db, args):
    """Posts search: the past week's posts by people LinkedIn lists at a frontier lab, newest first."""
    query = {
        "origin": "FACETED_SEARCH",
        "authorCompany": json.dumps(FRONTIER, separators=(",", ":")),
        "datePosted": json.dumps("past-week"),
        "sortBy": json.dumps("date_posted"),
    }
    emit({"url": "https://www.linkedin.com/search/results/content/?" + urlencode(query)})


def posts_due(db, args):
    """Post-search authors (JSON list on stdin) due for engagement, each recorded as a frontier-lab person.

    LinkedIn's author-company filter also returns people with no tie to a lab, so an author counts only
    when a frontier-lab people search already found them or their headline names a lab.
    """
    if not left_today(db, "react") and not left_today(db, "comment"):
        emit([])
        return
    pattern = re.compile(CONFIG["frontier_headline_pattern"], re.IGNORECASE)
    horizon = now() - timedelta(days=CONFIG["engage_person_gap_days"])
    items, seen = [], set()
    for row in json.load(sys.stdin):
        username = row.get("username")
        if not username or username in seen:
            continue
        person = db.execute("SELECT core, posts_checked_at FROM people WHERE username = ?", (username,)).fetchone()
        if not ((person and person["core"]) or pattern.search(row.get("headline") or "")):
            continue
        if person and person["posts_checked_at"] and datetime.fromisoformat(person["posts_checked_at"]) > horizon:
            continue
        seen.add(username)
        upsert(db, row)
        db.execute("UPDATE people SET core = 1 WHERE username = ?", (username,))
        items.append({"username": username, "name": row.get("name"), "headline": row.get("headline"), "reason": "core"})
    emit(items)


def queued(db, limit):
    """Cold-invite candidates, frontier-lab people first."""
    rows = db.execute(
        "SELECT username, name, headline, core FROM people WHERE candidate_at IS NOT NULL AND connected_on IS NULL"
        " ORDER BY core DESC, candidate_at, username"
    )
    return [dict(row) for row in rows if not blocked(db, row["username"], "invite")][:limit]


def candidates(db, args):
    emit(queued(db, args.limit))


def due(db, args):
    """Warm-ups owed today: recent profile viewers first, then new connections."""
    left = {"message": left_today(db, "message"), "invite": invites_left(db)}
    items, seen = [], set()

    def add(person, kind, reason):
        if person["username"] in seen or blocked(db, person["username"], kind) or left[kind] <= 0:
            return
        if kind == "invite" and blocked(db, person["username"], "message"):
            return
        seen.add(person["username"])
        left[kind] -= 1
        items.append({
            "username": person["username"], "name": person["name"], "headline": person["headline"],
            "degree": person["degree"], "kind": kind, "reason": reason,
        })

    horizon = (now() - timedelta(days=CONFIG["viewer_max_age_days"])).isoformat()
    for person in db.execute("SELECT * FROM people WHERE viewed_at >= ? ORDER BY viewed_at DESC", (horizon,)):
        connected = person["degree"] == "1st" or person["connected_on"]
        add(person, "message" if connected else "invite", "viewer")
    if go_live := get(db, "go_live"):
        cutoff = date.fromisoformat(go_live) - timedelta(days=CONFIG["new_connection_lookback_days"])
        for person in db.execute(
            "SELECT * FROM people WHERE connected_on >= ? ORDER BY connected_on, username", (cutoff.isoformat(),)
        ):
            add(person, "message", "connection")
    emit(items)


def engage_due(db, args):
    """Whose recent posts to read this round: frontier-lab connections first, then other new connections.

    Anyone whose activity was read in the last `engage_person_gap_days` waits, so nobody gets more
    than one reaction and comment a week from the round.
    """
    if not left_today(db, "react") and not left_today(db, "comment"):
        emit([])
        return
    horizon = now() - timedelta(days=CONFIG["engage_person_gap_days"])
    recent = (today() - timedelta(days=CONFIG["engage_new_connection_days"])).isoformat()
    rows = db.execute(
        "SELECT username, name, headline, core, connected_on, posts_checked_at FROM people"
        " WHERE degree = '1st' AND (core = 1 OR connected_on >= ?)"
        " ORDER BY core DESC, coalesce(connected_on >= ?, 0) DESC, connected_on DESC, posts_checked_at IS NOT NULL,"
        " posts_checked_at, username",
        (recent, recent),
    )
    items = []
    for row in rows:
        if row["posts_checked_at"] and datetime.fromisoformat(row["posts_checked_at"]) > horizon:
            continue
        items.append({
            "username": row["username"], "name": row["name"], "headline": row["headline"],
            "reason": "core" if row["core"] else "connection",
        })
        if len(items) >= CONFIG["engage_profiles_per_round"]:
            break
    emit(items)


def posts_checked(db, args):
    db.execute("UPDATE people SET posts_checked_at = ? WHERE username = ?", (stamp(), args.username))
    emit({"posts_checked": args.username})


def catchups_due(db, args):
    """Catch-up cards (JSON list on stdin) not yet sent for their occasion."""
    items, seen = [], set()
    for row in json.load(sys.stdin):
        if not row.get("username") or not row.get("text"):
            continue
        occasion = catchup_occasion(row)
        if (row["username"], occasion) in seen or blocked(db, row["username"], "catchup", occasion):
            continue
        seen.add((row["username"], occasion))
        items.append({
            "username": row["username"], "name": row.get("name"), "kind": "catchup",
            "reason": row["reason"], "occasion": occasion, "text": row["text"],
        })
    emit(items)


def claim(db, args):
    kind, username, text = args.kind, args.username, (args.text or "").strip()
    occasion = args.occasion if kind in OCCASION_KINDS else None
    if kind in OCCASION_KINDS and not occasion:
        stop(2, f"a {kind} needs --occasion")
    if kind in DAILY_CAPS and not left_today(db, kind):
        stop(3, f"daily {kind} cap of {CONFIG[DAILY_CAPS[kind]]} reached")
    if kind == "invite":
        if paused := invites_paused(db):
            stop(3, f"invites are paused until {paused}: {get(db, 'invites_pause_reason')}")
        if not invites_left(db):
            stop(3, f"invite cap of {CONFIG['invites_per_window']} per {CONFIG['window_hours']} h reached")
        if len(text) > CONFIG["invite_note_max_chars"]:
            stop(2, f"invite note is longer than {CONFIG['invite_note_max_chars']} characters")
        person = db.execute("SELECT connected_on FROM people WHERE username = ?", (username,)).fetchone()
        if person and person["connected_on"]:
            stop(2, f"{username} is already a connection")
    elif kind == "react":
        if text:
            stop(2, "a reaction carries no text")
    else:
        if not text:
            stop(2, f"{kind} text is empty")
        limit = CONFIG["comment_max_chars" if kind == "comment" else "message_max_chars"]
        if len(text) > limit:
            stop(2, f"{kind} is longer than {limit} characters")
        # LinkedIn's comment box turns @ and # into mention and hashtag pickers, and Enter submits.
        if kind == "comment" and re.search(r"[@#\n]", text):
            stop(2, "a comment can't hold @, # or a line break")
    # Catch-ups send LinkedIn's preset texts verbatim: they repeat by design, and a company can be named "<Genesys>".
    if kind != "catchup" and text and re.search(r"https?://|www\.|[{}\[\]<>]", text):
        stop(2, "text contains a link or an unfilled placeholder")
    if kind != "catchup" and text and db.execute("SELECT 1 FROM actions WHERE text = ? AND outcome != 'failed'", (text,)).fetchone():
        stop(2, "this exact text was already sent to someone; personalize it")
    if blocked(db, username, kind, occasion):
        stop(2, f"{username} already has a recorded {kind}" + (f" for {occasion}" if occasion else ""))
    if kind == "message":
        person = db.execute("SELECT thread_clear_at FROM people WHERE username = ?", (username,)).fetchone()
        checked = person and person["thread_clear_at"]
        if not checked or now() - datetime.fromisoformat(checked) > THREAD_CHECK_MAX_AGE:
            stop(2, f"{username} has no conversation check from the last {THREAD_CHECK_MAX_AGE}; never message without one")
    last = db.execute("SELECT max(at) FROM actions WHERE outcome IN (?, ?, ?)", LIVE).fetchone()[0]
    if last:
        gap = random.uniform(*CONFIG["write_gap_seconds"]) - (now() - datetime.fromisoformat(last)).total_seconds()
        time.sleep(max(0, gap))
    db.execute(
        "INSERT INTO actions (username, kind, reason, occasion, day, at, text, outcome) VALUES (?, ?, ?, ?, ?, ?, ?, 'claimed')",
        (username, kind, args.reason, occasion, today().isoformat(), stamp(), text or None),
    )
    emit({"claimed": username, "kind": kind})


def thread(db, args):
    """Record a conversation check. Any existing thread retires the person from warm-ups for good."""
    if args.state == "none":
        db.execute("UPDATE people SET thread_clear_at = ? WHERE username = ?", (stamp(), args.username))
    elif not blocked(db, args.username, "message"):
        db.execute(
            "INSERT INTO actions (username, kind, reason, day, at, outcome, status)"
            " VALUES (?, 'message', ?, ?, ?, 'skipped', 'existing_conversation')",
            (args.username, args.reason, today().isoformat(), stamp()),
        )
    emit({"thread": args.username, "state": args.state})


def result(db, args):
    row = db.execute(
        "SELECT id FROM actions WHERE username = ? AND kind = ? AND outcome = 'claimed' ORDER BY id DESC LIMIT 1",
        (args.username, args.kind),
    ).fetchone()
    if not row:
        stop(2, f"no open {args.kind} claim for {args.username}")
    db.execute("UPDATE actions SET outcome = ?, status = ? WHERE id = ?", (args.outcome, args.status, row["id"]))
    if args.kind == "invite" and args.status in ("accepted", "already_connected"):
        # "accepted" means LinkedIn accepted their pending invite to me: a new connection owed a warm-up.
        connected = today().isoformat() if args.status == "accepted" else None
        db.execute(
            "UPDATE people SET degree = '1st', connected_on = coalesce(connected_on, ?) WHERE username = ?",
            (connected, args.username),
        )
    emit({"recorded": args.username, "kind": args.kind, "outcome": args.outcome})


def pause_invites(db, args):
    until = (today() + timedelta(days=args.days)).isoformat()
    put(db, "invites_paused_until", until)
    put(db, "invites_pause_reason", args.reason)
    emit({"invites_paused_until": until, "reason": args.reason})


def finish(db, args):
    started = datetime.fromisoformat(get(db, "round_started_at") or now().isoformat())
    put(db, "round_finished_at", stamp())
    counts = {}
    for row in db.execute(
        "SELECT kind, reason, outcome, at FROM actions WHERE day >= ?", ((started.date() - timedelta(days=1)).isoformat(),)
    ):
        if datetime.fromisoformat(row["at"]) >= started:
            key = (row["kind"], row["reason"], row["outcome"])
            counts[key] = counts.get(key, 0) + 1
    state = snapshot(db)
    emit({
        "round_started_at": started.isoformat(timespec="seconds"),
        "actions": [{"kind": k, "reason": r, "outcome": o, "n": n} for (k, r, o), n in sorted(counts.items())],
        **{key: state[key] for key in ("invites", "messages", "reacts", "comments", "queued_candidates")},
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("precheck", help="exit 1 when a new round should not start")
    commands.add_parser("status", help="caps, pause and scan window")
    commands.add_parser("start", help="begin a round: exit 1 like precheck, else record it and print status")
    command = commands.add_parser("ingest", help="record page rows (JSON list on stdin)")
    command.add_argument("kind", choices=("connections", "viewers", "search", "core"))
    command.add_argument("--search", help="search name from next-search (kind=search)")
    commands.add_parser("next-search", help="URL of the next people-search page to read")
    commands.add_parser("next-core-page", help="URL of the next page of frontier-lab connections")
    command = commands.add_parser("candidates", help="queued cold-invite candidates")
    command.add_argument("limit", type=int)
    commands.add_parser("due", help="warm-up messages and viewer invites owed today")
    commands.add_parser("engage-due", help="people whose recent posts to read this round")
    commands.add_parser("post-search", help="URL of the past week's posts by frontier-lab people")
    commands.add_parser("posts-due", help="post-search authors (JSON list on stdin) due for engagement")
    command = commands.add_parser("posts-checked", help="record a read of someone's recent activity")
    command.add_argument("username")
    commands.add_parser("catchups-due", help="catch-up cards (JSON list on stdin) not yet sent")
    command = commands.add_parser("claim", help="reserve one write before sending it")
    command.add_argument("kind", choices=("invite", "message", "catchup", "react", "comment"))
    command.add_argument("username")
    command.add_argument("--reason", required=True, choices=(
        "search", "viewer", "connection", "core", "birthday", "job_change", "work_anniversary", "education",
    ))
    command.add_argument("--occasion", help="catch-up occasion from catchups-due, or the post key")
    command.add_argument("--text")
    command = commands.add_parser("thread", help="record a conversation check before a warm-up")
    command.add_argument("username")
    command.add_argument("state", choices=("none", "exists"))
    command.add_argument("--reason", required=True, choices=("viewer", "connection"))
    command = commands.add_parser("result", help="settle the open claim")
    command.add_argument("username")
    command.add_argument("kind", choices=("invite", "message", "catchup", "react", "comment"))
    command.add_argument("outcome", choices=("sent", "unknown", "skipped", "failed"))
    command.add_argument("--status", required=True)
    command = commands.add_parser("pause-invites", help="stop invites for a number of days")
    command.add_argument("--days", type=int, required=True)
    command.add_argument("--reason", required=True)
    commands.add_parser("finish", help="mark the round done and summarize it")
    args = parser.parse_args()
    if args.command == "ingest" and args.kind == "search" and not args.search:
        parser.error("ingest search needs --search")
    globals()[args.command.replace("-", "_")](open_db(), args)


if __name__ == "__main__":
    main()
