"""Command line: `linkedin-interaction <group> <action>`. Run with --help for the full command list."""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

from selenium.common.exceptions import WebDriverException

from . import communication, connection, job_applying, job_collecting, job_matching, posts, social, visits
from .config import Action, ConfigError, Settings, load_settings
from .session import LinkedInError, Session

log = logging.getLogger("linkedin_interaction")

Runner = Callable[[Session], None]


class UsageError(Exception):
    """Bad command-line input, detected before the browser starts."""


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = load_settings(args.config)
        if args.headless:
            settings = dataclasses.replace(settings, browser=dataclasses.replace(settings.browser, headless=True))
        settings, run = args.command(args, settings)
    except (ConfigError, tomllib.TOMLDecodeError, UsageError, ValueError, OSError) as error:
        parser.error(str(error))

    try:
        with Session(settings) as session:
            session.login()
            run(session)
    except KeyboardInterrupt:
        log.warning("Interrupted.")
        return 130
    except (LinkedInError, job_matching.RerankError, ValueError, OSError) as error:
        log.error("%s", error)
        return 1
    except WebDriverException as error:
        log.error("Browser error (was the window closed?): %s", error.msg)
        return 1
    return 0


# --- shared argument helpers -------------------------------------------------------------------


def _text(value: str) -> str:
    """Positional text argument; "-" reads it from stdin so multi-line text needs no shell quoting."""
    text = sys.stdin.read() if value == "-" else value
    text = text.strip()
    if not text:
        raise UsageError("text is empty")
    return text


def _cap(settings: Settings, action: Action, maximum: int | None) -> Settings:
    """Lower one per-run cap for this run; --max can never raise a cap set in config."""
    if maximum is None:
        return settings
    if maximum < 1:
        raise UsageError("--max must be at least 1")
    limits = {**settings.limits, action: min(settings.limits[action], maximum)}
    return dataclasses.replace(settings, limits=limits)


def _note(args: argparse.Namespace, settings: Settings) -> str:
    note = (settings.connection.note if args.note is None else args.note).strip()
    if len(note) > connection.MAX_NOTE_LENGTH:
        raise UsageError(f"connection note is {len(note)} chars; LinkedIn allows {connection.MAX_NOTE_LENGTH}")
    return note


# --- commands: each validates input, then returns (settings, runner) ---------------------------


def _login(args, settings):
    def run(session: Session) -> None:
        print(f"Logged in. Session saved in {settings.browser.resolved_profile_dir}")

    return settings, run


def _connect_profile(args, settings):
    note = _note(args, settings)

    def run(session: Session) -> None:
        result = connection.connect(session, args.profile_url, note)
        print(f"{result.status.value}: {result.target}" + (f" ({result.detail})" if result.detail else ""))

    return settings, run


def _connect_search(args, settings):
    url = args.url or settings.connection.search_url
    if not url:
        raise UsageError("no search URL: pass --url or set connection.search_url in config.toml")
    note = _note(args, settings)
    settings = _cap(settings, Action.CONNECTION, args.max)

    def run(session: Session) -> None:
        summary = connection.connect_from_search(session, url, note, follow=args.follow)
        print(
            f"Invited {summary.invited}, followed {summary.followed}, "
            f"skipped {summary.skipped} across {summary.pages} page(s)."
        )

    return settings, run


def _connect_accept(args, settings):
    settings = _cap(settings, Action.CONNECTION, args.max)

    def run(session: Session) -> None:
        names = connection.accept_invitations(session)
        print(f"Accepted {len(names)} invitation(s)" + (": " + ", ".join(names) if names else "."))

    return settings, run


def _message_send(args, settings):
    text = _text(args.text)

    def run(session: Session) -> None:
        communication.send_message(session, args.profile_url, text)
        print(f"Sent message to {args.profile_url}")

    return settings, run


