// Driver for the LinkedIn outreach and catch-up rounds; `.workflow/linkedin-daily-outreach.md` and
// `.workflow/linkedin-catchup.md` are the runbooks.
// Import it in an omp JavaScript eval cell and pass that cell's `browser` and `tool` globals.
// Reads go through the user's Chrome (omp browser relay). Writes go through the LinkedIn MCP server,
// and each write is claimed in the ledger (linkedin_outreach.py) first, so caps and dedupe hold.

const LEDGER = `${import.meta.dir}/linkedin_outreach.py`;
const TAB = "linkedin-outreach";
const CONNECTIONS_URL = "https://www.linkedin.com/mynetwork/invite-connect/connections/";
const VIEWERS_URL = "https://www.linkedin.com/analytics/profile-views/";
// Catch up tabs by ledger reason; "All" shows only a sample of them.
const CATCHUP_PAGES = {
  birthday: "birthday",
  job_change: "job_changes",
  work_anniversary: "work_anniversaries",
  education: "education",
};
// LinkedIn's weekly invitation limit shows up as invites that stop going through.
const FAILURES_BEFORE_PAUSE = 3;
const BARREN_PAGES_BEFORE_STOP = 8;
// connect_with_person statuses that prove nothing was sent; anything unlisted may have landed.
const INVITE_OUTCOME = {
  connected: "sent",
  accepted: "skipped",
  pending: "skipped",
  already_connected: "skipped",
  connect_unavailable: "failed",
  unavailable: "failed",
};

let inviteFailures = 0;
let barrenPages = 0;

async function ledger(args, input) {
  const proc = Bun.spawn(["python3", LEDGER, ...args], {
    stdin: input === undefined ? "ignore" : new Response(JSON.stringify(input)),
    stdout: "pipe",
    stderr: "pipe",
  });
  const [out, err, code] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
    proc.exited,
  ]);
  if (![0, 2, 3].includes(code)) throw new Error(`linkedin_outreach.py ${args[0]} exited ${code}: ${err || out}`);
  return { code, text: out.trim(), json: () => JSON.parse(out) };
}

async function mcp(tool, name, args) {
  const response = await tool.write({
    path: `xd://mcp__linkedin_${name}`,
    content: JSON.stringify(args),
    i: `Running LinkedIn ${name}`,
  });
  const text = typeof response === "string" ? response : response.text;
  try {
    return JSON.parse(text);
  } catch {
    return { status: "unparsed", message: String(text).slice(0, 500) };
  }
}

// Runs inside the LinkedIn page. Cards are found by the text every row carries.
const EXTRACT = String.raw`(kind) => {
  const marker = { connections: /Connected on /, viewers: /Viewed /, search: /•\s*(1st|2nd|3rd)/ }[kind];
  const cards = new Set();
  for (const link of document.querySelectorAll('main a[href*="/in/"]')) {
    let card = kind === "search" ? link.closest('[role="listitem"]') : link;
    while (kind !== "search" && card && card !== document.body && !marker.test(card.innerText || "")) card = card.parentElement;
    if (card && card !== document.body && marker.test(card.innerText || "")) cards.add(card);
  }
  return [...cards].flatMap((card) => {
    const link = card.matches('a[href*="/in/"]') ? card : card.querySelector('a[href*="/in/"]');
    const match = link?.href.match(/linkedin\.com\/in\/([^/?#]+)/);
    if (!match) return [];
    const lines = card.innerText.split("\n").map((line) => line.trim()).filter(Boolean);
    const labels = [...card.querySelectorAll("button, a")].map((el) => el.getAttribute("aria-label") || el.innerText || "");
    const action = labels.some((l) => /^Invite .+ to connect$/.test(l)) ? "connect"
      : labels.some((l) => /Pending/.test(l)) ? "pending"
      : labels.some((l) => /^Follow /.test(l)) ? "follow"
      : "other";
    return [{
      username: decodeURIComponent(match[1]),
      name: lines[0].split("•")[0].trim(),
      headline: lines.slice(1).find((l) => !/^•|^Connected on |^Viewed /.test(l)) || null,
      degree: (card.innerText.match(/•\s*(1st|2nd|3rd)/) || [])[1] || null,
      action,
      connected_on: (lines.find((l) => l.startsWith("Connected on ")) || "").slice(13) || null,
      viewed: lines.find((l) => l.startsWith("Viewed ")) || null,
    }];
  });
}`;

