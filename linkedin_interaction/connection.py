from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.remote.webelement import WebElement

from .config import Action
from .session import BASE_URL, LinkedInError, LimitReached, Session, xpath_literal

log = logging.getLogger(__name__)

__all__ = ['MAX_NOTE_LENGTH', 'ConnectStatus', 'ConnectResult', 'SearchSummary', 'connect', 'connect_from_search', 'accept_invitations']

MAX_NOTE_LENGTH = 300
OWNER_CONNECT = "//main//*[@aria-label=concat('Invite ', {name}, ' to connect')]"
MENU_CONNECT = "//*[@role='menuitem'][@aria-label=concat('Invite ', {name}, ' to connect') or .//*[@aria-label=concat('Invite ', {name}, ' to connect')] or normalize-space()='Connect']"
CONNECT_CONTROL = "./ancestor-or-self::*[self::a or self::button or @role='menuitem'][1]"
MORE_ACTIONS = "//main//button[@aria-label='More' or contains(@aria-label, 'More actions') or contains(@aria-label, 'More options')]"
OWNER_PENDING = "//main//*[contains(@aria-label, {name}) and (contains(@aria-label, 'Pending') or contains(@aria-label, 'Withdraw'))] | //main//*[self::h1 or self::h2][normalize-space()={name}]/ancestor::*[position() <= 3]//*[self::button or self::a][normalize-space()='Pending' or normalize-space()='Withdraw']"
MENU_PENDING = "//*[@role='menuitem'][normalize-space()='Pending' or normalize-space()='Withdraw' or normalize-space()='Withdraw invitation']"
MENU_CONNECTED = "//*[@role='menuitem'][normalize-space()='Remove connection' or contains(@aria-label, 'Remove connection')]"
FIRST_DEGREE = "//main//*[self::h1 or self::h2][normalize-space()={name}]/ancestor::*[position() <= 3]//*[normalize-space()='1st' or normalize-space()='· 1st' or normalize-space()='1st degree connection' or contains(@aria-label, '1st degree connection')]"
DIALOG = "//*[self::dialog or @role='dialog']"
ADD_NOTE = ".//button[normalize-space()='Add a note' or @aria-label='Add a note']"
NOTE_TEXTAREA = ".//textarea[@name='message' or @id='custom-message' or contains(@aria-label, 'note') or contains(@aria-label, 'message')] | .//textarea"
EMAIL_REQUIRED = ".//input[@type='email' or contains(@name, 'email') or contains(@id, 'email')]"
SEND_INVITATION = ".//button[not(@disabled) and (normalize-space()='Send invitation' or normalize-space()='Send' or @aria-label='Send invitation')]"
SEND_WITHOUT_NOTE = ".//button[not(@disabled) and (normalize-space()='Send without a note' or @aria-label='Send without a note')]"
DISMISS = ".//button[@aria-label='Dismiss' or @aria-label='Close' or normalize-space()='Cancel']"
SEARCH_CONNECT = "//main//*[self::button or self::a][starts-with(@aria-label, 'Invite ') and contains(@aria-label, ' to connect')]"
SEARCH_FOLLOW = "//main//*[self::button or self::a][starts-with(@aria-label, 'Follow ') and not(@disabled)]"
NEXT_PAGE = "//main//button[not(@disabled) and not(@aria-disabled='true') and (@aria-label='Next' or normalize-space()='Next')]"
ACCEPT = "//main//*[self::li or @role='listitem'][.//a[contains(@href, '/in/')]]//button[starts-with(@aria-label, 'Accept ') and contains(@aria-label, 'invitation') and not(@disabled)]"
RESULT_CARD = "./ancestor::*[@role='listitem' or self::li][1]"
PROFILE_LINK = ".//a[contains(@href, '/in/')]"


class ConnectStatus(StrEnum):
    SENT = 'sent'
    PENDING = 'pending'
    CONNECTED = 'connected'
    UNAVAILABLE = 'unavailable'


@dataclass(frozen=True)
class ConnectResult:
    target: str
    status: ConnectStatus
    detail: str = ''


@dataclass(frozen=True)
class SearchSummary:
    invited: int
    followed: int
    skipped: int
    pages: int


def _validate_note(note: str) -> None:
    if len(note) > MAX_NOTE_LENGTH:
        raise ValueError(f'Invitation note exceeds {MAX_NOTE_LENGTH} characters')


