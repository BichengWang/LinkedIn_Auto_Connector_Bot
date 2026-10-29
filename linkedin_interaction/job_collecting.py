"""Job collecting: save the jobs in LinkedIn's job tracker (Saved, Applied, …) as local Markdown files.

Each job becomes `<company>-<title>-<job id>.md` holding the posting's full description, for interview prep.
`<stage>-jobs.md` indexes one tracker stage in LinkedIn's order, and `<stage>-jobs.csv` lists the same jobs with
their requirements, salary ranges and full descriptions for a spreadsheet. Reruns fetch only jobs without a file
yet, so notes kept beside the files survive; `refresh` rewrites the job files from LinkedIn.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

from .job_matching import Job, fetch_description
from .job_requirements import extract_requirements
from .session import BASE_URL, ElementMissing, LinkedInError, Session

log = logging.getLogger(__name__)

CSV_COLUMNS = (
    "rank", "job_id", "company", "title", "location", "salary_ranges", "min_years", "max_years", "degrees",
    "required_qualifications", "preferred_qualifications", "url", "collected", "file", "not_collected",
    "description",
)

STAGES = ("saved", "applied", "interview", "archived")
TRACKER_JOB_LINK = "//main//a[contains(@href, '/jobs/view/')]"
TRACKER_EMPTY = "//main//*[normalize-space()='No matches']"
TRACKER_NEXT = "//main//button[@data-testid='pagination-controls-next-button-visible'][not(@disabled)]"
MAX_TRACKER_PAGES = 100  # 10 jobs a page


class CollectStatus(StrEnum):
    NEW = "new"
    REFRESHED = "refreshed"
    KEPT = "kept"
    FAILED = "failed"


@dataclass(frozen=True)
class CollectResult:
    job_id: str
    status: CollectStatus
    path: Path | None = None  # None only when FAILED
    detail: str = ""


def tracker_url(stage: str) -> str:
    return f"{BASE_URL}/jobs-tracker/?stage={stage}"


def job_url(job_id: str) -> str:
    return f"{BASE_URL}/jobs/view/{job_id}/"


def _page_job_ids(session: Session) -> list[str]:
    ids: list[str] = []
    for link in session.find_all(TRACKER_JOB_LINK, visible_only=False):
        found = re.search(r"/jobs/view/(\d+)", link.get_attribute("href") or "")
        if found and found.group(1) not in ids:
            ids.append(found.group(1))
    return ids


def tracker_job_ids(session: Session, stage: str) -> list[str]:
    """Job ids in one job-tracker stage, in LinkedIn's order, across every page."""
    session.open(tracker_url(stage))
    if not session.wait_until(lambda: _page_job_ids(session) or session.find_all(TRACKER_EMPTY), 20):
        raise ElementMissing(f"the job tracker's {stage} list did not load (LinkedIn UI may have changed)")
    ids: dict[str, None] = {}
    for _ in range(MAX_TRACKER_PAGES):
        page = _page_job_ids(session)
        ids.update(dict.fromkeys(page))
        next_button = session.find_optional(TRACKER_NEXT, timeout=0)
        if next_button is None or not page:
            break
        session.click(next_button)
        if not session.wait_until(lambda: (now := _page_job_ids(session)) and now[0] != page[0], 20):
            raise LinkedInError(f"the job tracker's {stage} list did not turn to the next page")
        session.pause(0.3)
    return list(ids)


def job_file_name(job: Job) -> str:
    stem = re.sub(r"[\W_]+", "-", f"{job.company} {job.title}".lower()).strip("-")[:80].rstrip("-")
    return f"{stem}-{job.id}.md" if stem else f"{job.id}.md"


def find_job_file(out_dir: Path, job_id: str) -> Path | None:
    """The file collected for `job_id`, if any; the name's prefix may have changed since."""
    for path in out_dir.glob(f"*{job_id}.md"):
        if path.stem == job_id or path.stem.endswith(f"-{job_id}"):
            return path
    return None


def render_job(job: Job, collected: date) -> str:
    meta = {
        "job_id": job.id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "url": job.url,
        "collected": collected.isoformat(),
    }
    # JSON strings are valid YAML scalars, so titles with colons or quotes stay readable front matter.
    front = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in meta.items())
    heading = " · ".join(part for part in (job.title, job.company) if part)
    where = f"{job.location} · " if job.location else ""
    return (
        f"---\n{front}\n---\n\n# {heading}\n\n{where}[Job posting on LinkedIn]({job.url})\n\n"
        f"## About the job\n\n{job.description}\n"
    )


