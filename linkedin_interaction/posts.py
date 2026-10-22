from __future__ import annotations

import logging
from dataclasses import dataclass

from selenium.webdriver.remote.webelement import WebElement

from .config import Action
from .session import BASE_URL, LinkedInError, Session

log = logging.getLogger(__name__)

__all__ = ["MAX_POST_LENGTH", "Post", "create_post", "read_feed"]

MAX_POST_LENGTH = 3000

START_POST_XPATH = "//main//*[@aria-label='Start a post' or (self::button and normalize-space()='Start a post')]"
SHARE_DIALOG_XPATH = "//*[self::dialog or @role='dialog'][.//*[@role='textbox' and @contenteditable='true']]"
POST_EDITOR_XPATH = ".//*[@role='textbox' and @contenteditable='true']"
PUBLISH_XPATH = ".//button[normalize-space()='Post'][not(@disabled) and not(@aria-disabled='true')]"
FEED_POST_XPATH = (
    "//main//*[@role='listitem'][.//button[starts-with(@aria-label,'Open control menu for post by')]]"
    " | //main//*[@data-urn[contains(., 'urn:li:activity:')] or "
    "@data-id[contains(., 'urn:li:activity:')]]"
)
AUTHOR_CONTROL_XPATH = ".//button[starts-with(@aria-label,'Open control menu for post by')]"
AUTHOR_LABEL_PREFIX = "Open control menu for post by "
SDUI_POST_TEXT_XPATH = ".//*[@data-testid='expandable-text-box']"
ACTIVITY_URN_SCRIPT = """
for (const element of [arguments[0], ...arguments[0].querySelectorAll('*')]) {
    for (const attribute of element.attributes) {
        const match = attribute.value.match(/activity:(\\d+)/);
        if (match) return 'urn:li:activity:' + match[1];
    }
}
return null;
"""
AUTHOR_XPATH = (
    ".//*[@data-test-id='actor-name']"
    " | .//a[contains(@href, '/in/') or contains(@href, '/company/')][@aria-label]"
    "//*[self::span or self::strong][@aria-hidden='true']"
    " | .//*[contains(@class, 'update-components-actor__name') or "
    "contains(@class, 'feed-shared-actor__name')]//*[@aria-hidden='true']"
)
AUTHOR_LINK_XPATH = ".//a[contains(@href, '/in/') or contains(@href, '/company/')][normalize-space()]"
POST_TEXT_XPATH = (
    ".//*[@data-test-id='post-text' or @data-test-id='main-feed-activity-card__commentary']"
    " | .//*[contains(@class, 'update-components-text') or "
    "contains(@class, 'feed-shared-update-v2__description')]"
)


@dataclass(frozen=True)
class Post:
    """A visible activity from the LinkedIn feed."""

    url: str
    author: str
    text: str


def create_post(session: Session, text: str) -> None:
    """Publish a text post after validating LinkedIn's character limit."""
    if not text.strip():
        raise ValueError("post text must not be empty")
    if len(text) > MAX_POST_LENGTH:
        raise ValueError(f"post text must be at most {MAX_POST_LENGTH} characters")
    session.open(f"{BASE_URL}/feed/")
    start = session.find_optional(START_POST_XPATH, timeout=10)
    if start is None:
        raise LinkedInError("Post creation unavailable: Start a post control was not found")
    session.click(start)
    session.pause()
    dialog = session.find(SHARE_DIALOG_XPATH)
    editor = session.find(POST_EDITOR_XPATH, root=dialog)
    session.type_text(editor, text)
    session.pause()
    publish = session.find(PUBLISH_XPATH, root=dialog)
    session.spend(Action.POST)
    session.click(publish)
    if not session.wait_until(lambda: not session.find_all(SHARE_DIALOG_XPATH), 15):
        raise LinkedInError("Post publication could not be confirmed: the share dialog remained open")


def read_feed(session: Session, limit: int = 10) -> list[Post]:
    """Read up to limit unique visible activities, stopping after three empty scrolls."""
    if limit < 0:
        raise ValueError("feed limit must not be negative")
    if limit == 0:
        return []
    session.open(f"{BASE_URL}/feed/")
    session.find_optional(FEED_POST_XPATH, timeout=10)
    posts: dict[str | tuple[str, str], Post] = {}
    empty_scrolls = 0
    while len(posts) < limit and empty_scrolls < 3:
        previous_count = len(posts)
        for container in session.find_all(FEED_POST_XPATH):
            urn = _activity_urn(session, container)
            author_control = session.find_optional(AUTHOR_CONTROL_XPATH, timeout=0, root=container)
            author = (
                (author_control.get_attribute("aria-label") or "").removeprefix(AUTHOR_LABEL_PREFIX).strip()
                if author_control is not None else ""
            )
            if not author:
                author_element = session.find_optional(AUTHOR_XPATH, timeout=0, root=container)
                if author_element is None:
                    author_element = session.find_optional(AUTHOR_LINK_XPATH, timeout=0, root=container)
                author = author_element.text.strip() if author_element is not None else ""
            text_element = session.find_optional(SDUI_POST_TEXT_XPATH, timeout=0, root=container)
            if text_element is None:
                text_element = session.find_optional(POST_TEXT_XPATH, timeout=0, root=container)
            text = (text_element.get_attribute("innerText") or "").strip() if text_element is not None else ""
            if not author and not text:
                continue
            key = urn if urn else (author, text[:200])
            if key in posts:
                continue
            posts[key] = Post(
                url=f"{BASE_URL}/feed/update/{urn}/" if urn else "",
                author=author,
                text=text,
            )
            if len(posts) >= limit:
                break
        if len(posts) >= limit:
            break
        empty_scrolls = empty_scrolls + 1 if len(posts) == previous_count else 0
        session.scroll_to_bottom()
        session.pause()
    return list(posts.values())


def _activity_urn(session: Session, container: WebElement) -> str | None:
    return session.driver.execute_script(ACTIVITY_URN_SCRIPT, container)
