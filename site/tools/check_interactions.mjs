// Fictional local OIDC users exercise actual native introduction/conversation forms.
// No test endpoint, real Google account, delivery service or production host is used.
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import assert from "node:assert/strict";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const localPython = path.join(repository, ".venv", "Scripts", "python.exe");
const python = process.env.PYTHON || (process.platform === "win32" && existsSync(localPython) ? localPython : "python3");
const outArg = process.argv.indexOf("--out");
const out = path.resolve(outArg > 0 ? process.argv[outArg + 1] : "artifacts/interactions-browser");
mkdirSync(out, { recursive: true });
const checks = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
function check(name, condition) { assert.ok(condition, name); checks.push({ check: name, passed: true }); }
async function navigate(page, control) {
  await Promise.all([
    page.waitForEvent("framenavigated", frame => frame === page.mainFrame()),
    control.click(),
  ]);
  await page.waitForLoadState("domcontentloaded");
}
async function followExactLink(page, link) {
  const target = new URL(await link.getAttribute("href"), page.url()).href;
  await Promise.all([
    page.waitForURL(url => url.href === target, { waitUntil: "domcontentloaded" }),
    link.click(),
  ]);
}

async function fixture(name) {
  const directory = mkdtempSync(path.join(out, `fixture-${name}-`));
  const child = spawn(python, [path.join(repository, "tests_server", "oidc_fixture.py"), "--interactions", "--out", directory], {
    cwd: repository, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", PYTHONUNBUFFERED: "1" }, stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "", spawnError;
  child.stdout.on("data", data => { output += data.toString(); });
  child.stderr.on("data", data => { output += data.toString(); });
  const finished = new Promise(resolve => {
    child.once("error", error => { spawnError = error; resolve(-1); });
    child.once("exit", code => resolve(code));
  });
  async function stop() {
    writeFileSync(path.join(directory, "stop"), "stop\n");
    const code = await Promise.race([finished, delay(10000).then(() => "timeout")]);
    writeFileSync(path.join(directory, "process.log"), output);
    if (code === "timeout") { child.kill(); throw new Error("Interaction fixture did not stop"); }
    assert.equal(code, 0, output || "Interaction fixture failed");
    const evidence = JSON.parse(readFileSync(path.join(directory, "fixture-evidence.json"), "utf8"));
    writeFileSync(path.join(out, `fixture-evidence-${name}.json`), JSON.stringify(evidence, null, 2) + "\n");
    return evidence;
  }
  async function command(stem, value) {
    const id = `${stem}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const pending = path.join(directory, stem + "-command.pending");
    writeFileSync(pending, JSON.stringify({ id, ...value }));
    renameSync(pending, path.join(directory, stem + "-command.json"));
    for (let attempt = 0; attempt < 600; attempt++) {
      const ack = path.join(directory, stem + "-ack.json");
      if (existsSync(ack)) {
        try {
          const result = JSON.parse(readFileSync(ack, "utf8"));
          if (result.id === id) return result;
        } catch (error) {
          // Retry a write in progress or a transient Windows file-sharing lock.
          if (!(error instanceof SyntaxError) && !["EACCES", "EPERM", "EBUSY", "ENOENT"].includes(error.code)) throw error;
        }
      }
      if (spawnError || child.exitCode !== null) throw new Error(output || String(spawnError));
      await delay(100);
    }
    throw new Error("Fictional interaction command timed out");
  }
  for (let attempt = 0; attempt < 120; attempt++) {
    if (existsSync(path.join(directory, "ready.json"))) return {
      ...JSON.parse(readFileSync(path.join(directory, "ready.json"), "utf8")), stop,
      snapshot: () => command("interactions", { action: "snapshot" }),
      quota: conversation_id => command("interactions", { action: "fill_message_quota", conversation_id }),
      advance: seconds => command("clock", { seconds }),
    };
    if (spawnError || child.exitCode !== null) throw new Error(output || String(spawnError));
    await delay(250);
  }
  child.kill(); throw new Error("Interaction fixture startup timed out: " + output);
}

const postings = {
  ama: { text: "I build fictional soil sensors. I can offer field measurements. I am looking for low-power firmware help.", tags: "sensors, firmware", looking_for: "Low-power firmware help", offering: "Fictional field measurements" },
  joon: { text: "I repair fictional workshop tools. I can offer repair notes. I am looking for a documentation partner.", tags: "repair, documentation", looking_for: "A documentation partner", offering: "Fictional repair notes" },
  priya: { text: "I document fictional lab equipment. I can offer drawing reviews. I am looking for tool measurements.", tags: "documentation, tools", looking_for: "Tool measurements", offering: "Fictional drawing reviews" },
};
const note = 'Could we compare one fictional sensor repair? <script>window.introExecuted=true</script>';
const reason = "Your repair notes could explain my sensor measurements.";
const escapedMessage = 'Fictional measurement <script>window.messageExecuted=true</script>\nSee https://fictional.example.invalid/notes';

const browser = await chromium.launch();
try {
  for (const [name, viewport] of [["desktop", { width: 1440, height: 900 }], ["mobile", { width: 390, height: 844 }]]) {
    const local = await fixture(name), base = local.modes.interactions.url;
    const contexts = [], forbidden = [], scriptErrors = [];
    const verify = (label, condition) => check(`${name}: ${label}`, condition);
    async function context(options = {}) {
      const ctx = await browser.newContext({ viewport, ...options }); contexts.push(ctx);
      ctx.on("page", page => page.on("pageerror", error => scriptErrors.push(error.message)));
      await ctx.route("**/*", route => {
        const host = new URL(route.request().url()).hostname;
        if (["localhost", "127.0.0.1", "[::1]"].includes(host)) return route.continue();
        forbidden.push(host); return route.abort();
      });
      return ctx;
    }
    async function login(ctx, person) {
      const page = await ctx.newPage();
      await page.goto(base + "/signin");
      await navigate(page, page.getByRole("link", { name: "Continue with Google", exact: true }));
      await page.waitForURL(local.provider_url + "/authorize?**");
      await page.getByLabel("Fictional person").selectOption(person);
      await navigate(page, page.getByRole("button", { name: "Use fictional account", exact: true }));
      await page.waitForURL(base + "/app");
      verify(`${person} authenticates through actual OIDC exchange`, (await page.locator("body").innerText()).includes(person[0].toUpperCase() + person.slice(1) + " Test"));
      return page;
    }
    async function publish(page, payload) {
      await page.goto(base + "/app/posting");
      await page.getByLabel("Public posting", { exact: true }).fill(payload.text);
      await page.getByLabel("Tags (comma separated, up to five)", { exact: true }).fill(payload.tags);
      await page.getByLabel("Looking for", { exact: true }).fill(payload.looking_for);
      await page.getByLabel("Offering", { exact: true }).fill(payload.offering);
      await navigate(page, page.getByRole("button", { name: "Save draft", exact: true }));
      await navigate(page, page.getByRole("button", { name: "Preview publication", exact: true }));
      await navigate(page, page.getByRole("button", { name: "Publish", exact: true }));
      const feed = await (await page.context().request.get(base + "/board.json")).json();
      const item = feed.items.find(item => item.text === payload.text);
      assert.ok(item, "Approved posting must appear in the public feed");
      return item.slug;
    }
    const form = async (page, action) => page.locator(`form[action='${action}']`).evaluate(node => Object.fromEntries(new FormData(node)));
    const post = (ctx, route, data) => ctx.request.post(base + route, { form: data, headers: { Origin: base }, maxRedirects: 0 });
    async function preview(page, slug, words = note) {
      await page.goto(base + "/app/introductions/new?slug=" + slug);
      await page.getByLabel("Your note", { exact: true }).fill(words);
      await page.getByLabel("Why you would like to meet", { exact: true }).fill(reason);
      await navigate(page, page.getByRole("button", { name: "Preview request", exact: true }));
      return form(page, "/app/introductions");
    }
    async function request(page, slug, words = note) {
      const submitted = await preview(page, slug, words);
      await navigate(page, page.getByRole("button", { name: "Send request", exact: true }));
      const id = await page.locator("[data-introduction-id]").getAttribute("data-introduction-id");
      return { id, submitted };
    }
    async function accept(page, id) {
      await page.goto(base + "/app/introductions/" + id);
      const submitted = await form(page, `/app/introductions/${id}/accept`);
      await navigate(page, page.getByRole("button", { name: "Yes", exact: true }));
      const conversation = await page.locator("[data-conversation-id]").getAttribute("data-conversation-id");
      return { conversation, submitted };
    }
    async function send(page, text) {
      await page.getByLabel("Your message", { exact: true }).fill(text);
      const id = await page.locator("[data-conversation-id]").getAttribute("data-conversation-id");
      const submitted = await form(page, `/app/conversations/${id}/messages`);
      const [response] = await Promise.all([
        page.waitForResponse(response => response.url() === base + `/app/conversations/${id}/messages`),
        navigate(page, page.getByRole("button", { name: "Send message", exact: true })),
      ]);
      return { response, submitted };
    }
    async function fitted(page, label) { verify(`${label} fits the viewport`, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)); }
    async function state(page, id, expected) {
      await page.goto(base + "/app/introductions/" + id);
      verify(`request becomes ${expected}`, await page.locator("[data-introduction-status]").getAttribute("data-introduction-status") === expected);
      verify(`${expected} request offers no acceptance`, await page.getByRole("button", { name: "Yes", exact: true }).count() === 0);
    }
    let succeeded = false;
    try {
      const ama = await context(), joon = await context({ javaScriptEnabled: false }), priya = await context(), anonymous = await context({ javaScriptEnabled: false });
      let a = await login(ama, "ama"), j = await login(joon, "joon"), p = await login(priya, "priya");
      const slugs = { ama: await publish(a, postings.ama), joon: await publish(j, postings.joon), priya: await publish(p, postings.priya) };
      verify("three humans explicitly publish their own words", (await (await anonymous.request.get(base + "/board.json")).json()).total === 3);
      await a.goto(base + "/board/" + slugs.joon);
      await navigate(a, a.getByRole("link", { name: "Ask for an introduction", exact: true }));
      verify("public posting leads to the human request form", new URL(a.url()).pathname === "/app/introductions/new");
      const pendingForm = await preview(a, slugs.joon);
      const card = a.locator("[data-introduction-preview]");
      verify("preview shows the exact note and reason", await card.locator(".interaction-words").innerText() === note && await card.locator(".why").innerText() === reason);
      verify("preview contains both exact approved postings", (await card.locator(".posting-text").allTextContents()).join("|") === postings.ama.text + "|" + postings.joon.text);
      verify("preview escapes supplied HTML", await card.locator("script").count() === 0 && await a.evaluate(() => window.introExecuted !== true));
      verify("preview creates no introduction or conversation", (await local.snapshot()).introductions === 0 && (await local.snapshot()).conversations === 0);
      await fitted(a, "request preview");
      await a.screenshot({ path: path.join(out, `introduction-preview-${name}.png`), fullPage: true });
      await p.goto(base + "/app/introductions/new?slug=" + slugs.ama);
      const thirdCSRF = await p.locator("form input[name='csrf']").first().inputValue();
      verify("another account cannot submit a stolen request preview", (await post(priya, "/app/introductions", { ...pendingForm, csrf: thirdCSRF })).status() === 400);
      await navigate(a, a.getByRole("button", { name: "Send request", exact: true }));
      const firstId = await a.locator("[data-introduction-id]").getAttribute("data-introduction-id");
      verify("sending waits for the recipient", await a.locator("h1").innerText() === "Waiting for the posting’s author to decide.");
      let evidence = await local.snapshot();
      verify("one request is stored without a conversation", evidence.introductions === 1 && evidence.intro_receipts === 1 && evidence.conversations === 0);
      verify("duplicate request confirmation returns the same introduction", (await post(ama, "/app/introductions", pendingForm)).headers().location === "/app/introductions/" + firstId && (await local.snapshot()).introductions === 1);
      verify("nonparticipant cannot read an introduction", (await priya.request.get(base + "/app/introductions/" + firstId)).status() === 404);
      await j.goto(base + "/app");
      verify("recipient dashboard shows a choice before acceptance", await j.getByRole("heading", { name: "Someone would like an introduction.", exact: true }).count() === 1);
      await navigate(j, j.getByRole("link", { name: "Review introduction request", exact: true }));
      verify("dashboard item opens the pending request", new URL(j.url()).pathname === "/app/introductions/" + firstId);
      verify("recipient sees the exact reason and note without JavaScript", await j.locator(".why").innerText() === reason && await j.locator(".interaction-words").innerText() === note);
      const acceptForm = await form(j, `/app/introductions/${firstId}/accept`);
      verify("requester cannot use recipient acceptance authority", (await post(ama, `/app/introductions/${firstId}/accept`, { ...acceptForm, csrf: pendingForm.csrf })).status() === 400);
      await navigate(j, j.getByRole("button", { name: "Yes", exact: true }));
      const firstConversation = await j.locator("[data-conversation-id]").getAttribute("data-conversation-id");
      verify("conversation keeps the agreed reason on its connection line", await j.locator(".why").innerText() === reason);
      evidence = await local.snapshot();
      verify("recipient Yes opens exactly one empty conversation", evidence.conversations === 1 && evidence.open_conversations === 1 && evidence.messages === 0);
      verify("repeating Yes returns the same conversation", (await post(joon, `/app/introductions/${firstId}/accept`, acceptForm)).headers().location === "/app/conversations/" + firstConversation && (await local.snapshot()).conversations === 1);
      await a.goto(base + "/app/conversations/" + firstConversation);
      const firstMessage = await send(a, escapedMessage);
      if (firstMessage.response.status() !== 303) assert.fail(await firstMessage.response.text());
      verify("explicit Send message stores one message", firstMessage.response.status() === 303 && (await local.snapshot()).messages === 1);
      verify("message text is escaped and preserves line breaks", await a.locator(".interaction-message .interaction-words").innerText() === escapedMessage && await a.locator(".interaction-message script").count() === 0 && await a.evaluate(() => window.messageExecuted !== true));
      verify("message URLs remain plain text with no attachment control", await a.locator(".conversation-messages a,input[type='file']").count() === 0);
      verify("retrying the same message does not duplicate it", (await post(ama, `/app/conversations/${firstConversation}/messages`, firstMessage.submitted)).status() === 303 && (await local.snapshot()).messages === 1);
      verify("changed content cannot reuse a sent form", (await post(ama, `/app/conversations/${firstConversation}/messages`, { ...firstMessage.submitted, text: "Different fictional content" })).status() === 409 && (await local.snapshot()).messages === 1);
      verify("attachment form fields are rejected", (await post(ama, `/app/conversations/${firstConversation}/messages`, { ...firstMessage.submitted, attachment: "fictional.txt" })).status() === 400);
      verify("nonparticipant cannot read a conversation", (await priya.request.get(base + "/app/conversations/" + firstConversation)).status() === 404);
      verify("nonparticipant cannot send with another account's form", (await post(priya, `/app/conversations/${firstConversation}/messages`, { ...firstMessage.submitted, csrf: thirdCSRF })).status() === 400);
      await j.reload();
      verify("the other participant can read the sent words", await j.locator(".interaction-message .interaction-words").innerText() === escapedMessage);
      verify("recipient can send without JavaScript", (await send(j, "Joon's fictional reply stays with its author.")).response.status() === 303 && (await local.snapshot()).messages === 2);
      await a.reload(); await fitted(a, "conversation");
      await a.screenshot({ path: path.join(out, `conversation-${name}.png`), fullPage: true });
      evidence = await local.quota(firstConversation);
      verify("quota seed uses 99 requester messages and one recipient reply", evidence.messages === 100 && evidence.message_receipts === 100);
      await a.reload();
      verify("the hundredth requester message is accepted", (await send(a, "Fictional hundredth message.")).response.status() === 303 && (await local.snapshot()).messages === 101);
      const overQuota = await send(a, "This over-limit message must not be stored.");
      evidence = await local.snapshot();
      verify("the next message receives a retryable limit without a write", overQuota.response.status() === 429 && Number(overQuota.response.headers()["retry-after"]) > 0 && evidence.messages === 101 && evidence.message_receipts === 101);
      await j.reload();
      verify("message history is bounded to the newest fifty", await j.locator("[data-message-id]").count() === 50);
      await followExactLink(j, j.getByRole("link", { name: "Older messages", exact: true }));
      verify("native older-message navigation loads the previous fifty", await j.locator("[data-message-id]").count() === 50);
      await followExactLink(j, j.getByRole("link", { name: "Older messages", exact: true }));
      const oldestCount = await j.locator("[data-message-id]").count();
      assert.equal(oldestCount, 1, `${name}: oldest page contains exactly one message`);
      verify("oldest page contains exactly one message without JavaScript", oldestCount === 1);
      // innerText depends on the stylesheet's pre-wrap rule. DOMContentLoaded
      // can precede that stylesheet, briefly collapsing the saved newline.
      await j.waitForLoadState("load");
      const oldestText = await j.locator(".interaction-message .interaction-words").innerText();
      assert.equal(oldestText, escapedMessage, `${name}: oldest message retains the exact fictional text`);
      verify("oldest message retains the exact fictional text without JavaScript", oldestText === escapedMessage);
      await navigate(j, j.getByRole("link", { name: "Latest messages", exact: true }));
      await navigate(j, j.getByRole("button", { name: "Leave conversation", exact: true }));
      await a.goto(base + "/app/conversations/" + firstConversation);
      verify("recipient leaving closes messaging for both people", await a.locator("[data-conversation-status]").getAttribute("data-conversation-status") === "closed" && await a.getByRole("button", { name: "Send message", exact: true }).count() === 0 && (await local.snapshot()).open_conversations === 0);
      verify("a retained message form cannot send after closure", (await post(ama, `/app/conversations/${firstConversation}/messages`, overQuota.submitted)).status() === 409 && (await local.snapshot()).messages === 101);
      await post(joon, `/app/introductions/${firstId}/accept`, acceptForm);
      verify("an old acceptance cannot reopen a closed conversation", (await local.snapshot()).conversations === 1 && (await local.snapshot()).open_conversations === 0);

      const declined = await request(a, slugs.joon, "Fictional decline scenario.");
      await j.goto(base + "/app/introductions/" + declined.id);
      const oldDeclineAccept = await form(j, `/app/introductions/${declined.id}/accept`);
      await navigate(j, j.getByRole("button", { name: "Not now", exact: true }));
      await state(a, declined.id, "declined");
      verify("an old Yes form cannot override a decline", (await post(joon, `/app/introductions/${declined.id}/accept`, oldDeclineAccept)).status() === 410);
      const withdrawn = await request(a, slugs.joon, "Fictional withdrawal scenario.");
      await j.goto(base + "/app/introductions/" + withdrawn.id);
      const oldWithdrawAccept = await form(j, `/app/introductions/${withdrawn.id}/accept`);
      await navigate(a, a.getByRole("button", { name: "Withdraw request", exact: true }));
      await state(j, withdrawn.id, "withdrawn");
      verify("an old Yes form cannot override withdrawal", (await post(joon, `/app/introductions/${withdrawn.id}/accept`, oldWithdrawAccept)).status() === 410);

      const stalePreview = await preview(a, slugs.joon, "Fictional stale preview must not be sent.");
      const revisedJoon = { ...postings.joon, offering: "Updated fictional repair notes" };
      await publish(j, revisedJoon);
      evidence = await local.snapshot();
      verify("republishing invalidates an unsubmitted request preview", (await post(ama, "/app/introductions", stalePreview)).status() === 409 && (await local.snapshot()).introductions === evidence.introductions);
      const stale = await request(a, slugs.joon, "Fictional old context must disappear.");
      await j.goto(base + "/app/introductions/" + stale.id);
      const oldStaleAccept = await form(j, `/app/introductions/${stale.id}/accept`);
      await publish(a, { ...postings.ama, offering: "Updated fictional field measurements" });
      await state(j, stale.id, "withdrawn");
      verify("changed public approval hides pending context", !(await j.locator("body").innerText()).includes("Fictional old context must disappear.") && await j.locator(".posting-text").count() === 0);
      verify("old acceptance cannot approve changed public context", (await post(joon, `/app/introductions/${stale.id}/accept`, oldStaleAccept)).status() === 410 && (await local.snapshot()).conversations === 1);

      const expiring = await request(a, slugs.joon, "Fictional expiring request.");
      await local.advance(7 * 86400); await local.advance(7 * 86400); await local.advance(1);
      a = await login(ama, "ama"); j = await login(joon, "joon"); p = await login(priya, "priya");
      await state(j, expiring.id, "expired");
      verify("expiry creates no conversation", (await local.snapshot()).conversations === 1 && (await local.snapshot()).introduction_states.expired === 1);
      const requesterLeaves = await request(a, slugs.joon, "Fictional requester-close scenario.");
      const second = await accept(j, requesterLeaves.id);
      await a.goto(base + "/app/conversations/" + second.conversation);
      const retainedSecond = await form(j, `/app/conversations/${second.conversation}/messages`);
      await navigate(a, a.getByRole("button", { name: "Leave conversation", exact: true }));
      await j.reload();
      verify("requester leaving also closes the recipient's composer", await j.locator("[data-conversation-status]").getAttribute("data-conversation-status") === "closed" && await j.getByLabel("Your message", { exact: true }).count() === 0);
      verify("recipient cannot send from a form retained before closure", (await post(joon, `/app/conversations/${second.conversation}/messages`, { ...retainedSecond, text: "Must not send" })).status() === 409);

      const toBlock = await request(a, slugs.priya, "Fictional private blocking scenario.");
      await p.goto(base + "/app/introductions/" + toBlock.id);
      const beforeBlock = await (await anonymous.request.get(base + "/board.json")).json();
      const outboxBefore = (await local.snapshot()).email_outbox;
      await navigate(p, p.getByRole("button", { name: "Block", exact: true }));
      await state(a, toBlock.id, "unavailable");
      verify("blocked requester receives a generic outcome", !(await a.locator("[data-introduction-id]").innerText()).toLowerCase().includes("blocked"));
      const aFeed = await (await ama.request.get(base + "/board.json")).json();
      const pFeed = await (await priya.request.get(base + "/board.json")).json();
      const anonymousFeed = await (await anonymous.request.get(base + "/board.json")).json();
      verify("blocking filters both signed-in board views", !aFeed.items.some(item => item.slug === slugs.priya) && !pFeed.items.some(item => item.slug === slugs.ama) && aFeed.total === 2 && pFeed.total === 2);
      verify("anonymous board remains public after a private block", anonymousFeed.total === 3 && anonymousFeed.updated_at === beforeBlock.updated_at);
      verify("blocked direct posting views are unavailable in both directions", (await ama.request.get(base + "/board/" + slugs.priya)).status() === 404 && (await priya.request.get(base + "/board/" + slugs.ama + ".json")).status() === 404);
      verify("future request creation is refused after blocking", (await ama.request.get(base + "/app/introductions/new?slug=" + slugs.priya)).status() === 404);
      evidence = await local.snapshot();
      verify("block is persisted without creating a conversation", evidence.user_blocks === 1 && evidence.conversations === 2);
      verify("blocking produces no notification event", evidence.email_outbox === outboxBefore);

      const deletionIntro = await request(a, slugs.joon, "Fictional deletion context must be scrubbed.");
      const deletionConversation = await accept(j, deletionIntro.id);
      await a.goto(base + "/app/conversations/" + deletionConversation.conversation);
      await send(a, "Ama's deletion-only private message.");
      await j.reload(); await send(j, "Joon's surviving private message.");
      await a.goto(base + "/app/account");
      await a.getByLabel("Type DELETE to confirm", { exact: true }).fill("DELETE");
      await navigate(a, a.getByRole("button", { name: "Delete my account", exact: true }));
      verify("account deletion completes through its explicit confirmation", await a.locator("h1").innerText() === "Your account is deleted.");
      await j.goto(base + "/app/conversations/" + deletionConversation.conversation);
      const survivingWords = await j.locator(".interaction-message .interaction-words").allTextContents();
      verify("deletion removes the deleted author's words but preserves the other person's", survivingWords.includes("[deleted]") && survivingWords.includes("Joon's surviving private message.") && !survivingWords.includes("Ama's deletion-only private message."));
      verify("deletion closes the remaining participant's conversation", await j.locator("[data-conversation-status]").getAttribute("data-conversation-status") === "closed" && await j.getByLabel("Your message", { exact: true }).count() === 0);
      verify("deleted introduction reason is replaced by the generic consent label", await j.locator(".why").innerText() === "You both chose to talk");
      await j.goto(base + "/app/introductions/" + deletionIntro.id);
      verify("deletion removes both shared posting snapshots and introduction words", await j.locator(".posting-text,.interaction-words,.why").count() === 0 && !(await j.locator("body").innerText()).includes("Fictional deletion context must be scrubbed."));
      await j.goto(base + "/app/account");
      const [download] = await Promise.all([j.waitForEvent("download"), j.getByRole("button", { name: "Download my data", exact: true }).click()]);
      const exportPath = path.join(out, `survivor-export-${name}.json`); await download.saveAs(exportPath);
      const exported = JSON.parse(readFileSync(exportPath, "utf8"));
      verify("surviving account export contains only its own sent messages", exported.messages.some(message => message.text === "Joon's surviving private message.") && exported.messages.every(message => !message.text.includes("Ama's") && message.text !== escapedMessage));
      evidence = await local.snapshot();
      verify("database deletion evidence confirms shared context and author scrubbing", evidence.deleted_messages === 101 && !evidence.deleted_author_text_remains && !evidence.deleted_participant_context_remains && evidence.open_conversations === 0);
      verify("deleted owner's public posting disappears", (await anonymous.request.get(base + "/board/" + slugs.ama + ".json")).status() === 404);
      verify("browser requests stayed on loopback", forbidden.length === 0);
      verify("browser execution has no script errors", scriptErrors.length === 0);
      succeeded = true;
    } finally {
      for (const ctx of contexts) await ctx.close();
      const evidence = await local.stop();
      if (succeeded) {
        verify("OIDC uses real signatures, exchanges and PKCE for six logins", evidence.code_exchanges === 6 && evidence.pkce_verified === 6 && evidence.jwks_reads >= 1);
        verify("interaction actions queue no notifications", evidence.modes.interactions.email_outbox === 2 && Object.keys(evidence.modes.interactions.outbox_kinds).join() === "security_first_login");
        verify("fixture explicitly distinguishes local tests from live Google", evidence.fixture_only === true && evidence.real_google_tested === false);
      }
    }
  }
  writeFileSync(path.join(out, "verification.json"), JSON.stringify({ checks, passed: checks.length, fixture_only: true, real_google_tested: false }, null, 2) + "\n");
  console.log(`${checks.length} interaction browser checks passed. Evidence: ${out}`);
} finally { await browser.close(); }