def _message_inbox(args, settings):
    def run(session: Session) -> None:
        conversations = communication.list_conversations(session, args.limit, args.unread)
        for conversation in conversations:
            marker = "*" if conversation.unread else " "
            print(f"{marker} {conversation.participants}: {conversation.snippet}")
        if not conversations:
            print("No conversations.")

    return settings, run


def _message_read(args, settings):
    def run(session: Session) -> None:
        for message in communication.read_conversation(session, args.who):
            print(f"{message.sender}: {message.text}")

    return settings, run


def _message_reply(args, settings):
    text = _text(args.text)

    def run(session: Session) -> None:
        communication.reply(session, args.who, text)
        print(f"Replied to {args.who}")

    return settings, run


def _social_follow(args, settings):
    def run(session: Session) -> None:
        followed = social.follow(session, args.url)
        print(f"Followed {args.url}" if followed else f"Already following {args.url}")

    return settings, run


def _social_react(args, settings):
    reaction = social.Reaction(args.type)

    def run(session: Session) -> None:
        reacted = social.react(session, args.post_url, reaction)
        print(f"Reacted '{reaction.value}' to {args.post_url}" if reacted else "You already reacted to this post.")

    return settings, run


def _social_comment(args, settings):
    text = _text(args.text)

    def run(session: Session) -> None:
        social.comment(session, args.post_url, text)
        print(f"Commented on {args.post_url}")

    return settings, run


def _social_comments(args, settings):
    if args.limit < 1:
        raise UsageError("--limit must be at least 1")

    def run(session: Session) -> None:
        comments = social.read_comments(session, args.post_url, args.limit)
        waiting = {c.id for c in social.awaiting_reply(comments)}
        for item in comments:
            indent = "    ↳ " if item.is_reply else ""
            flag = " (you)" if item.mine else "  [awaiting your reply]" if item.id in waiting else ""
            print(f"{indent}{item.id}  {item.author}  {item.profile}{flag}")
            pad = "      " if item.is_reply else "  "
            print("\n".join(pad + line for line in item.text.splitlines()) + "\n")
        print(f"{len(comments)} comment(s); {len(waiting)} awaiting your reply.")

    return settings, run


def _social_reply(args, settings):
    text = _text(args.text)

    def run(session: Session) -> None:
        target, sent = social.reply(session, args.post_url, args.comment, text)
        if sent:
            print(f"Replied to {target.author} ({target.id}) on {args.post_url}")
        else:
            print(f"Already replied to {target.author} ({target.id}) with this text; nothing sent.")

    return settings, run


def _post_create(args, settings):
    text = _text(args.text)
    if len(text) > posts.MAX_POST_LENGTH:
        raise UsageError(f"post is {len(text)} chars; LinkedIn allows {posts.MAX_POST_LENGTH}")

    def run(session: Session) -> None:
        posts.create_post(session, text)
        print("Post published.")

    return settings, run


def _post_feed(args, settings):
    def run(session: Session) -> None:
        for post in posts.read_feed(session, args.limit):
            # LinkedIn's feed exposes a post's URL only for some posts.
            print(f"{post.author}: {post.text[:280]}" + (f"\n    {post.url}" if post.url else "") + "\n")

    return settings, run


def _visit_targets(values: list[str]) -> list[str]:
    """Profile keys from URLs or usernames, in order and without repeats; "-" reads them from stdin."""
    raw: list[str] = []
    for value in values:
        raw += sys.stdin.read().split() if value == "-" else [value]
    keys: list[str] = []
    for value in raw:
        key = visits.profile_key(value)
        if key not in keys:
            keys.append(key)
    if not keys:
        raise UsageError("no profiles to visit")
    return keys


def _print_visits(results: list[visits.VisitResult]) -> None:
    for result in results:
        print(result.line())
    viewed = sum(r.status is visits.VisitStatus.VISITED for r in results)
    print(f"Viewed {viewed} profile(s); {len(results) - viewed} skipped or unavailable.")


