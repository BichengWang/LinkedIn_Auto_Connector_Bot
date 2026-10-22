"""Human-reviewed LinkedIn Easy Apply applications; never submit without approval."""
from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import Select

from .config import Action, ApplyingSettings
from .session import LinkedInError, LimitReached, Session, xpath_literal

log = logging.getLogger(__name__)

DIALOG = "//*[self::dialog or @role='dialog' or @role='alertdialog']"
EASY_DIALOG = (
    DIALOG + "[.//h2[starts-with(normalize-space(), 'Apply to')] or "
    ".//button[@aria-label='Submit application' or @aria-label='Continue to next step' or "
    "@aria-label='Review your application' or normalize-space()='Next' or "
    "normalize-space()='Review' or normalize-space()='Submit application'] or "
    ".//*[contains(normalize-space(), 'Easy Apply')]]"
)
APPLIED = (
    "//main//*[self::button or self::span or self::p]"
    "[normalize-space()='Applied' or normalize-space()='Application submitted' or "
    "(starts-with(normalize-space(), 'Applied ') and contains(normalize-space(), ' ago'))]"
    "[not(ancestor::li or ancestor::*[@role='listitem'])] | "
    "//main//a[normalize-space()='See application']"
    "[not(ancestor::li or ancestor::*[@role='listitem'])]"
)
EASY_APPLY = (
    "//main//button[@aria-label='Easy Apply to this job' or "
    "starts-with(@aria-label,'Easy Apply') or normalize-space()='Easy Apply'][not(@disabled)]"
)
COMPANY = "(//main//a[contains(@href, '/company/')])[1]"
FIELDS = ".//input[not(@type='hidden') and not(@type='file') and not(@type='radio')] | .//textarea | .//select"
RADIO_GROUPS = ".//fieldset[.//input[@type='radio']]"
RADIOS = ".//input[@type='radio']"
LEGEND = ".//legend"
FIELDSET = "./ancestor::fieldset[1]"
WRAPPING_LABEL = "./ancestor::label[1]"
LABEL_FOR = ".//label[@for={field_id}]"
LABELLED_BY = ".//*[@id={label_id}]"
STEP_HEADING = ".//h3 | .//h2"
PROGRESS = ".//*[@role='progressbar'] | .//progress"
SUBMIT = ".//button[@aria-label='Submit application' or normalize-space()='Submit application']"
NEXT = ".//button[@aria-label='Continue to next step' or normalize-space()='Next' or @aria-label='Review your application' or normalize-space()='Review']"
DISMISS = ".//button[@aria-label='Dismiss' or @aria-label='Close' or normalize-space()='Dismiss']"
DISCARD = DIALOG + "//button[normalize-space()='Discard' or normalize-space()='Discard application']"
ERRORS = ".//*[@role='alert' or @aria-live='assertive'] | .//*[contains(@class, 'artdeco-inline-feedback--error')]"
INVALID_FIELDS = ".//*[@aria-invalid='true']"
ERROR_ANCESTOR = "./ancestor::*[.//input or .//select or .//textarea][1]"
ERROR_LABELS = ".//legend | .//label"
CONFIRMATION = (
    DIALOG + "[.//*[contains(normalize-space(), 'Application sent') or "
    "contains(normalize-space(), 'Your application was sent') or "
    "contains(normalize-space(), 'application was submitted')] or "
    "@data-test-modal-id='post-apply-modal' or contains(@class, 'post-apply')]"
)


class ApplyStatus(StrEnum):
    SUBMITTED = "submitted"
    DISCARDED = "discarded"
    ALREADY_APPLIED = "already_applied"
    NOT_EASY_APPLY = "not_easy_apply"
    FAILED = "failed"


@dataclass(frozen=True)
class ApplyResult:
    job_url: str
    status: ApplyStatus
    detail: str = ""


def _normalise(text: str) -> str:
    return " ".join(text.casefold().split())


def find_answer(question: str, answers: Mapping[str, str]) -> str | None:
    """Return the most specific configured substring answer, ignoring case and spacing."""
    question = _normalise(question)
    matches = [(len(key), answer) for raw, answer in answers.items()
               if (key := _normalise(raw)) and key in question]
    return max(matches, key=lambda item: item[0])[1] if matches else None


