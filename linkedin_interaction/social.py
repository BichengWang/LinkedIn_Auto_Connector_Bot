from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import unquote, urlparse

from selenium.webdriver.remote.webelement import WebElement

from .config import Action
from .session import BASE_URL, LinkedInError, Session, xpath_literal
from .visits import profile_key

log = logging.getLogger(__name__)

__all__ = [
    "Comment",
    "Reaction",
    "awaiting_reply",
    "comment",
    "find_comment",
    "follow",
    "react",
    "read_comments",
    "reply",
]

OWNER_CONTROL_XPATH = "//main//*[self::button or @role='button'][@aria-label={label}]"
PERSON_FOLLOW_STATE_XPATH = (
    "//main//*[self::button or @role='button']"
    "[(starts-with(@aria-label,'Following') and contains(@aria-label,{name})) or @aria-label={unfollow}]"
)
COMPANY_FOLLOW_XPATH = (
    "(//main//button[@aria-label='Follow' or @aria-label='Following' or "
    "normalize-space()='Follow' or normalize-space()='+ Follow' or normalize-space()='Following'])[1]"
)
MORE_XPATH = (
    "//main//*[self::button or @role='button'][@aria-label='More actions' or "
    "starts-with(@aria-label, 'More actions for ') or @aria-label='More' or normalize-space()='More']"
)
FOLLOW_MENU_ITEM_XPATH = (
    "//*[@role='menuitem'] | //*[@role='menu']//*[self::button or @role='button']"
    " | //*[contains(@class,'artdeco-dropdown__content')]//*[self::button or @role='button']"
)
MENU_FOLLOW_XPATH = (
    "(" + FOLLOW_MENU_ITEM_XPATH + ")[normalize-space()='Follow' or @aria-label={label} "
    "or .//*[@aria-label={label}]]"
)
MENU_FOLLOW_STATE_XPATH = (
    "(" + FOLLOW_MENU_ITEM_XPATH + ")[normalize-space()='Following' or normalize-space()='Unfollow' "
    "or starts-with(@aria-label,'Following') or starts-with(@aria-label,'Unfollow ')]"
)
POST_CONTAINER_XPATH = (
    "//main//*[@data-urn[contains(., 'urn:li:activity:')] or "
    "@data-id[contains(., 'urn:li:activity:')]]"
)
TARGET_POST_XPATH = (
    "//main//*[@data-urn={urn} or @data-id={urn}]"
)
SDUI_POST_CONTAINER_XPATH = (
    "(//main//button[starts-with(@aria-label,'Open control menu for post by')])[1]"
    "/ancestor::*[.//button[starts-with(@aria-label,'Reaction button state')]][1]"
)
REACTION_XPATH = (
    ".//button[(starts-with(@aria-label, 'React ') or starts-with(@aria-label, 'Unreact ') or "
    "starts-with(@aria-label, 'Remove reaction') or starts-with(@aria-label,'Reaction button state')) "
    "and not(ancestor::*[@data-id[contains(., 'comment')] "
    "or contains(@class, 'comments-comment-item')])]"
)
REACTION_CHOICE_XPATH = "//button[@aria-label={label}] | //*[@role='button'][@aria-label={label}]"
COMMENT_BUTTON_XPATH = (
    ".//*[self::button or self::a][@aria-label='Comment' or "
    "starts-with(@aria-label, 'Comment on') or normalize-space()='Comment'][not(ancestor::form)]"
)
PREFERRED_COMMENT_EDITOR_XPATH = (
    ".//*[@contenteditable='true' and @role='textbox' and @aria-label='Text editor for creating comment']"
    " | ./following::*[@contenteditable='true' and @role='textbox' "
    "and @aria-label='Text editor for creating comment']"
)
COMMENT_EDITOR_XPATH = (
    ".//*[@contenteditable='true' and @role='textbox']"
    " | ./following::*[@contenteditable='true' and @role='textbox']"
)
COMMENT_SUBMIT_XPATH = (
    "./following::button[normalize-space()='Comment' or normalize-space()='Post']"
    "[not(@disabled) and not(@aria-disabled='true')]"
)
COMMENT_TEXT_XPATH = (
    "//main//*[@data-test-id='comment-text' or contains(@class, 'comments-comment-item__main-content') "
    "or contains(@class, 'comments-comment-item-content-body')][normalize-space(.)={text}]"
    " | //main//*[@data-id[contains(., 'urn:li:comment:')]]//*[@dir='ltr'][normalize-space(.)={text}]"
    " | //main//*[contains(@componentkey,'urn:li:comment') or contains(@id,'urn:li:comment')]"
    "//*[@data-testid='expandable-text-box'][normalize-space(.)={text}]"
)


