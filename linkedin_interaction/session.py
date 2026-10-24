"""Browser session shared by every module: driver lifecycle, login, DOM helpers, pacing and per-run caps.

Feature modules take a started, logged-in `Session` and keep their own LinkedIn selectors, so a LinkedIn
UI change touches only the module that uses that page.
"""

from __future__ import annotations

import json
import logging
import random
import sys
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import WebDriverWait

from .config import Action, BrowserSettings, Settings

log = logging.getLogger(__name__)

BASE_URL = "https://www.linkedin.com"
FEED_URL = f"{BASE_URL}/feed/"
LOGIN_URL = f"{BASE_URL}/login"
_SELECT_ALL = Keys.COMMAND if sys.platform == "darwin" else Keys.CONTROL


class LinkedInError(Exception):
    """A LinkedIn flow could not complete."""


class ElementMissing(LinkedInError):
    """An expected page element did not appear; LinkedIn's UI may have changed."""


class LimitReached(LinkedInError):
    """The per-run cap for an action is used up."""


def xpath_literal(text: str) -> str:
    """Quote `text` as an XPath 1.0 string literal, even when it contains both quote kinds."""
    if "'" not in text:
        return f"'{text}'"
    if '"' not in text:
        return f'"{text}"'
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in text.split("'")) + ")"


