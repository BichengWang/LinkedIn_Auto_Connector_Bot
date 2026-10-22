from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from selenium.webdriver.remote.webelement import WebElement

from .config import Action
from .session import BASE_URL, LinkedInError, Session, xpath_literal

log = logging.getLogger(__name__)

__all__ = ["Conversation", "Message", "send_message", "list_conversations", "open_conversation", "read_conversation", "reply"]

MESSAGE_CONTROL = "//main//a[contains(@href,'/messaging/compose/')][normalize-space()='Message'] | //main//*[self::button or self::a or @role='button'][contains(@aria-label, {name}) and contains(@aria-label, 'Message')] | //main//button[normalize-space()='Message']"
MORE_CONTROL = "//main//button[@aria-label='More' or contains(@aria-label, 'More actions') or contains(@aria-label, 'More options')]"
MENU_MESSAGE = "//*[@role='menuitem'][normalize-space()='Message' or contains(@href, '/messaging/compose/') or .//*[contains(@aria-label, {name}) and contains(@aria-label, 'Message')]]"
TEXTBOX = "//main//*[@contenteditable='true' and @role='textbox'] | //*[contains(concat(' ', normalize-space(@class), ' '), ' msg-overlay-conversation-bubble ')]//*[@contenteditable='true' and @role='textbox']"
OWNER_TEXTBOX = "//*[self::dialog or @role='dialog' or contains(concat(' ', normalize-space(@class), ' '), ' msg-overlay-conversation-bubble ')][.//*[contains(normalize-space(), {name})]]//*[@contenteditable='true' and @role='textbox'] | //main//*[@contenteditable='true' and @role='textbox']"
OVERLAY_ROOT = "./ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' msg-overlay-conversation-bubble ')][1]"
DIALOG_ROOT = "./ancestor::*[self::dialog or @role='dialog'][1]"
MAIN_ROOT = "./ancestor::main[1]"
THREAD_MAIN = "//main"
FORM_ROOT = "./ancestor::form[1]"
SEND_BUTTON = ".//button[not(@disabled) and (@type='submit' or normalize-space()='Send' or @aria-label='Send')]"
CLOSE_BUTTON = ".//button[contains(@aria-label, 'Close') or contains(@data-control-name, 'close')]"
MESSAGE_EVENTS = ".//*[@data-event-urn and not(ancestor::li[contains(concat(' ', normalize-space(@class), ' '), ' msg-s-message-list__event ')]) and not(ancestor::*[@data-event-urn])] | .//li[contains(concat(' ', normalize-space(@class), ' '), ' msg-s-message-list__event ')]"
MESSAGE_BODY = ".//*[@data-test-message-body] | .//*[contains(concat(' ', normalize-space(@class), ' '), ' msg-s-event-listitem__body ')]"
MESSAGE_SENDER = ".//*[@data-test-message-sender] | .//*[contains(concat(' ', normalize-space(@class), ' '), ' msg-s-message-group__name ')]"
CONVERSATION_ITEMS = "//li[contains(concat(' ', normalize-space(@class), ' '), ' msg-conversation-listitem ')]"
CONVERSATION_LINK = ".//div[contains(concat(' ', normalize-space(@class), ' '), ' msg-conversation-listitem__link ')]"
PARTICIPANTS = ".//*[@data-test-conversation-participant] | .//*[contains(concat(' ', normalize-space(@class), ' '), ' msg-conversation-listitem__participant-names ')]"
SNIPPET = ".//*[@data-test-conversation-snippet] | .//*[contains(concat(' ', normalize-space(@class), ' '), ' msg-conversation-card__message-snippet ')]"
UNREAD = ".//*[contains(@class, 'unread') or @aria-label='Unread' or contains(@aria-label, 'unread') or @data-unread='true']"
UNREAD_FILTER = "//button[@data-test-messaging-inbox-filters__filter-pill='UNREAD']"
THREAD_TITLE = "//main//*[contains(concat(' ', normalize-space(@class), ' '), ' msg-thread ')]//*[self::h1 or self::h2 or self::h3 or contains(concat(' ', normalize-space(@class), ' '), ' msg-entity-lockup__entity-title ')] | //main//*[contains(concat(' ', normalize-space(@class), ' '), ' msg-thread__name ')]"
UPSELL = "//*[self::dialog or @role='dialog']//*[contains(normalize-space(), 'InMail') or contains(normalize-space(), 'Premium')]"


