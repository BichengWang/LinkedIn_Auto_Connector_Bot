# LinkedIn Auto Connector (agent-driven)

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

An AI agent grows your LinkedIn network on a daily schedule. It warms up new connections and profile viewers with a short, personal self-intro, and it sends targeted connection invites. There are no hand-written browser bots. The agent reads LinkedIn in your own Chrome, writes through the [LinkedIn MCP server](https://github.com/stickerdaniel/linkedin-mcp-server), and follows a runbook that a small ledger keeps honest.

> ⚠️ LinkedIn's User Agreement prohibits automated access, and LinkedIn enforces an unpublished weekly invitation limit, typically around 100 per week. Going over it usually blocks invitations for a week. Use at your own risk.

## How it works

An [Orca](https://github.com/stablyai/orca) automation starts one agent round every day at 09:00 America/Los_Angeles:

1. **Warm-ups.** The agent reads your Connections page (Recently added) and Who's Viewed Your Profile.
   - **New connections** and **1st-degree viewers from the last 7 days** each get one short, personalized self-intro.
   - **2nd- and 3rd-degree viewers** get an invite with a note instead.
   - **Anyone you already have a conversation with is skipped.** Before writing, the round opens that person's composer and retires anyone with existing messages.
2. **Cold invites.** The agent pages through the people searches in `.workflow/linkedin-outreach.json` and sends invites, up to the daily cap.

The agent drafts each message from the person's headline, in the voice set in the runbook. Everything else is deterministic. The ledger refuses any write that would break a cap, repeat a person or a text, or skip the conversation check. Three failed invites in a row pause invites until the next day, which is how LinkedIn's weekly limit shows up.

| Path | Role |
| --- | --- |
| `.workflow/linkedin-daily-outreach.md` | Runbook the agent follows: steps, standing authorization, voice, rules |
| `.workflow/linkedin-outreach.json` | Caps, pacing, message lengths, search audiences |
| `.scripts/linkedin_outreach.py` | Ledger CLI (stdlib only, data in `~/.linkedin-outreach/`). Handles caps, dedupe, conversation-check gate, search cursor and the Orca `precheck` |
| `.scripts/linkedin_pages.js` | Driver loaded in an omp eval cell. Reads pages through the Chrome relay and writes through the LinkedIn MCP tools |
| `AGENTS.md` / `CLAUDE.md` | Project rules every agent in this repo follows |
| `.mcp.json`, `.agents/skills/`, `.claude/skills/`, `skills-lock.json` | Project-scoped LinkedIn MCP server and its `linkedin-mcp` skill |

## Setup

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then sign in to the MCP server once with `uvx mcp-server-linkedin@latest --login`. The session lives in `~/.linkedin-mcp/`.
2. Install omp and turn on its Chrome browser relay. Keep Chrome running and signed in to LinkedIn.
3. Install [Orca](https://github.com/stablyai/orca), add this repo, and create the automation:

   ```bash
   orca automations create --name "LinkedIn daily outreach" --trigger daily --time 09:00 \
     --timezone America/Los_Angeles --provider omp --workspace "path:$PWD" --workspace-mode existing \
     --missed-run-grace-minutes 240 \
     --precheck "python3 $PWD/.scripts/linkedin_outreach.py precheck" \
     --prompt "Run today's LinkedIn outreach round. Read and follow .workflow/linkedin-daily-outreach.md in this workspace exactly; it carries my standing authorization for the invites and warm-up messages it describes. Work alone: no Orca workers or subagents. Finish once python3 .scripts/linkedin_outreach.py finish has run, or stop at a precise blocker."
   ```

4. Edit the voice section of the runbook, plus the audiences and caps in the config, to fit you.

Run a round on demand with `orca automations run <id>`. Inspect the ledger with `python3 .scripts/linkedin_outreach.py status`.

## Ad-hoc agent use

Open the repo in any MCP-capable agent (omp, Claude Code, Codex, Cursor) and approve the project `linkedin` MCP server. Then ask something like "find Anthropic's leadership and send them connection requests with a short note". Outside the daily round, the `linkedin-mcp` skill makes the agent confirm the exact recipients and text before any write.

## License

MIT. See [LICENSE](LICENSE). Forked from [OfficialCodeVoyage/LinkedIn_Auto_Connector_Bot](https://github.com/OfficialCodeVoyage/LinkedIn_Auto_Connector_Bot) by Pavlo Bondarenko.