def _dismiss(session: Session, dialog: WebElement) -> None:
    button = session.find_optional(DISMISS, timeout=0, root=dialog)
    if button is None:
        raise LinkedInError('Invitation dialog cannot be dismissed safely')
    session.click(button)
    session.pause()


def _invitation(session: Session, target: str, note: str) -> ConnectResult:
    """Drive the shared modal without ever dropping a requested note."""
    dialog = session.find_optional(DIALOG, timeout=10)
    if dialog is None:
        raise LinkedInError(f'Invitation dialog did not open for {target}')
    if session.find_optional(EMAIL_REQUIRED, timeout=0, root=dialog) is not None:
        _dismiss(session, dialog)
        return ConnectResult(target, ConnectStatus.UNAVAILABLE, "LinkedIn requires this person's email address")
    if note:
        textarea = session.find_optional(NOTE_TEXTAREA, timeout=0, root=dialog)
        if textarea is None:
            add = session.find_optional(ADD_NOTE, root=dialog)
            if add is not None:
                session.click(add)
                session.pause()
                # LinkedIn can replace the dialog when opening the note editor.
                dialog = session.find(DIALOG)
                textarea = session.find_optional(NOTE_TEXTAREA, timeout=5, root=dialog)
        if session.find_optional(EMAIL_REQUIRED, timeout=0, root=dialog) is not None:
            _dismiss(session, dialog)
            return ConnectResult(target, ConnectStatus.UNAVAILABLE, "LinkedIn requires this person's email address")
        if textarea is None:
            _dismiss(session, dialog)
            return ConnectResult(target, ConnectStatus.UNAVAILABLE, 'Invitation note unavailable: free-account note limit or Premium required')
        session.type_text(textarea, note)
        session.pause()
        button = session.find(SEND_INVITATION, root=dialog)
    else:
        button = session.find_optional(SEND_WITHOUT_NOTE, root=dialog)
        if button is None:
            _dismiss(session, dialog)
            return ConnectResult(target, ConnectStatus.UNAVAILABLE, 'Send without a note is unavailable')
    session.spend(Action.CONNECTION)
    session.click(button)
    if not session.wait_until(lambda: _gone(dialog), timeout=10):
        raise LinkedInError(f'Invitation to {target} was not confirmed; LinkedIn kept the dialog open')
    session.pause()
    return ConnectResult(target, ConnectStatus.SENT)


def _gone(element: WebElement) -> bool:
    try:
        return not element.is_displayed()
    except StaleElementReferenceException:
        return True


def connect(session: Session, profile_url: str, note: str = '') -> ConnectResult:
    """Invite the profile owner, reporting existing or unavailable relationships."""
    _validate_note(note)
    session.open(profile_url)
    name = xpath_literal(session.heading())
    if session.find_optional(OWNER_PENDING.format(name=name), timeout=0) is not None:
        return ConnectResult(profile_url, ConnectStatus.PENDING)
    button = session.find_optional(OWNER_CONNECT.format(name=name))
    if button is None:
        if session.find_optional(FIRST_DEGREE.format(name=name), timeout=0) is not None:
            return ConnectResult(profile_url, ConnectStatus.CONNECTED)
        more = session.find_optional(MORE_ACTIONS, timeout=0)
        if more is not None:
            session.click(more)
            session.pause()
            if session.find_optional(MENU_PENDING, timeout=0) is not None:
                return ConnectResult(profile_url, ConnectStatus.PENDING)
            if session.find_optional(MENU_CONNECTED, timeout=0) is not None:
                return ConnectResult(profile_url, ConnectStatus.CONNECTED)
            button = session.find_optional(MENU_CONNECT.format(name=name))
    if button is None:
        return ConnectResult(profile_url, ConnectStatus.UNAVAILABLE, 'No Connect option for this profile')
    session.click(session.find(CONNECT_CONTROL, root=button))
    session.pause()
    return _invitation(session, profile_url, note)


def _target(session: Session, button: WebElement) -> tuple[str, str]:
    label = button.get_attribute('aria-label') or ''
    card = session.find_optional(RESULT_CARD, timeout=0, root=button)
    link = session.find_optional(PROFILE_LINK, timeout=0, root=card) if card is not None else None
    target = (link.get_attribute('href') or label) if link is not None else label
    return target, label