@dataclass(frozen=True)
class Conversation:
    participants: str
    snippet: str
    unread: bool


@dataclass(frozen=True)
class Message:
    sender: str
    text: str


def _validate_text(text: str) -> None:
    if not text.strip():
        raise ValueError('Message text must not be empty')


def _message_root(session: Session, editor: WebElement) -> WebElement:
    for selector in (OVERLAY_ROOT, DIALOG_ROOT, MAIN_ROOT):
        root = session.find_optional(selector, timeout=0, root=editor)
        if root is not None:
            return root
    raise LinkedInError('Message form has no conversation container')


def _editor(session: Session, recipient: str | None = None) -> WebElement:
    selector = OWNER_TEXTBOX.format(name=xpath_literal(recipient)) if recipient is not None else TEXTBOX
    editor = session.find_optional(selector, timeout=10)
    if editor is None:
        if session.find_optional(UPSELL, timeout=0) is not None:
            raise LinkedInError('Message unavailable: InMail or Premium is required')
        raise LinkedInError('Message unavailable: no editable message box (InMail may be required)')
    return editor


def _body_count(session: Session, root: WebElement, text: str) -> int:
    normalized = ' '.join(text.split())
    return sum(' '.join(body.text.split()) == normalized for body in session.find_all(MESSAGE_BODY, root=root))


def _send(session: Session, text: str, close: bool = False, recipient: str | None = None) -> None:
    editor = _editor(session, recipient)
    root = _message_root(session, editor)
    before = _body_count(session, root, text)
    form = session.find_optional(FORM_ROOT, timeout=0, root=editor)
    session.type_text(editor, text)
    session.pause()
    button = session.find(SEND_BUTTON, root=form if form is not None else root)
    session.spend(Action.MESSAGE)
    session.click(button)
    if not session.wait_until(lambda: _body_count(session, root, text) > before, timeout=15):
        raise LinkedInError('Message send could not be confirmed in the conversation')
    session.pause()
    if close:
        overlay = session.find_optional(OVERLAY_ROOT, timeout=0, root=editor)
        if overlay is not None:
            session.click(session.find(CLOSE_BUTTON, root=overlay))
            session.pause()


def send_message(session: Session, profile_url: str, text: str) -> None:
    """Send a message to the profile owner, then close its overlay."""
    _validate_text(text)
    session.open(profile_url)
    recipient = session.heading()
    name = xpath_literal(recipient)
    control = session.find_optional(MESSAGE_CONTROL.format(name=name))
    if control is None:
        more = session.find_optional(MORE_CONTROL.format(name=name))
        if more is not None:
            session.click(more)
            session.pause()
            control = session.find_optional(MENU_MESSAGE.format(name=name))
    if control is None:
        raise LinkedInError('Message unavailable: no Message control for this profile (InMail may be required)')
    session.click(control)
    session.pause()
    _send(session, text, close=True, recipient=recipient)


def _scroll_list(session: Session, item: WebElement) -> bool:
    moved = session.driver.execute_script(
        "let e = arguments[0].parentElement; while (e && e !== document.body) {"
        "if (e.scrollHeight > e.clientHeight && /auto|scroll/.test(getComputedStyle(e).overflowY)) {"
        "const before = e.scrollTop; e.scrollTop += e.clientHeight;"
        "return e.scrollTop > before; } e = e.parentElement; } return false;", item)
    session.pause()
    return bool(moved)


def _conversation(session: Session, item: WebElement, unread_only: bool = False) -> Conversation:
    participant = session.find_optional(PARTICIPANTS, timeout=0, root=item)
    snippet = session.find_optional(SNIPPET, timeout=0, root=item)
    unread = (unread_only or 'unread' in (item.get_attribute('class') or '')
              or item.get_attribute('data-unread') == 'true'
              or session.find_optional(UNREAD, timeout=0, root=item) is not None)
    return Conversation(participant.text.strip() if participant is not None else '',
                        snippet.text.strip() if snippet is not None else '', unread)