# Post pages (SDUI) list comments and replies as flat siblings; a reply is told apart only by its indent.
COMMENT_ITEM_XPATH = "//main//*[starts-with(@componentkey,'replaceableComment_urn:li:comment:')]"
COMMENT_BY_ID_XPATH = (
    "//main//*[starts-with(@componentkey,'replaceableComment_urn:li:comment:') and contains(@componentkey,{suffix})]"
)
COMMENT_ID_RE = re.compile(r",(\d+)\)$")
MORE_REPLIES_XPATH = (
    "//main//*[self::button or @role='button'][starts-with(normalize-space(),'See previous replies') "
    "or (starts-with(normalize-space(),'See ') and contains(normalize-space(),' more repl'))]"
)
REPLY_BUTTON_XPATH = ".//button[@aria-label='Reply']"
EDITOR_XPATH = "//main//*[@contenteditable='true' and @role='textbox']"
REPLY_SUBMIT_XPATH = "./following::button[normalize-space()='Reply'][not(@disabled) and not(@aria-disabled='true')]"
OWN_PROFILE_URL = f"{BASE_URL}/in/me/"
REPLY_INDENT = 8  # px; replies sit about 40 px right of top-level comments
MAX_EXPANSIONS = 50
COMMENTS_SCRIPT = r"""
const container = document.querySelector("main [componentkey*='pagedCommentsContainer']");
if (!container) return [];
return [...container.querySelectorAll("[componentkey^='replaceableComment_urn:li:comment:']")].map(item => {
    const link = item.querySelector("a[href*='/in/'], a[href*='/company/']");
    const label = item.querySelector("[aria-label^='View '][aria-label$='profile'], img[alt^='View ']");
    const avatar = item.querySelector('figure');
    const box = item.querySelector("[data-testid='expandable-text-box']");
    let text = '';
    if (box) {
        const copy = box.cloneNode(true);
        copy.querySelectorAll("[data-testid='expandable-text-button']").forEach(node => node.remove());
        copy.querySelectorAll('br').forEach(node => node.replaceWith('\n'));
        text = copy.textContent;
    }
    return {
        key: item.getAttribute('componentkey'),
        href: link ? link.href : '',
        label: label ? (label.getAttribute('aria-label') || label.getAttribute('alt')) : '',
        text: text,
        left: avatar ? avatar.getBoundingClientRect().left : 0,
    };
});
"""


class Reaction(StrEnum):
    """Reactions supported by LinkedIn posts."""

    LIKE = "like"
    CELEBRATE = "celebrate"
    SUPPORT = "support"
    LOVE = "love"
    INSIGHTFUL = "insightful"
    FUNNY = "funny"


@dataclass(frozen=True)
class Comment:
    """A comment or reply under a post, in page order."""

    id: str  # the number in urn:li:comment:(activity:…,<id>)
    author: str
    profile: str
    text: str
    is_reply: bool
    mine: bool