const LOAD_MORE = `(() => {
  const more = [...document.querySelectorAll("main button")].find((b) => /show more|load more/i.test(b.innerText));
  if (more) more.click();
  window.scrollTo(0, document.body.scrollHeight);
})()`;

// Catch up cards carry LinkedIn's preset text in full only in the button label,
// "Message <name>: <text>"; the visible copy is cut short.
const CATCHUP_EXTRACT = String.raw`(reason) => [...document.querySelectorAll('main [role="listitem"]')].flatMap((card) => {
  const button = card.querySelector('button[aria-label^="Message "]');
  const match = card.querySelector('a[href*="/in/"]')?.href.match(/linkedin\.com\/in\/([^/?#]+)/);
  if (!button || !match) return [];
  const lines = card.innerText.split("\n").map((line) => line.trim()).filter(Boolean);
  const prefix = "Message " + lines[0] + ": ";
  const label = button.getAttribute("aria-label");
  const text = label.startsWith(prefix) ? label.slice(prefix.length).trim() : "";
  return [{
    username: decodeURIComponent(match[1]),
    name: lines[0],
    detail: lines[1] || null,
    reason,
    text: text && !text.endsWith("…") ? text : null,
  }];
})`;

const CATCHUP_MORE = `(() => {
  const cards = document.querySelectorAll('main [role="listitem"]');
  cards[cards.length - 1]?.scrollIntoView();
  window.scrollBy(0, 3000);
  [...document.querySelectorAll("main button")].find((b) => /show more|load more|see more/i.test(b.innerText))?.click();
})()`;

async function openPage(browser, url) {
  // A fresh tab per page: a reused relay tab can keep evaluating in the previous document's frame.
  await browser.close({ name: TAB });
  const tab = await browser.open({ name: TAB, url, app: { relay: true }, timeout: 60 });
  const landed = await tab.url();
  if (/linkedin\.com\/(login|uas|checkpoint|authwall)/.test(landed)) {
    throw new Error(`Chrome is not signed in to LinkedIn (landed on ${landed}). Sign in there, then rerun.`);
  }
  await tab.waitForSelector("main", { timeout: 30000 });
  return tab;
}

async function scan(browser, url, kind, { rounds = 1, since = null } = {}) {
  const tab = await openPage(browser, url);
  await Bun.sleep(3000);
  const oldest = since && new Date(`${since}T00:00:00`);
  let rows = [];
  for (let round = 0; round < rounds; round++) {
    rows = await tab.evaluate(`(${EXTRACT})(${JSON.stringify(kind)})`);
    if (round === rounds - 1 || (oldest && rows.some((r) => r.connected_on && new Date(r.connected_on) < oldest))) break;
    await tab.evaluate(LOAD_MORE);
    await Bun.sleep(2500);
  }
  return rows;
}

async function scanCatchups(browser, reason) {
  const tab = await openPage(browser, `https://www.linkedin.com/mynetwork/catch-up/${CATCHUP_PAGES[reason]}/`);
  await Bun.sleep(3000);
  let rows = [];
  for (let round = 0; round < 40; round++) {
    const loaded = rows.length;
    rows = await tab.evaluate(`(${CATCHUP_EXTRACT})(${JSON.stringify(reason)})`);
    if (round && rows.length === loaded) break;
    await tab.evaluate(CATCHUP_MORE);
    await Bun.sleep(2500);
  }
  for (const row of rows) if (!row.text) console.log(`skip ${row.username}: could not read the full ${reason} text`);
  return rows;
}

