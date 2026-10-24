# Project rules

## LinkedIn outreach

- Never message anyone I already have a conversation with: any thread, either direction, any age. Before a warm-up, open that person's composer in Chrome (`/messaging/compose/?recipient=<URN from their profile's Message link>`). Send only when it shows no prior messages. If the check can't be completed, skip them.
- One warm-up per person, ever. The ledger in `~/.linkedin-outreach/` is the record: never message someone it refuses or marks `unknown`.
- Daily runs follow `.workflow/linkedin-daily-outreach.md`, and only `.scripts/linkedin_pages.js` sends. Never call `send_message` or `connect_with_person` directly for outreach.
- Catch-ups (birthdays, job changes, work anniversaries, education) are exempt from the prior-conversation rule: they go to existing connections, with LinkedIn's preset text only, once per person and occasion. Monthly runs follow `.workflow/linkedin-catchup.md`.