def _available(session: Session, follow: bool) -> bool:
    return session.remaining(Action.CONNECTION) > 0 or (follow and session.remaining(Action.FOLLOW) > 0)


def connect_from_search(session: Session, search_url: str, note: str = '', follow: bool = False, max_pages: int = 100) -> SearchSummary:
    """Process visible people results page by page, re-querying after each action."""
    _validate_note(note)
    invited = followed = skipped = pages = 0
    if max_pages <= 0 or not _available(session, follow):
        return SearchSummary(0, 0, 0, 0)
    session.open(search_url)
    processed: set[tuple[str, str]] = set()
    while pages < max_pages and _available(session, follow):
        session.scroll_to_bottom(times=2)
        pages += 1
        while _available(session, follow):
            candidates = [(Action.CONNECTION, SEARCH_CONNECT)]
            if follow:
                candidates.append((Action.FOLLOW, SEARCH_FOLLOW))
            selected: tuple[Action, WebElement, str, tuple[str, str]] | None = None
            for action, selector in candidates:
                if session.remaining(action) <= 0:
                    continue
                for button in session.find_all(selector):
                    target, label = _target(session, button)
                    key = (action.value, target)
                    if key not in processed:
                        selected = action, button, target, key
                        break
                if selected is not None:
                    break
            if selected is None:
                break
            action, button, target, key = selected
            processed.add(key)
            try:
                if action == Action.CONNECTION:
                    session.click(button)
                    session.pause()
                    result = _invitation(session, target, note)
                    if result.status == ConnectStatus.SENT:
                        invited += 1
                    else:
                        skipped += 1
                else:
                    session.spend(Action.FOLLOW)
                    session.click(button)
                    followed += 1
            except LimitReached:
                if not _available(session, follow):
                    return SearchSummary(invited, followed, skipped, pages)
            except LinkedInError as exc:
                skipped += 1
                log.warning('Skipping %s: %s', target, exc)
                dialog = session.find_optional(DIALOG, timeout=0)
                if dialog is not None:
                    _dismiss(session, dialog)
            session.pause()
        if pages >= max_pages or not _available(session, follow):
            break
        next_button = session.find_optional(NEXT_PAGE, timeout=0)
        if next_button is None:
            break
        old_url = session.driver.current_url
        session.click(next_button)
        if not session.wait_until(lambda: session.driver.current_url != old_url or _gone(next_button), timeout=10):
            raise LinkedInError('Search pagination did not advance after clicking Next')
        session.pause()
    return SearchSummary(invited, followed, skipped, pages)


def accept_invitations(session: Session, limit: int | None = None) -> list[str]:
    """Accept incoming invitations up to the requested limit and connection cap."""
    if limit is not None and limit < 0:
        raise ValueError('Invitation limit must not be negative')
    accepted: list[str] = []
    if limit == 0 or session.remaining(Action.CONNECTION) <= 0:
        return accepted
    session.open(f'{BASE_URL}/mynetwork/invitation-manager/received/')
    session.find_optional(ACCEPT, timeout=10)
    attempted: set[str] = set()
    while (limit is None or len(accepted) < limit) and session.remaining(Action.CONNECTION) > 0:
        buttons = session.find_all(ACCEPT)
        button = next((item for item in buttons if (item.get_attribute('aria-label') or '') not in attempted), None)
        if button is None:
            session.scroll_to_bottom()
            buttons = session.find_all(ACCEPT)
            button = next((item for item in buttons if (item.get_attribute('aria-label') or '') not in attempted), None)
            if button is None:
                break
        label = button.get_attribute('aria-label') or ''
        attempted.add(label)
        if label.startswith('Accept invitation for '):
            name = label.removeprefix('Accept invitation for ').strip()
        else:
            name = label.removeprefix('Accept ').removesuffix("'s invitation").removesuffix('’s invitation').strip()
        try:
            session.spend(Action.CONNECTION)
            session.click(button)
            if not session.wait_until(lambda: _gone(button), timeout=10):
                raise LinkedInError(f'Acceptance was not confirmed for {name}')
            accepted.append(name)
        except LimitReached:
            break
        except LinkedInError as exc:
            log.warning('Skipping invitation from %s: %s', name, exc)
        session.pause()
    return accepted
