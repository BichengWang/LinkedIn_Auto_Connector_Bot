import csv
from datetime import date
from pathlib import Path

from linkedin_interaction.job_collecting import (
    CollectResult,
    CollectStatus,
    find_job_file,
    job_file_name,
    read_job_meta,
    render_csv,
    render_index,
    render_job,
)
from linkedin_interaction.job_matching import Job


def _job(job_id: str = "4453042402", title: str = "Principal Engineer", company: str = "Acme") -> Job:
    return Job(job_id, f"https://www.linkedin.com/jobs/view/{job_id}/", title, company, "San Francisco, CA", False,
               "Requirements\n\n- 10+ years")


def test_file_names_slug_company_and_title_and_end_with_the_id() -> None:
    assert job_file_name(_job(title="Sr. Staff Engineer, Data | AI")) == "acme-sr-staff-engineer-data-ai-4453042402.md"
    assert job_file_name(_job(company="字节跳动", title="")) == "字节跳动-4453042402.md"
    assert job_file_name(_job(company="", title="!!!")) == "4453042402.md"


def test_find_job_file_matches_the_whole_id_even_after_a_rename(tmp_path: Path) -> None:
    # A rerun must not mistake job 14453042402's file for job 4453042402's, or skip a job it never fetched.
    (tmp_path / "other-14453042402.md").write_text("x")
    assert find_job_file(tmp_path, "4453042402") is None
    renamed = tmp_path / "acme-old-title-4453042402.md"
    renamed.write_text("x")
    assert find_job_file(tmp_path, "4453042402") == renamed
    bare = tmp_path / "999.md"
    bare.write_text("x")
    assert find_job_file(tmp_path, "999") == bare


def test_front_matter_round_trips_titles_with_yaml_special_characters(tmp_path: Path) -> None:
    job = _job(title='Engineer: "Data" & AI', company="Café #1")
    path = tmp_path / job_file_name(job)
    path.write_text(render_job(job, date(2026, 10, 24)), encoding="utf-8")
    meta = read_job_meta(path)
    assert meta["title"] == 'Engineer: "Data" & AI'
    assert meta["company"] == "Café #1"
    assert meta["collected"] == "2026-10-24"
    assert path.read_text(encoding="utf-8").endswith("## About the job\n\nRequirements\n\n- 10+ years\n")


def test_index_links_collected_files_and_failed_jobs_to_linkedin(tmp_path: Path) -> None:
    job = _job(title="Engineer | Platform")
    path = tmp_path / job_file_name(job)
    path.write_text(render_job(job, date(2026, 10, 24)), encoding="utf-8")
    index = render_index("saved", [
        CollectResult(job.id, CollectStatus.NEW, path),
        CollectResult("123", CollectStatus.FAILED, None, "no readable description"),
    ], date(2026, 10, 24))
    assert f"| 1 | Acme | [Engineer \\| Platform](<{path.name}>) | San Francisco, CA |" in index
    assert "| 2 | | [job 123](https://www.linkedin.com/jobs/view/123/) | not collected: no readable description |" in index


def test_csv_keeps_tracker_order_multiline_cells_and_jobs_that_failed(tmp_path: Path) -> None:
    job = _job(title='Engineer, "Data"')
    job = Job(job.id, job.url, job.title, job.company, job.location, False,
              "About the role\n\nBuild.\n\nRequirements\n\n- 10+ years of Python\n- Bachelor's degree\n\n"
              "Pay Range\n\n$292,000—$365,000 USD\n")
    path = tmp_path / job_file_name(job)
    path.write_text(render_job(job, date(2026, 10, 24)), encoding="utf-8")
    rows = list(csv.DictReader(render_csv([
        CollectResult("123", CollectStatus.FAILED, None, "no readable description"),
        CollectResult(job.id, CollectStatus.KEPT, path),
    ]).splitlines(keepends=True)))
    assert [row["rank"] for row in rows] == ["1", "2"]
    assert rows[0]["job_id"] == "123"
    assert rows[0]["url"] == "https://www.linkedin.com/jobs/view/123/"
    assert rows[0]["not_collected"] == "no readable description"
    assert rows[1]["title"] == 'Engineer, "Data"'
    assert rows[1]["required_qualifications"] == "10+ years of Python\nBachelor's degree"
    assert (rows[1]["min_years"], rows[1]["degrees"]) == ("10", "BS")
    assert rows[1]["salary_ranges"] == "$292,000—$365,000 USD"
    assert rows[1]["description"] == job.description.strip()
    assert rows[1]["file"] == path.name
