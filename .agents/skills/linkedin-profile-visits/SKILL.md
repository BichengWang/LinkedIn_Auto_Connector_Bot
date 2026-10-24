---
name: linkedin-profile-visits
description: View LinkedIn profiles as the signed-in user so they appear in each person's "Who viewed your profile", via this repo's `linkedin-interaction visit` CLI. Use for explicit requests to visit, view or warm up LinkedIn profiles, e.g. while invitations are blocked.
---

# LinkedIn profile visits

A profile view is a passive warm-up: with LinkedIn's Settings → Visibility → Profile viewing options set to
name and headline, each person sees your name under "Who viewed your profile" and often views back or connects.
It sends nothing, so it also works while LinkedIn has invitations blocked for the week.

LinkedIn counts a view when a signed-in browser renders the profile page. A bare HTTP call to its internal
API does not reliably register one, so this skill drives a real Chrome window through the repo's
`linkedin_interaction` package (Selenium), never raw HTTP.

## Setup (once per machine)

From the repo root:

```bash
uv venv venv && uv pip install --python venv/bin/python -e .
```

Sign-in uses local credentials, first match wins:

1. The session saved in `~/.linkedin-interaction/chrome-profile` from an earlier run.
2. `LINKEDIN_COOKIES=~/.linkedin-mcp/cookies.json`: the LinkedIn MCP server's signed-in session, imported
   once into the profile above. No password needed. The MCP server keeps working alongside it.
3. `LINKEDIN_USERNAME` / `LINKEDIN_PASSWORD` from the environment, e.g.
   `LINKEDIN_PASSWORD="$(security find-generic-password -s linkedin -w)"` from the macOS Keychain.
   Never write credentials into the repo, `config.toml` or a command you echo.
4. Otherwise the run waits up to 5 minutes for a manual login in the window.

A login page, captcha or `/checkpoint/` verification stops the run with an error. Ask the user to finish
it in the Chrome window, then rerun. Never loop on it.

## Run

`V="venv/bin/linkedin-interaction"`. Each run opens one Chrome window; never start two at once, they share
one browser profile.

| Targets | Command |
| --- | --- |
| Specific people (URLs or usernames) | `$V visit profile https://www.linkedin.com/in/jane-doe/ john-smith` |
| A list from a file or another command | `... \| $V visit profile -` (whitespace-separated URLs or usernames) |
| A people search | `$V visit search --url "<people search URL>" --pages 3 --max 30` |
| Invite candidates the daily round queued | `python3 .scripts/linkedin_outreach.py candidates 30 \| jq -r '.[].username' \| $V visit profile -` |

- People-search URLs for the daily round's audiences follow `.workflow/linkedin-outreach.json`:
  `https://www.linkedin.com/search/results/people/?keywords=<kw>&origin=FACETED_SEARCH&network=["S"]&geoUrn=["103644278"]`
  (URL-encode the brackets and quotes). Don't call `linkedin_outreach.py next-search` for this: it advances
  the daily round's search cursor.
- MCP `search_people` results can also feed `visit profile -` with their usernames.
- Each visit stays 8–20 s and scrolls; `[pacing]` adds 2–6 s between pages.
- `--max N` lowers the per-run cap (`limits.visits`, default 40). Keep a day under about 80 views.
  LinkedIn publishes no view limit, but bursts of views are a common restriction trigger.
- History: `~/.linkedin-interaction/visits.sqlite3` skips anyone viewed in the last 30 days
  (`visits.revisit_days`). `--again` overrides that for `visit profile`; use it only when the user asks.

Output: one line per person (`visited`, `skipped (viewed <date>)` or `unavailable (<reason>)`), then
`Viewed N profile(s); M skipped or unavailable.` Report those counts and any stop reason.

## Rules

- Visits only. This skill never connects, follows, messages or reacts. Invites and warm-up messages go through
  `.workflow/linkedin-daily-outreach.md`.
- Visit only people the user asked for or an audience they named. No bulk scraping and no saving profile data
  beyond the visit history.
- The history and cookie files hold personal data and a live session. Never copy them into the repo or print
  cookie values.
