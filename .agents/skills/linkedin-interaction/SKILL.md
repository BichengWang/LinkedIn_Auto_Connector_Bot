---
name: linkedin-interaction
description: Engage on LinkedIn posts as the signed-in user via this repo's `linkedin-interaction social` CLI. Read a post's comments, reply to commenters (e.g. on your own posts), comment, react, follow. Use for explicit requests about a specific post or person; not for outreach, invites or DMs.
---

# LinkedIn interaction

Engagement on posts: read the thread, draft a reply in the user's voice, get the exact text approved, post it,
confirm it landed. Everything runs in a real Chrome window through the repo's `linkedin_interaction` package
(Selenium). The LinkedIn MCP server can't comment or react, so don't use it for writes here.

## Setup (once per machine)

From the repo root: `uv venv venv && uv pip install --python venv/bin/python -e .`

Sign-in is the same as the `linkedin-profile-visits` skill: the saved session in
`~/.linkedin-interaction/chrome-profile`, else `LINKEDIN_COOKIES=~/.linkedin-mcp/cookies.json`. A login page,
captcha or `/checkpoint/` stops the run: ask the user to finish it in the window, then rerun once.

## Commands

Run `export LINKEDIN_COOKIES=~/.linkedin-mcp/cookies.json V=venv/bin/linkedin-interaction` first. One Chrome
window per run; never start two at once, they share one browser profile. Post URLs work in any form LinkedIn
shares (`/posts/<slug>-share-…`, `/posts/<slug>-activity-…`, `/feed/update/urn:li:activity:…/`).

| Goal | Command | Writes? |
| --- | --- | --- |
| Read a post's comments and replies | `$V social comments <post-url>` | no |
| Reply to a comment | `$V social reply <post-url> <comment-id> - <<'EOF'` … `EOF` | yes |
| Comment on a post | `$V social comment <post-url> - <<'EOF'` … `EOF` | yes |
| React | `$V social react <post-url> --type like\|celebrate\|support\|love\|insightful\|funny` | yes |
| Follow a person or company | `$V social follow <profile-or-company-url>` | yes |

- `social comments` prints each comment as `<id>  <author>  <profile>`, replies indented with `↳`, your own
  marked `(you)`, and top-level comments by others with no reply from you marked `[awaiting your reply]`.
  It opens `/in/me/` first to learn who "you" is, expands every "See previous replies" and loads more comments
  by scrolling (`--limit`, default 200).
- `social reply` takes the comment id from `social comments`, or the commenter's name when they left exactly one
  top-level comment. LinkedIn pre-fills an @mention of the commenter; the reply text follows it. The command
  checks the editor holds exactly mention + text before submitting and waits for the new reply to appear.
  If that same text from you is already in the thread it sends nothing (`Already replied … nothing sent.`), so
  rerunning after an unconfirmed submission is safe. Never retry any other write that may have gone through.
- Pass multi-line text through stdin (`-` plus a quoted heredoc), never via shell-interpolated strings.
- Replies and comments share `limits.comments` (10 per run); reactions `limits.reactions` (30).

## Workflow: reply to comments on your own post

1. `$V social comments <post-url>`. Work only on `[awaiting your reply]` items unless the user names others.
2. Read the post itself (`read <post-url>` returns the public text and comments without a browser) and any
   source it links that the reply depends on: for a post about a repo in `~/my-code/`, check that repo's docs.
3. Draft one reply per comment in the voice below. Show each draft with the commenter's name and their comment,
   and get the user's approval of the exact text (`ask`). The user's explicit wording counts as approval.
4. Post each approved reply with `social reply <post-url> <comment-id> -`. Report the printed result line.
5. Rerun `social comments` and confirm the item lost its `[awaiting your reply]` flag.

### Voice for replies on Kenneth's technical posts

- Answer the question asked, in the first sentence. Then at most two more sentences: one concrete fact from
  the post or its source, optionally one question back that keeps the thread going.
- Same honesty bar as the posts: state what the tool does and does not do; no roadmap, users, adoption or
  benchmarks that a source doesn't show. If the answer is "not built" or "out of scope", say so plainly.
- Warm peer, not marketing: thank them only when the comment adds something, by naming what it adds.
  No hashtags, no links unless asked, no emoji. Don't repeat the @mention LinkedIn already inserted.
- ≤ 600 characters. English unless the commenter wrote in another language.

## Use cases

### 1. Reply to Taylor Kolasinski on "Your LangGraph Approval Is Not Evidence" (2026-10-24)

- Post: https://www.linkedin.com/posts/kennethbcwang_langgraph-mlops-aigovernance-share-7519138064687079425-ieLr/
  (activity 7519138065618120704; part 5 of the ai-decision-system series on the GrowthBook, OPA/Cedar and
  LangGraph bridges).
- Comment `7519143361769799681`, Taylor Kolasinski (Founder, Poisson Labs): an approval covers one sample;
  rerunning the agent on the same inputs can propose something the reviewer never saw. "Is recording enough to
  rerun the proposal step on your list, or out of scope on purpose?"
- Facts for the answer: a decision-control record freezes the recommendation, ticket snapshot, policy and its
  digest, proposer, review and events (`~/my-code/ai-decision-system/docs/DECISION_RECORDS.md`). It records
  what was proposed and approved; it doesn't capture what's needed to rerun the proposer, so a rerun that
  proposes something else is a new decision needing its own review.
- Run: `$V social comments <post>` → draft → user approves → `$V social reply <post> 7519143361769799681 -`.

Add later use cases here in the same shape: post, comment, facts the reply relies on, command.

## Rules

- Act only on posts and people the user named. No bulk liking, commenting or replying across feeds or searches.
- Every comment, reply, reaction and follow is public and goes out under the user's name: confirm the exact
  text and target first, unless the user supplied both verbatim.
- Never message, invite or connect from this skill. Those go through `.workflow/linkedin-daily-outreach.md`,
  and its no-duplicate-conversation rule still applies.
- Don't save comment text or commenter data outside this conversation, except a short use-case entry above
  when the user asks to record one.
