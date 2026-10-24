---
name: linkedin-job-collector
description: Collect the jobs in the user's LinkedIn job tracker (Saved by default; also Applied, Interview, Archived) into a local folder, one Markdown file per job with its full description and requirements, for interview prep. Use for requests to collect, download, export or sync saved LinkedIn jobs, or to prepare for an interview from them.
---

# LinkedIn job collector

Runs this repo's `linkedin-interaction jobs collect`. It reads LinkedIn's job tracker
(`/jobs-tracker/?stage=saved`) page by page in a real Chrome window, opens each job and saves its
"About the job" text: responsibilities, qualifications, salary range when posted. List items keep their
`- ` bullets.

The MCP `get_saved_jobs` tool stops at 10 tracker pages (100 jobs) and `get_job_details` returns the whole
page with LinkedIn's sidebars, so use the CLI.

## Setup (once per machine)

From the repo root:

```bash
uv venv venv && uv pip install --python venv/bin/python -e .
```

Sign-in is the same as every `linkedin-interaction` command: the session saved in
`~/.linkedin-interaction/chrome-profile`, else `LINKEDIN_COOKIES=~/.linkedin-mcp/cookies.json` (the LinkedIn
MCP server's session, imported once), else `LINKEDIN_USERNAME`/`LINKEDIN_PASSWORD` from the environment, else
a manual login in the window. On a login page, captcha or `/checkpoint/`, ask the user to finish it in the
window and rerun. Never loop on it.

## Run

`V="venv/bin/linkedin-interaction"`. One Chrome window per run; never start two at once, they share one browser
profile.

| Goal | Command |
| --- | --- |
| Every saved job into `~/linkedin-jobs` | `$V jobs collect` |
| Another folder | `$V jobs collect --out "<folder>"` |
| Another tracker tab | `$V jobs collect --stage applied` (`saved`, `applied`, `interview`, `archived`) |
| Only the N most recently saved | `$V jobs collect --limit 10` |
| Re-fetch jobs that already have a file | `$V jobs collect --refresh` |

- Reading the tracker takes about 3 s a page (10 jobs). Each new job then takes 5–10 s, so a first run over
  about 100 jobs runs 10–15 minutes. Run it in the background, without a short timeout.
- Reruns fetch only jobs without a file, so a daily rerun takes about a minute.
- `--refresh` overwrites the job files. Keep the user's own notes in other files.

Output, all in the folder:

- `<company>-<title>-<job id>.md`: YAML front matter (`job_id`, `title`, `company`, `location`, `url`,
  `collected`), then `## About the job` with the full description.
- `<stage>-jobs.md`: a table of the jobs in tracker order, linking each file. A job that failed to load
  links to LinkedIn instead, with the reason.
- Files of jobs since removed from the tracker stay where they are.

The command prints one line per job (`new`, `refreshed`, `kept` or `failed`) and a count summary. Report the
counts, the folder and any failures.

## Interview prep

When the user names a job, find its file with `grep -l` on the company or title in the folder and read it.
Write prep material, such as requirements mapped to the user's experience, likely questions or gaps to
study, only when asked, into `<same name>.prep.md` beside the job file. `--refresh` never touches those
files.

## Rules

- Read-only on LinkedIn: never apply, save, unsave, archive, message or follow from this skill.
- Collect only the user's own tracker. The folder holds the user's job search, so keep it out of this repo
  and out of git.