def list_conversations(session: Session, limit: int = 20, unread_only: bool = False) -> list[Conversation]:
    """Read inbox rows without clicking threads, scrolling the list itself."""
    if limit <= 0:
        return []
    session.open(f'{BASE_URL}/messaging/')
    if unread_only:
        pill = session.find(UNREAD_FILTER)
        if pill.get_attribute('aria-pressed') != 'true':
            session.click(pill)
            session.pause()
    session.find_optional(CONVERSATION_ITEMS, timeout=10)
    conversations: dict[str, Conversation] = {}
    while True:
        items = session.find_all(CONVERSATION_ITEMS)
        previous = len(conversations)
        for item in items:
            conversations[item.id] = _conversation(session, item, unread_only)
            if len(conversations) >= limit:
                return list(conversations.values())[:limit]
        if not items:
            return list(conversations.values())
        moved = _scroll_list(session, items[-1])
        if not moved and len(conversations) == previous:
            return list(conversations.values())


def _thread_url(session: Session) -> str:
    parts = urlsplit(session.driver.current_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))


def open_conversation(session: Session, who: str) -> str:
    """Open a thread URL or an unambiguous, exact participant-name match."""
    if who.startswith('http'):
        session.open(who)
        if not session.wait_until(
                lambda: '/messaging/thread/' in session.driver.current_url
                and session.find_optional(TEXTBOX, timeout=0) is not None, timeout=15):
            raise LinkedInError(f'Conversation did not open: {who}')
        return _thread_url(session)
    wanted = ' '.join(who.split()).casefold()
    if not wanted:
        raise LinkedInError('Conversation participant name must not be empty')
    session.open(f'{BASE_URL}/messaging/')
    session.find_optional(CONVERSATION_ITEMS, timeout=10)
    seen: set[str] = set()
    matches: dict[str, tuple[WebElement, Conversation]] = {}
    while True:
        items = session.find_all(CONVERSATION_ITEMS)
        previous = len(seen)
        for item in items:
            seen.add(item.id)
            conversation = _conversation(session, item)
            if ' '.join(conversation.participants.split()).casefold() == wanted:
                matches[item.id] = (item, conversation)
        if not items:
            break
        moved = _scroll_list(session, items[-1])
        if not moved and len(seen) == previous:
            break
    if not matches:
        raise LinkedInError(f'Conversation not found: {who}')
    if len(matches) > 1:
        descriptions = [f'{c.participants}: {c.snippet}' for _, c in matches.values()]
        raise LinkedInError(f'Ambiguous conversation for {who}: ' + '; '.join(descriptions))
    item, _ = next(iter(matches.values()))
    session.click(session.find(CONVERSATION_LINK, root=item))
    if not session.wait_until(
            lambda: '/messaging/thread/' in session.driver.current_url
            and any(' '.join(title.text.split()).casefold() == wanted
                    for title in session.find_all(THREAD_TITLE)), timeout=15):
        raise LinkedInError(f'Conversation pane did not open for {who}')
    return _thread_url(session)


def read_conversation(session: Session, who: str) -> list[Message]:
    """Return loaded thread messages in display order, inheriting grouped senders."""
    open_conversation(session, who)
    root = session.find(THREAD_MAIN)
    session.find_optional(MESSAGE_EVENTS, timeout=10, root=root)
    result: list[Message] = []
    sender = ''
    for event in session.find_all(MESSAGE_EVENTS, root=root):
        author = session.find_optional(MESSAGE_SENDER, timeout=0, root=event)
        if author is not None and author.text.strip():
            sender = author.text.strip()
        for body in session.find_all(MESSAGE_BODY, root=event):
            result.append(Message(sender, body.text.strip()))
    return result


def reply(session: Session, who: str, text: str) -> None:
    """Send and confirm a reply in an existing thread."""
    _validate_text(text)
    open_conversation(session, who)
    _send(session, text)