/**
 * "exists" | "none" | "unknown": whether I already have a conversation with this person.
 * Their composer (`/messaging/compose/?recipient=<URN>`) shows our whole thread history, so this
 * does not depend on display names or message search. The profile also carries Message links for
 * sidebar people; the owner's URN is the one the sticky header (outside main) and the top card
 * (first link in main) agree on.
 */
async function conversationWith(browser, username) {
  let tab = await openPage(browser, `https://www.linkedin.com/in/${encodeURIComponent(username)}/`);
  await Bun.sleep(2500);
  const recipient = await tab.evaluate(`(() => {
    const links = [...document.querySelectorAll('a[href*="/messaging/compose/"]')];
    const of = (a) => a && new URL(a.href).searchParams.get("recipient");
    const header = of(links.find((a) => !a.closest("main")));
    return header && header === of(links.find((a) => a.closest("main"))) ? header : null;
  })()`);
  if (!recipient) return "unknown";
  tab = await openPage(browser, `https://www.linkedin.com/messaging/compose/?recipient=${encodeURIComponent(recipient)}`);
  for (let waited = 1000; waited <= 15000; waited += 1000) {
    await Bun.sleep(1000);
    const { events, composer } = await tab.evaluate(`({
      events: document.querySelectorAll(".msg-s-message-list__event").length,
      composer: !!document.querySelector(".msg-form__contenteditable"),
    })`);
    if (events) return "exists";
    if (composer && waited >= 5000) return "none";
  }
  return "unknown";
}

async function invite(tool, { username, reason, text }) {
  const note = text?.trim() || null;
  const claim = await ledger(["claim", "invite", username, "--reason", reason, ...(note ? ["--text", note] : [])]);
  if (claim.code) return { username, kind: "invite", refused: claim.text, stop: claim.code === 3 };
  let reply;
  try {
    reply = await mcp(tool, "connect_with_person", { linkedin_username: username, note });
    // LinkedIn refused only the note (monthly note quota); nothing was sent, so invite without it.
    if (reply.status === "custom_note_limit_reached") {
      reply = await mcp(tool, "connect_with_person", { linkedin_username: username });
    }
  } catch (error) {
    reply = { status: "tool_error", message: String(error?.message ?? error) };
  }
  const outcome = INVITE_OUTCOME[reply.status] ?? "unknown";
  await ledger(["result", username, "invite", outcome, "--status", String(reply.status)]);
  const settled = { username, kind: "invite", outcome, status: reply.status, message: reply.message };
  inviteFailures = outcome === "failed" || outcome === "unknown" ? inviteFailures + 1 : 0;
  if (inviteFailures >= FAILURES_BEFORE_PAUSE) {
    inviteFailures = 0;
    const why = `${FAILURES_BEFORE_PAUSE} invites in a row did not go through; last: ${reply.status} ${reply.message ?? ""}`;
    await ledger(["pause-invites", "--days", "1", "--reason", why]);
    return { ...settled, stop: true, refused: `invites paused until tomorrow: ${why}` };
  }
  return settled;
}

/** Warm-up or catch-up message; a catch-up carries its `occasion` from the ledger. */
async function message(tool, { username, kind = "message", reason, occasion, text }) {
  const claim = await ledger([
    "claim", kind, username, "--reason", reason, ...(occasion ? ["--occasion", occasion] : []), "--text", text ?? "",
  ]);
  if (claim.code) return { username, kind, refused: claim.text, stop: claim.code === 3 };
  let reply;
  try {
    reply = await mcp(tool, "send_message", { linkedin_username: username, message: text, confirm_send: true });
  } catch (error) {
    reply = { status: "tool_error", message: String(error?.message ?? error), retry_safe: false };
  }
  const outcome = reply.sent === true ? "sent" : reply.retry_safe === true ? "failed" : "unknown";
  await ledger(["result", username, kind, outcome, "--status", String(reply.status)]);
  // enter_to_send_enabled: LinkedIn's "Press Enter to Send" hides the Send button for every recipient.
  return { username, kind, outcome, status: reply.status, message: reply.message, stop: reply.status === "enter_to_send_enabled" };
}