def follow(session: Session, url: str) -> bool:
    """Follow the profile or company owner; return False when already following."""
    if not urlparse(url).path.startswith(("/in/", "/company/")):
        raise ValueError("follow requires a person (/in/) or company (/company/) URL")
    session.open(url)
    name = session.heading()
    company = urlparse(url).path.startswith("/company/")
    follow_path = (
        COMPANY_FOLLOW_XPATH if company else
        OWNER_CONTROL_XPATH.format(label=xpath_literal(f"Follow {name}"))
    )
    state_path = PERSON_FOLLOW_STATE_XPATH.format(
        name=xpath_literal(name), unfollow=xpath_literal(f"Unfollow {name}")
    )
    used_menu = False

    def is_following() -> bool:
        if company:
            control = session.find_optional(COMPANY_FOLLOW_XPATH, timeout=0)
            if control is not None and (
                control.get_attribute("aria-pressed") == "true"
                or (control.get_attribute("aria-label") or "").startswith("Following")
                or control.text.strip() == "Following"
            ):
                return True
        elif session.find_all(state_path):
            return True
        return used_menu and bool(session.find_all(MENU_FOLLOW_STATE_XPATH))

    if is_following():
        return False
    control = session.find_optional(follow_path)
    if control is None:
        more = session.find_optional(MORE_XPATH)
        if more is None:
            raise LinkedInError(f"Follow unavailable for {name}: no Follow or More control")
        session.click(more)
        session.pause()
        used_menu = True
        if is_following():
            return False
        control = session.find_optional(
            MENU_FOLLOW_XPATH.format(label=xpath_literal(f"Follow {name}"))
        )
    if control is None:
        raise LinkedInError(f"Follow unavailable for {name}")
    session.spend(Action.FOLLOW)
    session.click(control)
    session.pause()
    if used_menu and not is_following() and not session.find_all(MENU_FOLLOW_STATE_XPATH):
        # A menu-only Follow may close its menu; reopen it to inspect the new state.
        more = session.find_optional(MORE_XPATH)
        if more is not None:
            session.click(more)
            session.pause()
    if not session.wait_until(is_following, 10):
        raise LinkedInError(f"Follow could not be confirmed for {name}: Following state did not appear")
    return True


def react(session: Session, post_url: str, reaction: Reaction = Reaction.LIKE) -> bool:
    """React to the primary post, without replacing an existing reaction."""
    reaction = Reaction(reaction)
    session.open(post_url)
    post = _primary_post(session, post_url)
    button = session.find(REACTION_XPATH, root=post)
    if _has_reaction(button):
        return False
    original_label = button.get_attribute("aria-label") or ""
    target = button
    if reaction != Reaction.LIKE:
        session.hover(button)
        session.pause()
        target = session.find(REACTION_CHOICE_XPATH.format(label=xpath_literal(reaction.value.title())))
    session.spend(Action.REACTION)
    session.click(target)

    def confirmed() -> bool:
        current = session.find_optional(REACTION_XPATH, timeout=0, root=post)
        if current is None:
            return False
        label = current.get_attribute("aria-label") or ""
        if label.startswith("Reaction button state:"):
            return _has_reaction(current)
        return _has_reaction(current) or label != original_label

    if not session.wait_until(confirmed, 10):
        raise LinkedInError(f"{reaction.value.title()} reaction could not be confirmed on this post")
    return True


def comment(session: Session, post_url: str, text: str) -> None:
    """Submit a comment on the primary post and confirm it appears."""
    if not text.strip():
        raise ValueError("comment text must not be empty")
    session.open(post_url)
    post = _primary_post(session, post_url)
    session.click(session.find(COMMENT_BUTTON_XPATH, root=post))
    session.pause()
    editor = session.find_optional(PREFERRED_COMMENT_EDITOR_XPATH, timeout=0, root=post)
    if editor is None:
        editor = session.find(COMMENT_EDITOR_XPATH, root=post)
    text_path = COMMENT_TEXT_XPATH.format(text=xpath_literal(" ".join(text.split())))
    previous = {item.id for item in session.find_all(text_path)}
    session.type_text(editor, text)
    session.pause()
    submit = session.find(COMMENT_SUBMIT_XPATH, root=editor)
    session.spend(Action.COMMENT)
    session.click(submit)
    if not session.wait_until(
        lambda: any(item.id not in previous for item in session.find_all(text_path))
        or not (editor.get_attribute("innerText") or "").strip(), 10
    ):
        raise LinkedInError("Comment submission could not be confirmed: the new comment did not appear")


