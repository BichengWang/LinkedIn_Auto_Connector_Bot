# LinkedIn monthly catch-ups

One agent sends every catch-up on [Catch up](https://www.linkedin.com/mynetwork/catch-up/all/): birthdays, job changes, work anniversaries and education. Don't start Orca workers or subagents; every action shares one LinkedIn account. Pages are read in my Chrome through the omp browser relay. Writes go through the LinkedIn MCP server, and only the driver may make them.

**Standing authorization** (Kenneth, 2026-10-24). The round may send each person on the Catch up tabs LinkedIn's own preset text for their card ("Wishing you a very happy birthday!", "Congrats on starting your new role at …!") without per-recipient confirmation. This satisfies the `linkedin-mcp` skill's confirmation rule, for this workflow only.

Catch-ups go to existing connections, so they are exempt from the warm-up rule against messaging anyone I already have a conversation with. The ledger still allows one catch-up per person and occasion: one birthday or work anniversary a year, one per new role or school.

Load the driver in a JS eval cell with `const P = await import(process.cwd() + "/.scripts/linkedin_pages.js")`, and pass it the cell's `browser` and `tool`.

1. Run `const items = await P.collectCatchups(browser)` (timeout 600 s). It reads every Catch up tab and returns the catch-ups not yet sent.
2. Send all of them: `await P.deliver(tool, items.slice(i, i + 10))`, 10 items per cell, timeout 900 s, until every item is settled. Send each item exactly as returned.
3. Run `await P.closeTab(browser)`. Reply with the counts by reason and outcome, failures and blockers.

## Rules

- Never change the text, and never call `send_message` yourself. Never retry a write the ledger refused or recorded as `unknown`.
- A Chrome sign-in page, captcha or verification step: stop and ask me to finish it.
- `enter_to_send_enabled`: stop and ask me to switch LinkedIn to "Click Send to send".
- The ledger in `~/.linkedin-outreach/` holds personal data. Never copy it into the repo.