def _visit_profile(args, settings):
    keys = _visit_targets(args.targets)
    settings = _cap(settings, Action.VISIT, args.max)

    def run(session: Session) -> None:
        with visits.VisitHistory(settings.visits.history) as history:
            _print_visits(visits.visit_profiles(session, keys, history, settings.visits, again=args.again))

    return settings, run


def _visit_search(args, settings):
    url = args.url or settings.connection.search_url
    if not url:
        raise UsageError("no search URL: pass --url or set connection.search_url in config.toml")
    if args.pages < 1:
        raise UsageError("--pages must be at least 1")
    settings = _cap(settings, Action.VISIT, args.max)

    def run(session: Session) -> None:
        with visits.VisitHistory(settings.visits.history) as history:
            _print_visits(visits.visit_from_search(session, url, history, settings.visits, max_pages=args.pages))

    return settings, run


def _jobs_match(args, settings):
    overrides = {
        key: value
        for key, value in (
            ("keywords", args.keywords),
            ("location", args.location),
            ("geo_id", args.geo_id),
            ("max_results", args.max_results),
        )
        if value is not None
    }
    jobs = dataclasses.replace(settings.jobs, **overrides)
    if not jobs.keywords:
        raise UsageError("no job keywords: pass --keywords or set jobs.keywords in config.toml")
    rerank = not args.no_rerank
    if rerank and settings.matching.resume is None:
        raise UsageError("the Claude rerank needs matching.resume in config.toml; or pass --no-rerank")
    output = args.output or settings.matching.output
    settings = dataclasses.replace(settings, jobs=jobs)

    def run(session: Session) -> None:
        matches = job_matching.match_jobs(session, settings, rerank_enabled=rerank)
        job_matching.save_matches(matches, output)
        _print_matches(matches)
        print(f"\n{len(matches)} match(es) saved to {output}. Apply with: linkedin-interaction jobs apply --from {output}")

    return settings, run


def _print_matches(matches: list[job_matching.Match]) -> None:
    for match in matches:
        job = match.job
        llm = "--" if match.llm_score is None else f"{match.llm_score:>3}"
        apply_kind = "Easy Apply" if job.easy_apply else "external"
        print(f"[{match.score:>3}] claude {llm} | keywords {match.keyword.score:.2f} | {job.title} @ {job.company}")
        print(f"      {job.location} | {apply_kind} | {job.url}")
        if match.reasons:
            print(f"      + {'; '.join(match.reasons[:2])}")
        if match.gaps:
            print(f"      - {'; '.join(match.gaps[:2])}")


def _jobs_apply(args, settings):
    if settings.browser.headless:
        raise UsageError("jobs apply needs a visible browser for review; drop --headless / browser.headless")
    settings = _cap(settings, Action.APPLICATION, args.max)
    if args.job_urls:
        urls = list(args.job_urls)
    else:
        source = args.source or settings.matching.output
        if not source.exists():
            raise UsageError(f"{source} not found: run `linkedin-interaction jobs match` first or pass job URLs")
        min_score = settings.applying.min_score if args.min_score is None else args.min_score
        selected = job_matching.select_for_applying(job_matching.load_matches(source), min_score)
        if not selected:
            raise UsageError(f"no Easy Apply matches with score >= {min_score} in {source}")
        urls = [match.job.url for match in selected]

    def run(session: Session) -> None:
        results = job_applying.apply_all(session, urls, settings.applying, job_applying.TerminalReviewer())
        for result in results:
            print(f"{result.status.value:>15}  {result.job_url}" + (f"  ({result.detail})" if result.detail else ""))
        submitted = sum(r.status is job_applying.ApplyStatus.SUBMITTED for r in results)
        print(f"Submitted {submitted} of {len(results)} application(s).")

    return settings, run


