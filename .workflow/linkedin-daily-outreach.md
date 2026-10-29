# LinkedIn outreach round

A round runs every 3 hours. One agent runs the whole round from this workspace. Don't start Orca workers or subagents. Every action shares one LinkedIn account, so parallel sessions would only race each other. Pages are read in my Chrome through the omp browser relay. Invites and messages go through the LinkedIn MCP server; reactions and comments are clicked in that Chrome tab. Only the driver below may make any of these writes.

**Standing authorization** (Kenneth, 2026-10-22; extended 2026-10-28). The round may do the following without per-recipient confirmation:
- send cold invites to the audiences in `.workflow/linkedin-outreach.json`, up to its caps (100 per 3 hours). Frontier-lab people come first;
- send the warm-ups listed below;
- like the recent posts of my frontier-lab connections and new connections, and post a comment I write on them (see Comments).

This satisfies the confirmation rules of the `linkedin-mcp` and `linkedin-interaction` skills, for this workflow only.

`L` = `python3 .scripts/linkedin_outreach.py`. Load the driver in a JS eval cell with `const P = await import(process.cwd() + "/.scripts/linkedin_pages.js")`, and pass it the cell's `browser` and `tool`. Give each driver cell an eval timeout of 900 s.

1. Run `L start`. If it exits 1 (a round is still running or started less than 2.5 hours ago), stop and report its line.
2. **Warm-ups.** Run `const due = await P.collectWarmups(browser)`. Write one text per item (see Voice). Then run `await P.deliver(tool, items)`, with up to 10 items per cell. Each item is a `due` entry plus `text`.
3. **Cold invites.** Repeat `await P.runInvites(browser, tool)` until it returns `done: true`.
4. **Posts.** Run `const posts = await P.collectPosts(browser)`. For each item, read `post.text` and write a comment (see Comments). Then run `await P.engage(browser, items)`, with up to 4 items per cell. Each item is a `posts` entry plus `comment`. Leave `comment` out to only like the post. Leave the item out entirely to skip the post.
5. Run `await P.closeTab(browser)`, then `L finish`. Reply with the counts, failures, `done` reasons and blockers. Run `L finish` even when a step failed or you stop at a blocker, so the next round isn't held back by this one's lock.

## Who

- **Frontier labs**: the companies in `frontier_labs` in the config (OpenAI, Anthropic, Google DeepMind, xAI, Mistral AI, Thinking Machines Lab, Reflection AI, Physical Intelligence, Periodic Labs). The `frontier-labs` search finds their people for invites. My connections who work there are found a few pages per round (`core_pages_per_round`).
- **Post engagement**: frontier-lab connections first, then connections made in the last 14 days. Each person's newest own post from the last 14 days gets one like and one comment. Remaining slots (8 posts per round) are filled from LinkedIn's posts search for the past week's posts by frontier-lab people, at any connection degree. That search also returns people with no tie to a lab, so its authors count only when a frontier-lab people search already found them or their headline names a lab (`frontier_headline_pattern`). Reposts and posts I already liked are skipped. Each person is checked at most once a week.

## Warm-ups

- `message` / `connection`: a new 1st-degree connection, made on or after go-live day minus one. They accepted my invite, or I accepted theirs.
- `message` / `viewer`: a connection who viewed my profile in the last 7 days.
- `invite` / `viewer`: a 2nd- or 3rd-degree viewer. The `text` is the invite note (≤ 200 characters). These invites count toward the invite cap.

Anonymous viewers never appear. Each person gets one warm-up at most.

**Never message anyone I already have a conversation with**: any thread, either direction, any age. `collectWarmups` opens each person's composer and retires anyone with history. The ledger refuses to send a message without a "no conversation" check from the last 2 hours. If a check can't be completed, the person waits for another round.

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

## Comments

A comment is public under my name. Write it as a peer who read the whole post:
- 1–2 sentences, ≤ 300 characters, one paragraph. Engage with one specific point from the post: a tradeoff, a result, a design choice. Add a short thought or one real question.
- Congratulate on launches, papers and new roles, then still say something specific.
- No self-promotion, no pitch, no asking for jobs, no links, no hashtags, no @mentions, no emojis. The ledger refuses `@`, `#` and line breaks.
- Never claim facts the post doesn't state, and never claim I've used their product or read their paper.
- Write in the post's language.
- Skip the comment (like only) when the post gives nothing specific to say, such as a bare link, a job ad or a repost-style share.
- Skip the post entirely when it's about layoffs, deaths, illness, politics or other sensitive news.

Example, post on full-duplex voice models losing the turn boundary: "The turn boundary point is sharp: once there is no end of turn, compaction, reconnect checkpoints and billing all lose their natural trigger. Have you found a better signal than latency for scheduling compaction, like a per-call cache size budget?"

## Rules

- Never call `connect_with_person` or `send_message` yourself, and never like or comment outside `P.engage`. Never retry a write the ledger refused or recorded as `unknown`.
- Respect every stop: a cap was reached, invites are paused, or search pages ran dry. Three failed invites in a row pause invites until tomorrow; that is how LinkedIn's weekly limit shows up. Report stops; never work around them.
- A Chrome sign-in page, captcha or verification step: stop and ask me to finish it.
- `enter_to_send_enabled`: stop messaging and ask me to switch LinkedIn to "Click Send to send".
- The browser relay sometimes drops for a minute ("extension disconnected"). Wait 60 s and rerun the cell once. The ledger keeps reruns safe: written people are refused, and an `unknown` is never retried. If it fails again, go to step 5 and report it.
- The ledger in `~/.linkedin-outreach/` holds personal data. Never copy it into the repo.