def read_comments(session: Session, post_url: str, limit: int = 200) -> list[Comment]:
    """The post's comments and replies in page order, after loading more and expanding reply threads."""
    return _read_comments(session, post_url, limit)[1]


def awaiting_reply(comments: list[Comment]) -> list[Comment]:
    """Top-level comments by others with no reply from you in their thread."""
    return [parent for parent, replies in _threads(comments) if not parent.mine and not any(r.mine for r in replies)]


def find_comment(comments: list[Comment], target: str) -> Comment:
    """The comment with id `target`, else the one top-level comment by author `target` (case-insensitive)."""
    target = target.strip()
    for item in comments:
        if item.id == target:
            return item
    matches = [c for c in comments if not c.is_reply and c.author.casefold() == target.casefold()]
    if not matches:
        raise LinkedInError(f"No comment {target!r} on this post; list them with `social comments`")
    if len(matches) > 1:
        ids = ", ".join(c.id for c in matches)
        raise LinkedInError(f"{target} has {len(matches)} comments on this post ({ids}); pass the comment id")
    return matches[0]


def reply(session: Session, post_url: str, target: str, text: str) -> tuple[Comment, bool]:
    """Reply in the thread of comment `target` (id or author, see find_comment).

    Returns the target and False, without sending, when you already posted this text in that thread,
    so a retry after an unconfirmed submission cannot post twice.
    """
    text = text.strip()
    if not text:
        raise ValueError("reply text must not be empty")
    me, comments = _read_comments(session, post_url, 200)
    target_comment = find_comment(comments, target)
    wanted = _squash(text)
    thread = next((parent, *replies) for parent, replies in _threads(comments) if target_comment in (parent, *replies))
    if any(c.mine and wanted in _squash(c.text) for c in thread):
        return target_comment, False
    item = session.find(COMMENT_BY_ID_XPATH.format(suffix=xpath_literal(f",{target_comment.id})")))
    previous = {editor.id for editor in session.find_all(EDITOR_XPATH, visible_only=False)}
    session.click(session.find(REPLY_BUTTON_XPATH, root=item))

    def new_editor() -> WebElement | None:
        return next((e for e in session.find_all(EDITOR_XPATH) if e.id not in previous), None)

    if not session.wait_until(new_editor, 10):
        raise LinkedInError(f"Reply box did not open under {target_comment.author}'s comment")
    editor = new_editor()
    mention = editor.get_attribute("innerText") or ""  # LinkedIn pre-fills an @mention of the commenter
    session.type_text(editor, text)
    session.pause()
    if _squash(editor.get_attribute("innerText") or "") != _squash(f"{mention} {text}"):
        raise LinkedInError("The reply box did not take the text as typed; nothing was sent")
    submit = session.find(REPLY_SUBMIT_XPATH, root=editor)
    known = {c.id for c in comments}
    session.spend(Action.COMMENT)
    session.click(submit)
    if not session.wait_until(
        lambda: any(
            c.mine and c.id not in known and wanted in _squash(c.text) for c in _page_comments(session, me)
        ),
        15,
    ):
        raise LinkedInError("Reply submission could not be confirmed: the new reply did not appear")
    return target_comment, True


def _read_comments(session: Session, post_url: str, limit: int) -> tuple[str, list[Comment]]:
    if limit < 1:
        raise ValueError("comment limit must be at least 1")
    session.open(OWN_PROFILE_URL)  # LinkedIn redirects /in/me/ to your own profile
    try:
        me = profile_key(session.driver.current_url or "")
    except ValueError as error:
        raise LinkedInError(f"Could not resolve your own profile from {OWN_PROFILE_URL}: {error}") from error
    session.open(post_url)
    _primary_post(session, post_url)
    session.find_optional(COMMENT_ITEM_XPATH, timeout=5)
    _load_comments(session, limit)
    return me, _page_comments(session, me)[:limit]