class Reviewer(Protocol):
    def fill_manually(self, job_title: str, questions: list[str]) -> bool:
        """Wait for manual browser edits; False abandons this application."""
        ...

    def approve(self, job_title: str, company: str, filled: list[tuple[str, str]]) -> bool:
        """Return explicit permission to submit the displayed application."""
        ...


class TerminalReviewer:
    """Review application details with the person at the terminal."""

    def fill_manually(self, job_title: str, questions: list[str]) -> bool:
        print(f"\nComplete these questions for {job_title} in the browser window:")
        for question in questions:
            print(f"  - {question}")
        return input("Press Enter when done, or type s to skip this job: ").strip().casefold() != "s"

    def approve(self, job_title: str, company: str, filled: list[tuple[str, str]]) -> bool:
        print(f"\nApplication: {job_title}\nCompany: {company}\nQuestion | Answer")
        for question, answer in filled:
            print(f"{question} | {answer}")
        return input("Submit application? [y/N] ").strip().casefold() in {"y", "yes"}


def _label(session: Session, dialog: WebElement, field: WebElement) -> str:
    field_id = field.get_attribute("id")
    if field_id:
        label = session.find_optional(LABEL_FOR.format(field_id=xpath_literal(field_id)), timeout=0, root=dialog)
        if label is not None and label.text.strip():
            return label.text.strip()
    labelled_by = []
    for label_id in (field.get_attribute("aria-labelledby") or "").split():
        label = session.find_optional(LABELLED_BY.format(label_id=xpath_literal(label_id)), timeout=0, root=dialog)
        if label is not None and label.text.strip():
            labelled_by.append(label.text.strip())
    if labelled_by:
        return " ".join(labelled_by)
    label = session.find_optional(WRAPPING_LABEL, timeout=0, root=field)
    if label is not None and label.text.strip():
        return label.text.strip()
    group = session.find_optional(FIELDSET, timeout=0, root=field)
    if group is not None:
        legend = session.find_optional(LEGEND, timeout=0, root=group)
        if legend is not None and legend.text.strip():
            return legend.text.strip()
    return (field.get_attribute("aria-label") or field.get_attribute("name") or "Unlabelled required field").strip()


def _required(field: WebElement) -> bool:
    return field.get_attribute("required") is not None or field.get_attribute("aria-required") == "true"


def _option_value(select: Select) -> str:
    selected = select.first_selected_option
    text = selected.text.strip()
    return text if selected.get_attribute("value") and _normalise(text) != "select an option" else ""


