from __future__ import annotations

import logging
import re
from enum import StrEnum
from urllib.parse import unquote, urlparse

from selenium.webdriver.remote.webelement import WebElement

from .config import Action
from .session import LinkedInError, Session, xpath_literal

log = logging.getLogger(__name__)

__all__ = ["Reaction", "follow", "react", "comment"]

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


class Reaction(StrEnum):
    """Reactions supported by LinkedIn posts."""

    LIKE = "like"
    CELEBRATE = "celebrate"
    SUPPORT = "support"
    LOVE = "love"
    INSIGHTFUL = "insightful"
    FUNNY = "funny"


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
