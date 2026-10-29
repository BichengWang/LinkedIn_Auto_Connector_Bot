"""Pull the requirements out of a job description's plain text, as collected from LinkedIn's "About the job".

Postings mark sections with short heading lines ("Minimum Qualifications", "You might thrive in this role if
you", "Bonus points for"), so a heading decides what the lines under it are: required, preferred or neither.
These are heuristics over free text; the full description stays the source of truth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Headings that open a required or a preferred section. Checked on short heading lines only.
_PREFERRED = re.compile(
    r"prefer|nice[- ]to[- ]have|bonus|strong candidates may|ideally|extra credit|desired|\bplus\b|good to have"
    r"|additional qualifications|even better|better if|stand out|set you apart|differentiators", re.IGNORECASE)
_REQUIRED = re.compile(
    r"qualifications?|requirements?\b|you have|you[’']?ll (?:need|bring)|you bring|expect you to|your background"
    r"|ideal candidate|looking for|what we look for|what we need to see|good fit if you|thrive in this role if you"
    r"|excited about you because|your expertise|who you (?:might be|are)|the person|about you|must[- ]have|skills"
    r"|experience\b|academic credentials|education|perfect for you if you|you should have|what it takes"
    r"|our person if|you need|will need|^you are$", re.IGNORECASE)
# Headings that close a section: the role, the company, pay, benefits, legal boilerplate.
_OTHER = re.compile(
    r"^(?:about|responsibilit|key (?:job )?responsibilit|what you[’']?ll (?:do|build|be doing)"
    r"|what you will (?:do|be doing)"
    r"|you will\b|in this role|the role|role\b|benefit|compensation|salary|annual salary|pay\b|base salary"
    r"|logistics|location|your location|working location|equal|our commitment|privacy|how we|why join"
    r"|team description|the team|overview|perks|come work|ready to|see below|relocation|working at"
    r"|employee pay|job (?:description|category|id)|description|a typical day|the difference you will make"
    r"|what you do at|how do you want|who we are|our benefits|full-time employees|what else|the community"
    r"|what we offer|additional information|compliance|us based)"
    r"|in-office requirement|itar requirement|physical requirement", re.IGNORECASE)
# Words that make a heading a requirements heading even when it also matches `_OTHER` ("About You",
# "You Will Thrive In This Role If You Have"); the boilerplate "… requirement" headings stay excluded.
_REQUIREMENTS_WORD = re.compile(
    r"(?<!in-office )(?<!itar )(?<!physical )(?:qualif|requirement)|thrive|fit if you|about you|you have"
    r"|expect you|your background", re.IGNORECASE)
# A heading-shaped line that is really a requirement ("Experience shipping agentic systems").
_REQUIREMENT_LINE = re.compile(
    r"^(?:experience|\d|bachelor|master|ph\.?d|ms\b|bs\b|strong|deep|proven|familiar|knowledge|ability|expert"
    r"|proficien|hands-on|demonstrated)", re.IGNORECASE)
_BOILERPLATE = re.compile(
    r"equal (?:employment )?opportunity|reasonable accommodation|affirmative action|pay transparency", re.IGNORECASE)
# LinkedIn sometimes runs Google's headings into the previous sentence: "…hiring process.Minimum qualifications:".
_GLUED_HEADING = re.compile(r"(?<=\S)((?:Minimum|Preferred|Basic|Required) qualifications:|About the job)")

_AMOUNT = (r"(?:USD\s*)?(?:US\$|\$)?\s?\d{2,3}(?:,\d{3})+(?:\.\d\d)?\s?(?:USD)?"
           r"|(?:USD\s*)?(?:US\$|\$)\s?\d[\d,]*(?:\.\d+)?\s?[kK]"
           r"|(?:US\$|\$)\s?\d{5,7}(?:\.\d\d)?")
_PER_YEAR = r"(?:\s*(?:/\s*(?:year|yr)|per\s+year|annually))?"
_SALARY_RANGE = re.compile(rf"(?:{_AMOUNT}){_PER_YEAR}\s*(?:[-–—]|to)\s*(?:{_AMOUNT}){_PER_YEAR}", re.IGNORECASE)
_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:or more\s+|plus\s+)?(?:years|yrs)\b", re.IGNORECASE)
_DEGREES = (
    ("PhD", re.compile(r"\bph\.?\s?d\b|doctora", re.IGNORECASE)),
    ("MS", re.compile(r"\bmaster|\bm\.?s\.?\b|\bmsc\b", re.IGNORECASE)),
    ("BS", re.compile(r"\bbachelor|\bb\.?s\.?\b|\bbsc\b|\bb\.?a\.?\b|undergraduate degree", re.IGNORECASE)),
)


@dataclass(frozen=True)
class Requirements:
    required: tuple[str, ...]
    preferred: tuple[str, ...]
    salary_ranges: tuple[str, ...]
    min_years: int | None  # smallest "N+ years" in the required section
    max_years: int | None  # largest one, since postings often ask for several ("8 years …, 5 years of ML")
    degrees: tuple[str, ...]  # PhD, MS, BS as named in the required section


def _is_heading(line: str) -> bool:
    return (2 < len(line) < 80 and not line.startswith(("- ", "• ", "* "))
            and not line.endswith((".", ",", ";")) and not re.search(r"\$\d", line))


def _heading_kind(heading: str) -> str | None:
    if _OTHER.match(heading) and not _REQUIREMENTS_WORD.search(heading):
        return None
    if len(heading) >= 60:
        return None
    if _PREFERRED.search(heading) and "ideal candidate" not in heading.lower():
        return "preferred"
    if _REQUIRED.search(heading):
        return "required"
    return None


def _sections(description: str) -> tuple[list[str], list[str]]:
    required: list[str] = []
    preferred: list[str] = []
    current: list[str] | None = None
    for raw in _GLUED_HEADING.sub(r"\n\1", description).splitlines():
        line = raw.strip()
        if not line:
            continue
        if _is_heading(line):
            heading = line.rstrip(":").strip()
            if current is not None and len(heading) > 25 and _REQUIREMENT_LINE.match(heading):
                current.append(line)
                continue
            current = {"required": required, "preferred": preferred}.get(_heading_kind(heading) or "")
            continue
        if current is not None:
            current.append(line)
    return required, preferred


def _items(lines: list[str]) -> tuple[str, ...]:
    return tuple(re.sub(r"^[-•*]\s+", "", line) for line in lines if not _BOILERPLATE.search(line))


def extract_requirements(description: str) -> Requirements:
    required_lines, preferred_lines = _sections(description)
    required, preferred = _items(required_lines), _items(preferred_lines)
    required_text = "\n".join(required)
    years = [int(n) for n in _YEARS.findall(required_text) if 0 < int(n) <= 30]
    return Requirements(
        required=required,
        preferred=preferred,
        salary_ranges=tuple(dict.fromkeys(match.strip() for match in _SALARY_RANGE.findall(description))),
        min_years=min(years, default=None),
        max_years=max(years, default=None),
        degrees=tuple(name for name, pattern in _DEGREES if pattern.search(required_text)),
    )
