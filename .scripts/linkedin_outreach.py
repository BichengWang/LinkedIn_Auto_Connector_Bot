#!/usr/bin/env python3
"""Ledger and guard rails for the daily LinkedIn outreach round.

`.workflow/linkedin-daily-outreach.md` is the runbook. The round reads LinkedIn
in Chrome and writes through the LinkedIn MCP server; this ledger decides who is
due and enforces the daily caps, the invite pause and per-person dedupe, so
nobody is invited or messaged twice even when a run dies halfway.

Exit codes: 0 ok, 1 precheck says skip, 2 refused for this person, 3 stop this
kind of write for today (cap reached or invites paused).
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
    first_seen   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS actions (
    id       INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    kind     TEXT NOT NULL,  -- invite | message
    reason   TEXT NOT NULL,  -- search | viewer | connection
    day      TEXT NOT NULL,
    at       TEXT NOT NULL,
    text     TEXT,
    outcome  TEXT NOT NULL,  -- claimed | sent | unknown | skipped | failed
    status   TEXT            -- LinkedIn MCP status
);
CREATE INDEX IF NOT EXISTS actions_person ON actions (username, kind);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def now():
    return datetime.now(TZ)


def today():
    return now().date()


def open_db():
    HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = sqlite3.connect(HOME / "ledger.sqlite3", isolation_level=None)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
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


def blocked(db, username, kind):
    """One write per person and kind; two proven failures retire the person."""
    live, failed = db.execute(
        "SELECT count(*) FILTER (WHERE outcome != 'failed'), count(*) FILTER (WHERE outcome = 'failed')"
        " FROM actions WHERE username = ? AND kind = ?",
        (username, kind),
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


def viewed_at(text):
    match = re.search(r"(\d+)\s*(mo|yr|y|m|h|d|w)\b", text or "")
    days = int(match[1]) * AGE_DAYS[match[2]] if match else 0
    return (now() - timedelta(days=days)).isoformat(timespec="seconds")


def connections_since(db):
    """Oldest Connected-on date today's Connections scan has to reach."""
    last = get(db, "connections_scan_day")
    start = date.fromisoformat(last) if last else today()
    return start - timedelta(days=CONFIG["new_connection_lookback_days"])


def search_cursor(db, search):
    return get(db, "search_cursors", {}).get(search["name"], {"keyword": 0, "page": 1})


def precheck(db, args):
    if get(db, "finished_day") == today().isoformat():
        stop(1, f"LinkedIn outreach already finished for {today()}")
    if subprocess.run(["pgrep", "-x", "Google Chrome"], capture_output=True).returncode:
        stop(1, "Google Chrome is not running; the round reads LinkedIn through it")


def status(db, args):
    emit({
        "day": today().isoformat(),
        "finished_today": get(db, "finished_day") == today().isoformat(),
        "go_live": get(db, "go_live"),
        "invites": {"used": used_today(db, "invite"), "cap": CONFIG["invites_per_day"], "paused_until": invites_paused(db)},
        "messages": {"used": used_today(db, "message"), "cap": CONFIG["messages_per_day"]},
        "connections_since": connections_since(db).isoformat(),
        "queued_candidates": len(queued(db, 10**6)),
    })


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
    else:
        search = next(s for s in CONFIG["searches"] if s["name"] == args.search)
        cursor = search_cursor(db, search)
        cursor = {"keyword": cursor["keyword"], "page": cursor["page"] + 1}
        if not rows or cursor["page"] > SEARCH_PAGES:
            cursor = {"keyword": (cursor["keyword"] + 1) % len(search["keywords"]), "page": 1}
        put(db, "search_cursors", {**get(db, "search_cursors", {}), search["name"]: cursor})
        for row in rows:
            upsert(db, row)
            if row.get("action") == "connect" and not blocked(db, row["username"], "invite"):
                queued_now += db.execute(
                    "UPDATE people SET candidate_at = coalesce(candidate_at, ?) WHERE username = ? AND connected_on IS NULL",
                    (now().isoformat(timespec="seconds"), row["username"]),
                ).rowcount
    emit({"ingested": len(rows), "queued": queued_now})


def next_search(db, args):
    turn = get(db, "search_turn", 0)
    search = CONFIG["searches"][turn % len(CONFIG["searches"])]
    put(db, "search_turn", turn + 1)
    cursor = search_cursor(db, search)
    keyword = search["keywords"][cursor["keyword"] % len(search["keywords"])]
    query = {"keywords": keyword, "origin": "FACETED_SEARCH"}
    for facet in ("network", "geoUrn", "industry"):
        if facet in search:
            query[facet] = json.dumps(search[facet], separators=(",", ":"))
    query["page"] = cursor["page"]
    url = "https://www.linkedin.com/search/results/people/?" + urlencode(query)
    emit({"search": search["name"], "keyword": keyword, "page": cursor["page"], "url": url})


def queued(db, limit):
    rows = db.execute(
        "SELECT username, name, headline FROM people WHERE candidate_at IS NOT NULL AND connected_on IS NULL"
        " ORDER BY candidate_at, username"
    )
    return [dict(row) for row in rows if not blocked(db, row["username"], "invite")][:limit]