/**
 * Scan new connections and profile viewers into the ledger; return the warm-ups owed today.
 * Anyone I already have a conversation with is retired from warm-ups; a message also needs a
 * verified "no conversation" (the ledger refuses to claim one otherwise).
 */
export async function collectWarmups(browser) {
  const status = (await ledger(["status"])).json();
  const connections = await scan(browser, CONNECTIONS_URL, "connections", { rounds: 20, since: status.connections_since });
  await ledger(["ingest", "connections"], connections);
  const viewers = await scan(browser, VIEWERS_URL, "viewers", { rounds: 3 });
  await ledger(["ingest", "viewers"], viewers);
  const owed = [];
  for (const item of (await ledger(["due"])).json()) {
    const state = await conversationWith(browser, item.username);
    if (state !== "unknown") await ledger(["thread", item.username, state, "--reason", item.reason]);
    if (state === "exists") console.log(`skip ${item.username}: we already have a conversation`);
    else if (state === "none" || item.kind === "invite") owed.push(item);
    else console.log(`skip ${item.username} today: could not verify that we have no conversation`);
  }
  return owed;
}

/**
 * Read every Catch up tab; return the catch-ups not yet sent, each with LinkedIn's preset text.
 * Catch-ups go to existing connections, so they skip the no-conversation check warm-ups need.
 */
export async function collectCatchups(browser) {
  const rows = [];
  for (const reason of Object.keys(CATCHUP_PAGES)) rows.push(...await scanCatchups(browser, reason));
  return (await ledger(["catchups-due"], rows)).json();
}

/** Send drafted warm-ups or catch-ups: items from collectWarmups (plus `text`) or collectCatchups. Stops a kind at its cap. */
export async function deliver(tool, items) {
  const results = [];
  const stopped = new Set();
  for (const item of items) {
    if (stopped.has(item.kind)) {
      results.push({ username: item.username, kind: item.kind, outcome: "not_attempted" });
      continue;
    }
    const settled = item.kind === "invite" ? await invite(tool, item) : await message(tool, item);
    if (settled.stop) stopped.add(item.kind);
    results.push(settled);
  }
  return results;
}

/** Send up to `batch` cold invites from the configured searches. Call again until `done`. */
export async function runInvites(browser, tool, { batch = 10 } = {}) {
  const status = (await ledger(["status"])).json();
  if (status.invites.paused_until) return { done: true, reason: `invites paused until ${status.invites.paused_until}`, results: [] };
  if (status.invites.used >= status.invites.cap) return { done: true, reason: "daily invite cap reached", results: [] };
  const results = [];
  while (results.length < batch) {
    const queue = (await ledger(["candidates", String(batch - results.length)])).json();
    if (!queue.length) {
      if (barrenPages >= BARREN_PAGES_BEFORE_STOP) {
        barrenPages = 0;
        return { done: true, reason: `${BARREN_PAGES_BEFORE_STOP} search pages in a row had nobody to invite`, results };
      }
      const next = (await ledger(["next-search"])).json();
      const rows = await scan(browser, next.url, "search");
      const { queued } = (await ledger(["ingest", "search", "--search", next.search], rows)).json();
      barrenPages = queued ? 0 : barrenPages + 1;
      continue;
    }
    for (const person of queue) {
      const settled = await invite(tool, { username: person.username, reason: "search" });
      results.push(settled);
      if (settled.stop) return { done: true, reason: settled.refused, results };
    }
  }
  return { done: false, results };
}

/** Close the Chrome tab this round opened. */
export async function closeTab(browser) {
  await browser.close({ name: TAB });
}
