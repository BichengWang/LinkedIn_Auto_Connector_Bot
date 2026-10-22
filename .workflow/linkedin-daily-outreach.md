# LinkedIn daily outreach

One agent runs the whole round from this workspace. Don't start Orca workers or subagents. Every action shares one LinkedIn account, so parallel sessions would only race each other. Pages are read in my Chrome through the omp browser relay. Writes go through the LinkedIn MCP server, and only the driver below may make them.

**Standing authorization** (Kenneth, 2026-10-22). The round may do the following without per-recipient confirmation:
- send cold invites to the audiences in `.workflow/linkedin-outreach.json`, up to its daily caps;
- send the warm-ups listed below.

This satisfies the `linkedin-mcp` skill's confirmation rule, for this workflow only.

`L` = `python3 .scripts/linkedin_outreach.py`. Load the driver in a JS eval cell with `const P = await import(process.cwd() + "/.scripts/linkedin_pages.js")`, and pass it the cell's `browser` and `tool`.

1. Run `L status`. If it says `finished_today`, stop.
2. **Warm-ups.** Run `const due = await P.collectWarmups(browser)`. Write one text per item (see Voice). Then run `await P.deliver(tool, items)`, with up to 10 items per cell. Each item is a `due` entry plus `text`. Give each cell an eval timeout of 900 s.
3. **Cold invites.** Repeat `await P.runInvites(browser, tool)` (timeout 900 s) until it returns `done: true`.
4. Run `await P.closeTab(browser)`, then `L finish`. Reply with the counts, failures, `done` reasons and blockers.

## Warm-ups

- `message` / `connection`: a new 1st-degree connection, made on or after go-live day minus one. They accepted my invite, or I accepted theirs.
- `message` / `viewer`: a connection who viewed my profile in the last 7 days.
- `invite` / `viewer`: a 2nd- or 3rd-degree viewer. The `text` is the invite note (≤ 200 characters). These invites count toward the daily invite cap.

Anonymous viewers never appear. Each person gets one warm-up at most.

**Never message anyone I already have a conversation with**: any thread, either direction, any age. `collectWarmups` opens each person's composer and retires anyone with history. The ledger refuses to send a message without a "no conversation" check from the last 2 hours. If a check can't be completed, the person waits for another day.

## Voice

Me: Kenneth, Palo Alto. I work on AI at Salesforce, previously xAI and Uber, and I studied at Stanford. My profile says "All in AI. Grab a coffee and chat about your idea!"

Write like a warm peer, not a salesperson:
- 2–3 short sentences, ≤ 300 characters. Use their first name only.
- Open with one specific, genuine hook from their headline: their team, product or research area. No generic praise.
- Introduce myself in one line. Close with one easy question or an open offer of coffee.
- No pitch, no asking for jobs or referrals, no links, no emojis.
- For viewers, a light "thanks for stopping by my profile". Never say when they looked.
- Never invent shared history or facts that aren't in their headline. Vary the wording. The ledger refuses a text it has already sent.
- If the headline gives you nothing to work with, keep it short and friendly.

Examples:
- Connection, "Research Scientist, Google DeepMind": "Hi Ruibo, great to connect! I'm Kenneth, working on AI at Salesforce (ex-xAI, Uber). Curious what you're digging into at DeepMind these days. Always up for swapping notes over coffee."
- 1st-degree viewer, "Assistant Professor at CSULB": "Hi Jingyi, thanks for stopping by my profile! I'm Kenneth, working on AI at Salesforce (ex-xAI, Uber). What's your research focused on lately? Would love to hear."
- Viewer invite note, "Senior Technical Recruiter": "Hi Yiming, thanks for visiting my profile! I'm Kenneth, working on AI at Salesforce (ex-xAI, Uber). Would love to connect."

## Rules

- Never call `connect_with_person` or `send_message` yourself. Never retry a write the ledger refused or recorded as `unknown`.
- Respect every stop: a daily cap was reached, invites are paused, or search pages ran dry. Three failed invites in a row pause invites until tomorrow; that is how LinkedIn's weekly limit shows up. Report stops; never work around them.
- A Chrome sign-in page, captcha or verification step: stop and ask me to finish it.
- `enter_to_send_enabled`: stop messaging and ask me to switch LinkedIn to "Click Send to send".
- The ledger in `~/.linkedin-outreach/` holds personal data. Never copy it into the repo.