def _collect(
    session: Session, dialog: WebElement, settings: ApplyingSettings, company: str, *, fill: bool,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Read the current step and optionally answer only empty supported controls."""
    values: list[tuple[str, str]] = []
    unknown: list[str] = []
    review = session.find_optional(SUBMIT, timeout=0, root=dialog) is not None
    for field in session.find_all(FIELDS, root=dialog, visible_only=False):
        kind = (field.get_attribute("type") or "text").casefold()
        tag = field.tag_name.casefold()
        if kind != "checkbox" and not field.is_displayed():
            continue
        if tag == "input" and kind not in {"text", "number", "tel", "email", "checkbox"}:
            continue
        question = _label(session, dialog, field)
        answer = find_answer(question, settings.answers)
        if kind == "checkbox":
            if review and not settings.follow_company and _normalise(question).startswith("follow "):
                if (not company or _normalise(company) in _normalise(question)) and field.is_selected():
                    label = session.find_optional(LABEL_FOR.format(field_id=xpath_literal(field.get_attribute("id") or "")), timeout=0, root=dialog)
                    session.click(label if label is not None else field)
                    session.pause()
            values.append((question, "Yes" if field.is_selected() else "No"))
            continue
        if tag == "select":
            select = Select(field)
            value = _option_value(select)
            if not value and answer is not None and answer.strip() and fill:
                options = [option for option in select.options if option.get_attribute("value")]
                exact = [option for option in options if _normalise(option.text) == _normalise(answer)]
                matches = exact or [option for option in options if _normalise(answer) in _normalise(option.text)]
                if matches:
                    select.select_by_index(select.options.index(matches[0]))
                    session.pause()
                value = _option_value(select)
        else:
            value = field.get_attribute("value") or ""
            if not value.strip() and answer is not None and fill:
                session.type_text(field, answer, clear=True)
                session.pause()
                value = field.get_attribute("value") or ""
        values.append((question, value))
        if not value.strip() and _required(field):
            unknown.append(question)
    for group in session.find_all(RADIO_GROUPS, root=dialog):
        legend = session.find_optional(LEGEND, timeout=0, root=group)
        radios = session.find_all(RADIOS, root=group, visible_only=False)
        question = legend.text.strip() if legend is not None else (group.get_attribute("aria-label") or "Unlabelled radio question")
        selected = next((radio for radio in radios if radio.is_selected()), None)
        answer = find_answer(question, settings.answers)
        if selected is None and answer is not None and answer.strip() and fill:
            choices = [(radio, _label(session, dialog, radio)) for radio in radios]
            exact = [(radio, label) for radio, label in choices if _normalise(label) == _normalise(answer)]
            matches = exact or [(radio, label) for radio, label in choices if _normalise(answer) in _normalise(label)]
            if matches:
                radio = matches[0][0]
                label = session.find_optional(LABEL_FOR.format(field_id=xpath_literal(radio.get_attribute("id") or "")), timeout=0, root=group)
                if label is None:
                    label = session.find_optional(WRAPPING_LABEL, timeout=0, root=radio)
                if label is not None:
                    session.click(label)
                    session.pause()
                    selected = next((radio for radio in radios if radio.is_selected()), None)
        values.append((question, _label(session, dialog, selected) if selected is not None else ""))
        if selected is None and (_required(group) or any(_required(radio) for radio in radios)):
            unknown.append(question)
    return values, list(dict.fromkeys(unknown))


def _step(session: Session, dialog: WebElement) -> tuple[str, str]:
    """Identify the current step by its last heading plus the progress value; headings alone repeat."""
    headings = session.find_all(STEP_HEADING, root=dialog)
    progress = session.find_optional(PROGRESS, timeout=0, root=dialog)
    value = (progress.get_attribute("aria-valuenow") or progress.get_attribute("value") or "") if progress else ""
    return (headings[-1].text.strip() if headings else ""), value


def _errors(session: Session, dialog: WebElement) -> list[str]:
    questions = [_label(session, dialog, field) for field in session.find_all(INVALID_FIELDS, root=dialog)]
    for error in session.find_all(ERRORS, root=dialog):
        if not error.text.strip():
            continue
        parent = session.find_optional(ERROR_ANCESTOR, timeout=0, root=error)
        labels = session.find_all(ERROR_LABELS, root=parent) if parent is not None else []
        question = next((label.text.strip() for label in labels if label.text.strip()), error.text.strip())
        questions.append(question)
    return list(dict.fromkeys(questions))


def _discard(session: Session) -> None:
    """Close Easy Apply and explicitly discard, rather than leaving a saved draft."""
    dialog = session.find_optional(DIALOG, timeout=0)
    if dialog is None:
        return
    discard = session.find_optional(DISCARD, timeout=0)
    if discard is None:
        session.click(session.find(DISMISS, root=dialog))
        session.pause()
        discard = session.find_optional(DISCARD, timeout=3)
    if discard is not None:
        session.click(discard)
        session.pause()
    if not session.wait_until(lambda: not session.find_all(DIALOG), timeout=5):
        raise LinkedInError("Could not close and discard the Easy Apply dialog")


def apply(session: Session, job_url: str, settings: ApplyingSettings, reviewer: Reviewer) -> ApplyResult:
    """Complete Easy Apply with manual intervention and explicit final approval."""
    if session.settings.browser.headless:
        raise LinkedInError("Easy Apply requires a visible browser for human review; disable headless mode")
    filled: dict[str, str] = {}
    try:
        session.open(job_url)
        if session.find_optional(APPLIED, timeout=1) is not None:
            return ApplyResult(job_url, ApplyStatus.ALREADY_APPLIED)
        page_title = session.driver.title.removesuffix(" | LinkedIn")
        title, separator, company = page_title.rpartition(" | ")
        if not separator:
            title, company = page_title, ""
        title, company = title.strip(), company.strip()
        if not company:
            company_element = session.find_optional(COMPANY, timeout=1)
            company = company_element.text.strip() if company_element is not None else ""
        easy_apply = session.find_optional(EASY_APPLY, timeout=3)
        if easy_apply is None:
            return ApplyResult(job_url, ApplyStatus.NOT_EASY_APPLY)
        session.click(easy_apply)
        session.pause()
        session.find(EASY_DIALOG)
        stalls = 0
        errors_seen: Counter[tuple[str, ...]] = Counter()
        for _ in range(15):
            dialog = session.find(EASY_DIALOG)
            step = _step(session, dialog)
            values, unknown = _collect(session, dialog, settings, company, fill=True)
            filled.update(values)
            if unknown:
                if not reviewer.fill_manually(title, unknown):
                    _discard(session)
                    return ApplyResult(job_url, ApplyStatus.DISCARDED, "; ".join(unknown))
                dialog = session.find(EASY_DIALOG)
                values, _ = _collect(session, dialog, settings, company, fill=False)
                filled.update(values)
            submit = session.find_optional(SUBMIT, timeout=0, root=dialog)
            if submit is not None:
                if not reviewer.approve(title, company, list(filled.items())):
                    _discard(session)
                    return ApplyResult(job_url, ApplyStatus.DISCARDED, "declined at review")
                session.spend(Action.APPLICATION)
                session.click(submit)
                def submission_finished() -> bool:
                    if session.find_all(CONFIRMATION):
                        return True
                    current = session.find_optional(EASY_DIALOG, timeout=0)
                    return current is not None and bool(_errors(session, current))

                if not session.wait_until(submission_finished, timeout=15):
                    raise LinkedInError("Submission clicked but no application confirmation appeared; check LinkedIn before retrying")
                confirmation = session.find_optional(CONFIRMATION, timeout=0)
                if confirmation is not None:
                    session.click(session.find(DISMISS, root=confirmation))
                    if not session.wait_until(lambda: not session.find_all(DIALOG), timeout=5):
                        raise LinkedInError("Application confirmed, but its confirmation dialog could not be dismissed")
                    return ApplyResult(job_url, ApplyStatus.SUBMITTED)
            else:
                session.click(session.find(NEXT, root=dialog))
                session.pause()
            dialog = session.find(EASY_DIALOG)
            errors = _errors(session, dialog)
            if errors:
                signature = tuple(errors)
                errors_seen[signature] += 1
                if errors_seen[signature] >= 3:
                    raise LinkedInError("Application validation repeated three times: " + "; ".join(errors))
                if not reviewer.fill_manually(title, errors):
                    _discard(session)
                    return ApplyResult(job_url, ApplyStatus.DISCARDED, "; ".join(errors))
                dialog = session.find(EASY_DIALOG)
                values, _ = _collect(session, dialog, settings, company, fill=False)
                filled.update(values)
            elif _step(session, dialog) == step:
                stalls += 1
                if stalls >= 3:
                    raise LinkedInError(f"Application made no progress: stuck at step {step[0]!r}")
            else:
                stalls = 0
        raise LinkedInError("Easy Apply exceeded 15 steps")
    except LimitReached:
        try:
            _discard(session)
        except (LinkedInError, WebDriverException) as exc:
            log.error("Could not discard after the application limit was reached: %s", exc)
        raise
    except (LinkedInError, WebDriverException) as exc:
        detail = str(exc)
        try:
            _discard(session)
        except (LinkedInError, WebDriverException) as cleanup_error:
            detail += f"; cleanup failed: {cleanup_error}"
        log.warning("Application failed for %s: %s", job_url, detail)
        return ApplyResult(job_url, ApplyStatus.FAILED, detail)


def apply_all(session: Session, job_urls: Iterable[str], settings: ApplyingSettings, reviewer: Reviewer) -> list[ApplyResult]:
    """Apply in order until the application cap is exhausted."""
    results: list[ApplyResult] = []
    for job_url in job_urls:
        if session.remaining(Action.APPLICATION) == 0:
            break
        if results:
            session.pause()
        try:
            result = apply(session, job_url, settings, reviewer)
        except LimitReached:
            break
        except LinkedInError as exc:
            log.warning("Application skipped for %s: %s", job_url, exc)
            result = ApplyResult(job_url, ApplyStatus.FAILED, str(exc))
        results.append(result)
        if "cleanup failed:" in result.detail:
            log.error("Stopping applications because the previous dialog could not be closed")
            break
    return results