def _jobs_collect(args, settings):
    if args.limit is not None and args.limit < 1:
        raise UsageError("--limit must be at least 1")
    out_dir = args.out.expanduser()

    def run(session: Session) -> None:
        results = job_collecting.collect_jobs(session, out_dir, args.stage, refresh=args.refresh, limit=args.limit)
        for result in results:
            where = result.path.name if result.path is not None else job_collecting.job_url(result.job_id)
            print(f"{result.status.value:>9}  {where}" + (f"  ({result.detail})" if result.detail else ""))
        counts = {status: sum(r.status is status for r in results) for status in job_collecting.CollectStatus}
        print(", ".join(f"{count} {status.value}" for status, count in counts.items())
              + f". Index: {out_dir / f'{args.stage}-jobs.md'}")

    return settings, run


# --- parser ------------------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linkedin-interaction",
        description="Automate LinkedIn: communication, connections, social interaction, posts, job matching and applying.",
    )
    parser.add_argument("--config", type=Path, help="TOML settings file (default: ./config.toml if present)")
    parser.add_argument("--headless", action="store_true", help="run the browser without a window")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    groups = parser.add_subparsers(dest="group", required=True, metavar="<group>")

    login = groups.add_parser("login", help="log in once; the browser profile keeps the session")
    login.set_defaults(command=_login)

    def actions(name: str, help_text: str) -> argparse._SubParsersAction:
        group = groups.add_parser(name, help=help_text)
        return group.add_subparsers(dest="action", required=True, metavar="<action>")

    connect = actions("connect", "connection requests and invitations")
    cmd = connect.add_parser("profile", help="send a connection request from a profile page")
    cmd.add_argument("profile_url")
    cmd.add_argument("--note", help="invitation note (default: connection.note; '' for none)")
    cmd.set_defaults(command=_connect_profile)
    cmd = connect.add_parser("search", help="invite everyone in a people-search result (the original bot)")
    cmd.add_argument("--url", help="people-search URL (default: connection.search_url)")
    cmd.add_argument("--note", help="invitation note (default: connection.note; '' for none)")
    cmd.add_argument("--follow", action="store_true", help="also follow people who only offer Follow")
    cmd.add_argument("--max", type=int, help="stop after N invitations (never above limits.connections)")
    cmd.set_defaults(command=_connect_search)
    cmd = connect.add_parser("accept", help="accept received invitations")
    cmd.add_argument("--max", type=int, help="accept at most N (never above limits.connections)")
    cmd.set_defaults(command=_connect_accept)

    message = actions("message", "direct messages")
    cmd = message.add_parser("send", help="message a person from their profile")
    cmd.add_argument("profile_url")
    cmd.add_argument("text", help="message text, or - to read it from stdin")
    cmd.set_defaults(command=_message_send)
    cmd = message.add_parser("inbox", help="list recent conversations")
    cmd.add_argument("--limit", type=int, default=20)
    cmd.add_argument("--unread", action="store_true", help="only unread conversations")
    cmd.set_defaults(command=_message_inbox)
    cmd = message.add_parser("read", help="print a conversation")
    cmd.add_argument("who", help="participant name as shown in the inbox, or a /messaging/thread/ URL")
    cmd.set_defaults(command=_message_read)
    cmd = message.add_parser("reply", help="reply in a conversation")
    cmd.add_argument("who", help="participant name as shown in the inbox, or a /messaging/thread/ URL")
    cmd.add_argument("text", help="message text, or - to read it from stdin")
    cmd.set_defaults(command=_message_reply)

    social_group = actions("social", "follow, react, comment and reply to comments")
    cmd = social_group.add_parser("follow", help="follow a person (/in/) or company (/company/)")
    cmd.add_argument("url")
    cmd.set_defaults(command=_social_follow)
    cmd = social_group.add_parser("react", help="react to a post")
    cmd.add_argument("post_url")
    cmd.add_argument("--type", choices=[r.value for r in social.Reaction], default=social.Reaction.LIKE.value)
    cmd.set_defaults(command=_social_react)
    cmd = social_group.add_parser("comment", help="comment on a post")
    cmd.add_argument("post_url")
    cmd.add_argument("text", help="comment text, or - to read it from stdin")
    cmd.set_defaults(command=_social_comment)
    cmd = social_group.add_parser("comments", help="list a post's comments and replies, flagging ones awaiting your reply")
    cmd.add_argument("post_url")
    cmd.add_argument("--limit", type=int, default=200, help="read at most N comments and replies (default: 200)")
    cmd.set_defaults(command=_social_comments)
    cmd = social_group.add_parser("reply", help="reply to a comment on a post")
    cmd.add_argument("post_url")
    cmd.add_argument("comment", help="comment id from `social comments`, or the commenter's name if they left one comment")
    cmd.add_argument("text", help="reply text, or - to read it from stdin")
    cmd.set_defaults(command=_social_reply)

    post = actions("post", "publish posts and read the feed")
    cmd = post.add_parser("create", help="publish a text post")
    cmd.add_argument("text", help="post text, or - to read it from stdin")
    cmd.set_defaults(command=_post_create)
    cmd = post.add_parser("feed", help="print recent feed posts")
    cmd.add_argument("--limit", type=int, default=10)
    cmd.set_defaults(command=_post_feed)

    visit = actions("visit", "view profiles so you show up in their 'Who viewed your profile'")
    cmd = visit.add_parser("profile", help="view the given profiles")
    cmd.add_argument("targets", nargs="+", help="profile URLs or usernames; - reads them from stdin, whitespace-separated")
    cmd.add_argument("--again", action="store_true", help="also view people viewed within visits.revisit_days")
    cmd.add_argument("--max", type=int, help="view at most N profiles (never above limits.visits)")
    cmd.set_defaults(command=_visit_profile)
    cmd = visit.add_parser("search", help="view people from a people-search result, skipping recent visits")
    cmd.add_argument("--url", help="people-search URL (default: connection.search_url)")
    cmd.add_argument("--pages", type=int, default=5, help="result pages to read (default: 5)")
    cmd.add_argument("--max", type=int, help="view at most N profiles (never above limits.visits)")
    cmd.set_defaults(command=_visit_search)

    jobs = actions("jobs", "collect tracked jobs, job matching and Easy Apply")
    cmd = jobs.add_parser("collect", help="save your job-tracker jobs (default: Saved) as Markdown files")
    cmd.add_argument("--stage", choices=job_collecting.STAGES, default="saved", help="job tracker tab (default: saved)")
    cmd.add_argument("--out", type=Path, default=Path("~/linkedin-jobs"), help="folder (default: ~/linkedin-jobs)")
    cmd.add_argument("--refresh", action="store_true", help="re-fetch jobs that already have a file")
    cmd.add_argument("--limit", type=int, help="only the first N jobs in the tracker (the most recent)")
    cmd.set_defaults(command=_jobs_collect)
    cmd = jobs.add_parser("match", help="search jobs, score them against your profile, save matches")
    cmd.add_argument("--keywords", help="default: jobs.keywords")
    cmd.add_argument("--location", help="default: jobs.location (ignored by LinkedIn's newer search; use --geo-id)")
    cmd.add_argument("--geo-id", help="LinkedIn geoId, e.g. 103644278 = United States (default: jobs.geo_id)")
    cmd.add_argument("--max-results", type=int, help="default: jobs.max_results")
    cmd.add_argument("--no-rerank", action="store_true", help="keyword scoring only, skip the Claude rerank")
    cmd.add_argument("--output", type=Path, help="default: matching.output")
    cmd.set_defaults(command=_jobs_match)
    cmd = jobs.add_parser("apply", help="Easy Apply with a review prompt before each submission")
    cmd.add_argument("job_urls", nargs="*", help="job URLs (default: matches from --from)")
    cmd.add_argument("--from", dest="source", type=Path, help="matches file (default: matching.output)")
    cmd.add_argument("--min-score", type=int, help="default: applying.min_score")
    cmd.add_argument("--max", type=int, help="apply to at most N jobs (never above limits.applications)")
    cmd.set_defaults(command=_jobs_apply)

    return parser