class Session:
    """A LinkedIn browser session. Use as a context manager: `with Session(settings) as s: s.login()`."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._driver: WebDriver | None = None
        self._used: Counter[Action] = Counter()

    def __enter__(self) -> Session:
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def start(self) -> None:
        if self._driver is None:
            self._driver = _create_driver(self.settings.browser)

    def close(self) -> None:
        if self._driver is not None:
            self._driver.quit()
            self._driver = None

    @property
    def driver(self) -> WebDriver:
        if self._driver is None:
            raise RuntimeError("session not started; use `with Session(settings) as session:`")
        return self._driver

    # --- authentication -------------------------------------------------------------------------

    def login(self, timeout: float = 300) -> None:
        """Sign in, trying in order: the persistent profile's session, the cookie file in LINKEDIN_COOKIES,
        LINKEDIN_USERNAME/LINKEDIN_PASSWORD, and finally a manual login. Waits for the user on 2FA/captcha.

        A logged-out visit to /feed/ redirects to a login or guest page, so landing on /feed means signed in.
        """
        self.open(FEED_URL)
        if self._on_feed():
            log.info("Using the existing LinkedIn session.")
            return
        if self.settings.cookies_file is not None and self._import_cookies(self.settings.cookies_file):
            log.info("Signed in with the session cookies from %s.", self.settings.cookies_file)
            return
        self.open(LOGIN_URL)
        username = self.find_optional("//input[@id='username']", timeout=10)
        if username is not None and self.settings.username and self.settings.password:
            self.type_text(username, self.settings.username, clear=True)
            password = self.find("//input[@id='password']")
            self.type_text(password, self.settings.password, clear=True)
            password.send_keys(Keys.RETURN)
            log.info("Submitted credentials; complete any verification in the browser window.")
        else:
            log.info("Log in to LinkedIn in the browser window.")
        if not self.wait_until(self._on_feed, timeout):
            raise LinkedInError(f"login did not reach the LinkedIn feed within {timeout:.0f}s")
        log.info("Logged in to LinkedIn.")

    def _on_feed(self) -> bool:
        # Chrome reports a null URL mid-navigation (e.g. right after the login form submits).
        return "/feed" in (self.driver.current_url or "")

    def _import_cookies(self, path: Path) -> bool:
        """Load a JSON cookie export (a list of {name, value, domain, …}, as Playwright and the LinkedIn MCP
        server's ~/.linkedin-mcp/cookies.json write) into the browser; True when that reaches the feed.

        The browser profile keeps the imported session, so later runs need neither the file nor a password.
        """
        try:
            cookies = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            log.warning("Cannot read LINKEDIN_COOKIES file %s: %s", path, error)
            return False
        added = 0
        for cookie in cookies if isinstance(cookies, list) else []:
            domain = str(cookie.get("domain", ""))
            if not (cookie.get("name") and domain.lstrip(".").endswith("linkedin.com")):
                continue
            entry = {
                "name": cookie["name"],
                "value": str(cookie.get("value", "")),
                "domain": domain,
                "path": cookie.get("path") or "/",
                "secure": bool(cookie.get("secure")),
                "httpOnly": bool(cookie.get("httpOnly")),
            }
            expires = cookie.get("expires", cookie.get("expiry", -1))
            if isinstance(expires, (int, float)) and expires > 0:
                if expires < time.time():
                    continue
                entry["expiry"] = int(expires)
            if cookie.get("sameSite") in ("Strict", "Lax", "None"):
                entry["sameSite"] = cookie["sameSite"]
            try:
                self.driver.add_cookie(entry)
                added += 1
            except WebDriverException as error:
                log.debug("Skipped cookie %s: %s", cookie["name"], error.msg)
        if not added:
            log.warning("LINKEDIN_COOKIES file %s has no usable linkedin.com cookies.", path)
            return False
        self.open(FEED_URL)
        if self._on_feed():
            return True
        log.warning("The cookies in %s did not sign in (expired session?); trying the next login method.", path)
        return False

    # --- navigation and waiting -----------------------------------------------------------------

    def open(self, url: str) -> None:
        self.driver.get(url)
        self.wait_until(lambda: self.driver.execute_script("return document.readyState") == "complete", 30)
        self.pause(0.5)

    def heading(self, timeout: float = 10) -> str:
        """The page's name heading: <h1> where present (company pages), else the first <h2> (SDUI profiles
        name the person in an <h2>). Job pages have neither; read `driver.title` there instead."""
        first = self.find("(//main//h1 | //main//h2)[1]", timeout)
        h1 = self.find_optional("//main//h1", timeout=0)
        return (h1 if h1 is not None else first).text.strip()

    def wait_until(self, condition: Callable[[], object], timeout: float = 10) -> bool:
        """Poll `condition` until it is truthy; False on timeout. Stale-element errors count as not yet."""
        try:
            WebDriverWait(self.driver, timeout, ignored_exceptions=(StaleElementReferenceException,)).until(
                lambda _: condition()
            )
            return True
        except TimeoutException:
            return False

    def pause(self, scale: float = 1.0) -> None:
        """Sleep a random human-like interval from [pacing], multiplied by `scale`."""
        pacing = self.settings.pacing
        time.sleep(random.uniform(pacing.min_delay, pacing.max_delay) * scale)

    def scroll_to_bottom(self, times: int = 1) -> None:
        for _ in range(times):
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            self.pause(0.3)

    # --- element lookup (XPath; pass `root` with a relative `.//` path to search inside an element) --

    def find(self, xpath: str, timeout: float = 10, root: WebElement | None = None) -> WebElement:
        """First displayed match, waiting up to `timeout`; raises ElementMissing."""
        element = self.find_optional(xpath, timeout, root)
        if element is None:
            raise ElementMissing(f"element not found (LinkedIn UI may have changed): {xpath}")
        return element

    def find_optional(self, xpath: str, timeout: float = 3, root: WebElement | None = None) -> WebElement | None:
        """First displayed match, waiting up to `timeout`; None when absent."""
        context = root if root is not None else self.driver
        found: list[WebElement] = []

        def first_displayed() -> bool:
            for element in context.find_elements(By.XPATH, xpath):
                if _displayed(element):
                    found.append(element)
                    return True
            return False

        return found[0] if self.wait_until(first_displayed, timeout) else None

    def find_all(self, xpath: str, root: WebElement | None = None, visible_only: bool = True) -> list[WebElement]:
        """All current matches, without waiting."""
        context = root if root is not None else self.driver
        elements = context.find_elements(By.XPATH, xpath)
        return [e for e in elements if _displayed(e)] if visible_only else elements

    # --- interaction ----------------------------------------------------------------------------

    def click(self, element: WebElement) -> None:
        """Scroll `element` to the viewport centre and click it; fall back to a JS click when covered."""
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        time.sleep(0.3)
        try:
            element.click()
        except (ElementClickInterceptedException, ElementNotInteractableException):
            self.driver.execute_script("arguments[0].click();", element)

    def hover(self, element: WebElement) -> None:
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        ActionChains(self.driver).move_to_element(element).perform()

    def type_text(self, element: WebElement, text: str, clear: bool = False) -> None:
        """Type into an input, textarea or contenteditable editor.

        Newlines are sent as Shift+Enter so chat boxes don't submit early. Lines with characters outside
        the Basic Multilingual Plane (emoji) are inserted via execCommand, which ChromeDriver requires.
        """
        self.click(element)
        if clear:
            element.send_keys(_SELECT_ALL, "a")
            element.send_keys(Keys.DELETE)
        for index, line in enumerate(text.split("\n")):
            if index:
                ActionChains(self.driver).key_down(Keys.SHIFT).send_keys(Keys.ENTER).key_up(Keys.SHIFT).perform()
            if not line:
                continue
            if all(ord(char) <= 0xFFFF for char in line):
                element.send_keys(line)
            else:
                self.driver.execute_script("document.execCommand('insertText', false, arguments[0]);", line)
        time.sleep(0.3)

    # --- per-run caps ---------------------------------------------------------------------------

    def remaining(self, action: Action) -> int:
        return max(0, self.settings.limits[action] - self._used[action])

    def used(self, action: Action) -> int:
        return self._used[action]

    def spend(self, action: Action) -> None:
        """Reserve one unit of `action`; call immediately before the irreversible click. Raises LimitReached."""
        if self.remaining(action) <= 0:
            raise LimitReached(f"per-run limit reached: {action.value} = {self.settings.limits[action]}")
        self._used[action] += 1


def _displayed(element: WebElement) -> bool:
    try:
        return element.is_displayed()
    except StaleElementReferenceException:
        return False


def _create_driver(browser: BrowserSettings) -> WebDriver:
    profile = browser.resolved_profile_dir
    profile.mkdir(parents=True, exist_ok=True)
    if browser.name == "chrome":
        options = webdriver.ChromeOptions()
        options.add_argument(f"--user-data-dir={profile}")
        options.add_argument("--window-size=1400,1000")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        if browser.headless:
            options.add_argument("--headless=new")
        if browser.binary:
            options.binary_location = str(browser.binary)
        return webdriver.Chrome(options=options)

    options = webdriver.FirefoxOptions()
    options.add_argument("-profile")
    options.add_argument(str(profile))
    if browser.headless:
        options.add_argument("-headless")
    if browser.binary:
        options.binary_location = str(browser.binary)
    driver = webdriver.Firefox(options=options)
    driver.set_window_size(1400, 1000)
    return driver