def candidates(db, args):
    emit(queued(db, args.limit))


def due(db, args):
    """Warm-ups owed today: recent profile viewers first, then new connections."""
    left = {
        "message": CONFIG["messages_per_day"] - used_today(db, "message"),
        "invite": 0 if invites_paused(db) else CONFIG["invites_per_day"] - used_today(db, "invite"),
    }
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


def claim(db, args):
    kind, username, text = args.kind, args.username, (args.text or "").strip()
    if kind == "invite":
        if paused := invites_paused(db):
            stop(3, f"invites are paused until {paused}: {get(db, 'invites_pause_reason')}")
        if used_today(db, "invite") >= CONFIG["invites_per_day"]:
            stop(3, f"daily invite cap of {CONFIG['invites_per_day']} reached")
        if len(text) > CONFIG["invite_note_max_chars"]:
            stop(2, f"invite note is longer than {CONFIG['invite_note_max_chars']} characters")
        person = db.execute("SELECT connected_on FROM people WHERE username = ?", (username,)).fetchone()
        if person and person["connected_on"]:
            stop(2, f"{username} is already a connection")
    else:
        if used_today(db, "message") >= CONFIG["messages_per_day"]:
            stop(3, f"daily message cap of {CONFIG['messages_per_day']} reached")
        if not text:
            stop(2, "message text is empty")
        if len(text) > CONFIG["message_max_chars"]:
            stop(2, f"message is longer than {CONFIG['message_max_chars']} characters")
    if text and re.search(r"https?://|www\.|[{}\[\]<>]", text):
        stop(2, "text contains a link or an unfilled placeholder")
    if text and db.execute("SELECT 1 FROM actions WHERE text = ? AND outcome != 'failed'", (text,)).fetchone():
        stop(2, "this exact text was already sent to someone; personalize it")
    if blocked(db, username, kind):
        stop(2, f"{username} already has a recorded {kind}")
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
        "INSERT INTO actions (username, kind, reason, day, at, text, outcome) VALUES (?, ?, ?, ?, ?, ?, 'claimed')",
        (username, kind, args.reason, today().isoformat(), now().isoformat(timespec="seconds"), text or None),
    )
    emit({"claimed": username, "kind": kind})


def thread(db, args):
    """Record a conversation check. Any existing thread retires the person from warm-ups for good."""
    if args.state == "none":
        db.execute(
            "UPDATE people SET thread_clear_at = ? WHERE username = ?",
            (now().isoformat(timespec="seconds"), args.username),
        )
    elif not blocked(db, args.username, "message"):
        db.execute(
            "INSERT INTO actions (username, kind, reason, day, at, outcome, status)"
            " VALUES (?, 'message', ?, ?, ?, 'skipped', 'existing_conversation')",
            (args.username, args.reason, today().isoformat(), now().isoformat(timespec="seconds")),
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
    put(db, "finished_day", today().isoformat())
    rows = db.execute(
        "SELECT kind, reason, outcome, count(*) AS n FROM actions WHERE day = ? GROUP BY 1, 2, 3 ORDER BY 1, 2, 3",
        (today().isoformat(),),
    )
    emit({
        "day": today().isoformat(),
        "actions": [dict(row) for row in rows],
        "invites_paused_until": invites_paused(db),
        "queued_candidates": len(queued(db, 10**6)),
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("precheck", help="exit 1 when today's round should not start")
    commands.add_parser("status", help="caps, pause and scan window for today")
    command = commands.add_parser("ingest", help="record page rows (JSON list on stdin)")
    command.add_argument("kind", choices=("connections", "viewers", "search"))
    command.add_argument("--search", help="search name from next-search (kind=search)")
    commands.add_parser("next-search", help="URL of the next people-search page to read")
    command = commands.add_parser("candidates", help="queued cold-invite candidates")
    command.add_argument("limit", type=int)
    commands.add_parser("due", help="warm-up messages and viewer invites owed today")
    command = commands.add_parser("claim", help="reserve one write before sending it")
    command.add_argument("kind", choices=("invite", "message"))
    command.add_argument("username")
    command.add_argument("--reason", required=True, choices=("search", "viewer", "connection"))
    command.add_argument("--text")
    command = commands.add_parser("thread", help="record a conversation check before a warm-up")
    command.add_argument("username")
    command.add_argument("state", choices=("none", "exists"))
    command.add_argument("--reason", required=True, choices=("viewer", "connection"))
    command = commands.add_parser("result", help="settle the open claim")
    command.add_argument("username")
    command.add_argument("kind", choices=("invite", "message"))
    command.add_argument("outcome", choices=("sent", "unknown", "skipped", "failed"))
    command.add_argument("--status", required=True)
    command = commands.add_parser("pause-invites", help="stop invites for a number of days")
    command.add_argument("--days", type=int, required=True)
    command.add_argument("--reason", required=True)
    commands.add_parser("finish", help="mark today's round done and summarize it")
    args = parser.parse_args()
    if args.command == "ingest" and args.kind == "search" and not args.search:
        parser.error("ingest search needs --search")
    globals()[args.command.replace("-", "_")](open_db(), args)


if __name__ == "__main__":
    main()