def read_job_meta(path: Path) -> dict[str, str]:
    """Front matter of a file written by `render_job`; {} when it has none."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        return {}
    meta: dict[str, str] = {}
    for line in lines[1:]:
        if line == "---":
            return meta
        key, _, value = line.partition(": ")
        try:
            meta[key] = str(json.loads(value))
        except ValueError:
            meta[key] = value
    return {}


def read_job_description(path: Path) -> str:
    """The "About the job" text of a file written by `render_job`."""
    return path.read_text(encoding="utf-8").partition("\n## About the job\n\n")[2].strip()


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_index(stage: str, results: list[CollectResult], updated: date) -> str:
    rows = []
    for number, result in enumerate(results, 1):
        if result.path is None:
            rows.append(f"| {number} | | [job {result.job_id}]({job_url(result.job_id)}) | "
                        f"not collected: {_cell(result.detail)} |")
            continue
        meta = read_job_meta(result.path)
        title = _cell(meta.get("title") or f"job {result.job_id}")
        rows.append(f"| {number} | {_cell(meta.get('company', ''))} | [{title}](<{result.path.name}>) | "
                    f"{_cell(meta.get('location', ''))} |")
    return (
        f"# LinkedIn {stage} jobs\n\n{len(results)} job(s) from [the job tracker]({tracker_url(stage)}), "
        f"updated {updated.isoformat()}.\n\n| # | Company | Title | Location |\n| --- | --- | --- | --- |\n"
        + "".join(row + "\n" for row in rows)
    )


def render_csv(results: list[CollectResult]) -> str:
    """One row per job in tracker order. List cells put one item per line; a job never collected keeps only
    its id, link and the reason."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for rank, result in enumerate(results, 1):
        row = {"rank": rank, "job_id": result.job_id, "url": job_url(result.job_id)}
        if result.path is None:
            writer.writerow(row | {"not_collected": result.detail})
            continue
        meta = read_job_meta(result.path)
        description = read_job_description(result.path)
        requirements = extract_requirements(description)
        writer.writerow(row | {
            "company": meta.get("company", ""),
            "title": meta.get("title", ""),
            "location": meta.get("location", ""),
            "salary_ranges": "\n".join(requirements.salary_ranges),
            "min_years": requirements.min_years,
            "max_years": requirements.max_years,
            "degrees": ", ".join(requirements.degrees),
            "required_qualifications": "\n".join(requirements.required),
            "preferred_qualifications": "\n".join(requirements.preferred),
            "url": meta.get("url") or row["url"],
            "collected": meta.get("collected", ""),
            "file": result.path.name,
            "description": description,
        })
    return out.getvalue()


def collect_jobs(session: Session, out_dir: Path, stage: str = "saved", refresh: bool = False,
                 limit: int | None = None, today: date | None = None) -> list[CollectResult]:
    """Write one file per job in the tracker stage (the first `limit` only, when given) plus the stage index
    and CSV."""
    if stage not in STAGES:
        raise ValueError(f"unknown job tracker stage {stage!r}; choose from {', '.join(STAGES)}")
    today = today or date.today()
    ids = tracker_job_ids(session, stage)
    log.info("The job tracker lists %d %s job(s).", len(ids), stage)
    if limit is not None:
        ids = ids[:limit]
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[CollectResult] = []
    for job_id in ids:
        existing = find_job_file(out_dir, job_id)
        if existing is not None and not refresh:
            results.append(CollectResult(job_id, CollectStatus.KEPT, existing))
            continue
        try:
            job = fetch_description(session, Job(job_id, job_url(job_id), "", "", "", False))
        except LinkedInError as error:
            log.warning("Job %s: %s", job_id, error)
            results.append(CollectResult(job_id, CollectStatus.FAILED, existing, str(error)))
            continue
        path = out_dir / job_file_name(job)
        path.write_text(render_job(job, today), encoding="utf-8")
        if existing is not None and existing != path:
            existing.unlink()
        status = CollectStatus.NEW if existing is None else CollectStatus.REFRESHED
        results.append(CollectResult(job_id, status, path))
        session.pause()
    (out_dir / f"{stage}-jobs.md").write_text(render_index(stage, results, today), encoding="utf-8")
    # The BOM lets Excel and Numbers read the CSV as UTF-8.
    (out_dir / f"{stage}-jobs.csv").write_text(render_csv(results), encoding="utf-8-sig")
    return results