def _load_comments(session: Session, limit: int) -> None:
    """Expand reply threads and load more comments until the list stops growing or reaches `limit`.

    Post pages scroll inside <main>, not the window, so scrolling the last comment into view loads the next batch.
    """
    expansions = 0
    while True:
        while expansions < MAX_EXPANSIONS and (more := session.find_optional(MORE_REPLIES_XPATH, timeout=0)):
            session.click(more)
            expansions += 1
            session.pause(0.3)
        items = session.find_all(COMMENT_ITEM_XPATH, visible_only=False)
        if not items or len(items) >= limit:
            return
        count = len(items)
        session.driver.execute_script("arguments[0].scrollIntoView({block: 'end'});", items[-1])
        if not session.wait_until(lambda: len(session.find_all(COMMENT_ITEM_XPATH, visible_only=False)) > count, 6):
            return


def _page_comments(session: Session, me: str) -> list[Comment]:
    return _comments_from_page(session.driver.execute_script(COMMENTS_SCRIPT) or [], me)


def _comments_from_page(raw: list[dict], me: str) -> list[Comment]:
    """Build comments from COMMENTS_SCRIPT rows; `me` is your profile key."""
    if not raw:
        return []
    base = min(row["left"] for row in raw)
    comments: list[Comment] = []
    for row in raw:
        match = COMMENT_ID_RE.search(row["key"] or "")
        if match is None:
            continue
        href = (row["href"] or "").split("?")[0]
        comments.append(
            Comment(
                id=match.group(1),
                author=_author_name(row["label"] or ""),
                profile=href,
                text=(row["text"] or "").strip(),
                is_reply=row["left"] > base + REPLY_INDENT,
                mine=_is_profile(href, me),
            )
        )
    return comments


def _threads(comments: list[Comment]) -> list[tuple[Comment, list[Comment]]]:
    threads: list[tuple[Comment, list[Comment]]] = []
    for item in comments:
        if item.is_reply and threads:
            threads[-1][1].append(item)
        else:
            threads.append((item, []))
    return threads


def _author_name(label: str) -> str:
    match = re.fullmatch(r"View (.+?)[’']s (?:profile|page)", label.strip())
    return match.group(1) if match else label.strip()


def _is_profile(href: str, key: str) -> bool:
    try:
        return profile_key(href) == key
    except ValueError:
        return False


def _squash(text: str) -> str:
    return " ".join(text.split())


def _primary_post(session: Session, url: str) -> WebElement:
    """Resolve the requested activity, avoiding suggested posts and comment controls."""
    decoded = unquote(url)
    activity = re.search(r"urn:li:activity:\d+", decoded)
    if activity is None:
        activity_id = re.search(r"(?:^|[-/])activity-(\d+)", decoded)
        urn = f"urn:li:activity:{activity_id.group(1)}" if activity_id else None
    else:
        urn = activity.group()
    path = TARGET_POST_XPATH.format(urn=xpath_literal(urn)) if urn else POST_CONTAINER_XPATH
    post = session.find_optional(path, timeout=10)
    if post is None:
        post = session.find_optional(SDUI_POST_CONTAINER_XPATH, timeout=10)
    if post is None:
        raise LinkedInError("Post unavailable: the primary activity container was not found")
    return post


def _has_reaction(button: WebElement) -> bool:
    label = (button.get_attribute("aria-label") or "").lower()
    if label.startswith("reaction button state:"):
        state = label.partition(":")[2].strip()
        return bool(state) and state != "no reaction"
    return button.get_attribute("aria-pressed") == "true" or (
        label.startswith(("unreact ", "remove reaction"))
        or (label.startswith("react ") and not label.startswith("react like"))
    )
