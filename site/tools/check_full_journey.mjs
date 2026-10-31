// One continuous fictional two-person journey across every hosted feature.
// The local provider uses real RSA/PKCE exchanges; this is not live Google/SMTP.
// Secrets, cookies, downloaded prompts and arbitrary failure pages stay in memory.
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import assert from "node:assert/strict";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const localPython = path.join(repository, ".venv", "Scripts", "python.exe");
const python = process.env.PYTHON || (process.platform === "win32" && existsSync(localPython) ? localPython : "python3");
const outArg = process.argv.indexOf("--out");
const out = path.resolve(outArg > 0 ? process.argv[outArg + 1] : "artifacts/full-journey-browser");
mkdirSync(out, { recursive: true });
const checks = [], stages = [];
const kinds = ["security_first_login", "introduction_request", "introduction_accepted", "new_message", "checkin_missed", "weekly_summary"];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
function check(name, condition) { assert.ok(condition, name); checks.push({ check: name, passed: true }); }
async function navigate(page, control) {
  await Promise.all([page.waitForEvent("framenavigated", frame => frame === page.mainFrame()), control.click()]);
  await page.waitForLoadState("domcontentloaded");
}
async function downloadedText(download) {
  let text = "";
  for await (const chunk of await download.createReadStream()) text += chunk.toString("utf8");
  return text;
}

async function fixture(name) {
  const directory = mkdtempSync(path.join(out, `fixture-${name}-`));
  const child = spawn(python, [path.join(repository, "tests_server", "oidc_fixture.py"), "--notifications", "--out", directory], {
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
    if (code === "timeout") { child.kill(); throw new Error("Full-journey fixture did not stop"); }
    writeFileSync(path.join(directory, "process.log"), output);
    assert.equal(code, 0, output || "Full-journey fixture failed");
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
          if (result.id === id) { unlinkSync(ack); return result; }
        } catch (error) {
          if (!(error instanceof SyntaxError) && !["EACCES", "EPERM", "EBUSY", "ENOENT"].includes(error.code)) throw error;
        }
      }
      if (spawnError || child.exitCode !== null) throw new Error(output || String(spawnError));
      await delay(100);
    }
    throw new Error("Fictional full-journey command timed out");
  }
  for (let attempt = 0; attempt < 120; attempt++) {
    if (existsSync(path.join(directory, "ready.json"))) return {
      ...JSON.parse(readFileSync(path.join(directory, "ready.json"), "utf8")), stop,
      snapshot: () => command("notifications", { action: "snapshot" }),
      tick: () => command("notifications", { action: "tick" }),
      advance: seconds => command("clock", { seconds }),
    };
    if (spawnError || child.exitCode !== null) throw new Error(output || String(spawnError));
    await delay(250);
  }
  child.kill(); throw new Error("Full-journey fixture startup timed out");
}

const postings = {
  ama: { text: "I build fictional soil sensors. I can offer field measurements. I am looking for low-power firmware help.", tags: "sensors, firmware", looking_for: "Low-power firmware help", offering: "Fictional field measurements" },
  joon: { text: "I repair fictional workshop tools. I can offer low-power firmware notes. I am looking for field measurements.", tags: "firmware, repair", looking_for: "Fictional field measurements", offering: "Low-power firmware notes" },
};
const note = "A fictional private note about one small sensor trial.";
const reason = "Fictional repair notes could explain sensor measurements.";
const messages = {
  ama: 'Ama fictional private message: compare one <script>window.journeyExecuted=true</script> power trace.',
  joon: "Joon fictional private message: I can explain the power trace.",
  later: "A fictional private message with the second sensor measurement.",
  batch: "Another fictional private message in the same hourly batch.",
};
const mailCount = (snapshot, person, kind) => snapshot.users[person].mail.filter(item => item.kind === kind && item.state === "outbox").length;
const eventCount = (snapshot, person, kind) => snapshot.users[person].events.filter(item => item.kind === kind && item.cancelled_at === null).length;
const browser = await chromium.launch();
try {
  for (const [name, viewport] of [["desktop", { width: 1440, height: 900 }], ["mobile", { width: 390, height: 844 }]]) {
    const local = await fixture(name), base = local.modes.notifications.url;
    const contexts = [], forbidden = [], scriptErrors = [];
    const verify = (label, condition) => check(`${name}: ${label}`, condition);
    function stage(label, evidence) {
      stages.push({ viewport: name, stage: label, passed: true, ...evidence });
      writeFileSync(path.join(out, "stages.json"), JSON.stringify(stages, null, 2) + "\n");
    }
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
    async function capture(page, label) {
      verify(`${label} fits the viewport`, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.screenshot({ path: path.join(out, `${label}-${name}.png`), fullPage: true });
    }
    async function login(ctx, person) {
      const page = await ctx.newPage(); await page.goto(base + "/signin");
      await navigate(page, page.getByRole("link", { name: "Continue with Google", exact: true }));
      await page.waitForURL(local.provider_url + "/authorize?**");
      await page.getByLabel("Fictional person").selectOption(person);
      await navigate(page, page.getByRole("button", { name: "Use fictional account", exact: true }));
      await page.waitForURL(base + "/app");
      verify(`${person} arrives at their dashboard through the OIDC callback`, await page.locator("h1").innerText() === "Your dashboard."
        && (await page.locator("body").innerText()).includes(person === "ama" ? "Ama Test" : "Joon Test"));
      return page;
    }
    async function createKey(page) {
      await page.goto(base + "/app/keys/new");
      await page.getByLabel("Key name", { exact: true }).fill("Fictional journey assistant");
      await navigate(page, page.getByRole("button", { name: "Create key and prompt", exact: true }));
      const key = await page.locator("#created-report-key").innerText();
      const prompt = await page.getByLabel("Your personalised prompt", { exact: true }).inputValue();
      verify("one-time key and prompt bind delivery to this account and origin", /^ctb_[A-Z2-7]{52}$/.test(key)
        && prompt.includes(base + "/api/v1/reports") && prompt.includes("Authorization: Bearer " + key)
        && prompt.includes("Idempotency-Key: UNIQUE-REPORT-ID"));
      const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Download private prompt", exact: true }).click()]);
      verify("downloaded private prompt preserves the complete handoff", (await downloadedText(download)).replace(/\r\n/g, "\n") === prompt.replace(/\r\n/g, "\n"));
      await page.goto(base + "/app/keys");
      verify("key list retains only display metadata after the one-time handoff", !(await page.content()).includes(key)
        && await page.locator("#created-report-key,#personalised-prompt").count() === 0 && (await page.locator("body").innerText()).includes(key.slice(-4)));
      return key;
    }
    async function ingest(ctx, key, payload, id) {
      return ctx.request.post(base + "/api/v1/reports", { headers: {
        Authorization: "Bearer " + key, "Idempotency-Key": id, "Content-Type": "application/json",
      }, data: JSON.stringify(payload) });
    }
    async function publish(page, person) {
      const payload = postings[person];
      await page.goto(base + "/app/posting");
      await page.getByLabel("Public posting", { exact: true }).fill(payload.text);
      await page.getByLabel("Tags (comma separated, up to five)", { exact: true }).fill(payload.tags);
      await page.getByLabel("Looking for", { exact: true }).fill(payload.looking_for);
      await page.getByLabel("Offering", { exact: true }).fill(payload.offering);
      await navigate(page, page.getByRole("button", { name: "Save draft", exact: true }));
      await navigate(page, page.getByRole("button", { name: "Preview publication", exact: true }));
      verify(`${person}'s preview shows exact words before public approval`, (await page.locator("body").innerText()).includes(payload.text)
        && !(await (await page.context().request.get(base + "/board.json")).json()).items.some(item => item.text === payload.text));
      await navigate(page, page.getByRole("button", { name: "Publish", exact: true }));
      const item = (await (await page.context().request.get(base + "/board.json")).json()).items.find(item => item.text === payload.text);
      verify(`${person}'s explicitly approved posting is public with an opaque slug`, !!item && typeof item.slug === "string");
      return item.slug;
    }
    async function send(page, conversation, text) {
      await page.goto(base + "/app/conversations/" + conversation);
      await page.getByLabel("Your message", { exact: true }).fill(text);
      await navigate(page, page.getByRole("button", { name: "Send message", exact: true }));
    }
    async function exportData(page, label) {
      await page.goto(base + "/app/account");
      const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Download my data", exact: true }).click()]);
      const raw = await downloadedText(download), data = JSON.parse(raw);
      verify(`${label} export excludes raw keys, cookie and unsubscribe capabilities`, !/ctb_[A-Z2-7]{52}/.test(raw)
        && !raw.includes("/email/unsubscribe/") && !raw.includes("__Host-") && !raw.includes("payload_json") && !raw.includes("List-Unsubscribe"));
      writeFileSync(path.join(out, `${label}-export-${name}.json`), JSON.stringify(data, null, 2) + "\n");
      return data;
    }
    let succeeded = false;
    try {
      const ama = await context(), joon = await context({ javaScriptEnabled: false }), anonymous = await context({ javaScriptEnabled: false });
      const a = await login(ama, "ama"), j = await login(joon, "joon");
      let evidence = await local.tick();
      verify("both accounts start with only first-login security email enabled", ["ama", "joon"].every(person => kinds.every(kind => evidence.users[person].preferences[kind] === (kind === "security_first_login"))));
      await capture(a, "01-signed-in"); stage("sign-in", { accounts: 2, second_person_javascript: false });
      const key = await createKey(a);
      stage("one-time-report-key", { prompt_download_verified: true, secrets_saved: false });
      const apiReport = { kind: "question", checked_at: local.clock, places_checked: [{ name: "Fictional reports board", url: "https://fictional.example.invalid/board" }], posts_read: 17,
        question: { text: "Would a short fictional field test help?", why: "The fictional sensor needs one field check." } };
      const receipt = await ingest(ama, key, apiReport, "full-journey-api");
      verify("bearer report returns a concrete stored receipt", receipt.status() === 201 && (await receipt.json()).status === "received");
      await a.goto(base + "/app"); await a.reload();
      verify("API report survives reload in its owner's question card", await a.locator("[data-dashboard-state]").getAttribute("data-dashboard-state") === "question"
        && (await a.locator("[data-report-id]").innerText()).includes(apiReport.question.text));
      await j.reload();
      verify("other person's dashboard has no first-owner report", await j.locator(".report-history").count() === 0 && !(await j.locator("body").innerText()).includes(apiReport.question.text));
      await capture(a, "02-api-report"); stage("api-report", { owner_reports: 1, other_owner_reports: 0 });
      await navigate(a, a.getByRole("button", { name: "Mark reviewed", exact: true }));
      const pastedQuestion = "Should the fictional repair notes include a wiring diagram?";
      await a.goto(base + "/app/reports/paste");
      await a.getByLabel("Report from your assistant", { exact: true }).fill(`Kind: question\nChecked at: ${local.clock}\nPlaces checked: none\nPosts read: 0\nQuestion: ${pastedQuestion}\nWhy: The fictional notes need a clear next step.`);
      await navigate(a, a.getByRole("button", { name: "Preview report", exact: true }));
      verify("paste requires a visible unsaved preview", await a.locator("h1").innerText() === "Review this report."
        && (await a.locator("body").innerText()).includes("Nothing has been saved."));
      const beforeConfirm = await ama.newPage(); await beforeConfirm.goto(base + "/app");
      verify("unconfirmed paste has no dashboard effect", await beforeConfirm.locator(".report-history").count() === 1
        && !(await beforeConfirm.locator("body").innerText()).includes(pastedQuestion));
      await beforeConfirm.close(); await capture(a, "03-paste-preview");
      await navigate(a, a.getByRole("button", { name: "Confirm and save report", exact: true })); await a.reload();
      verify("confirmed paste becomes a persistent second report", await a.locator(".report-history").count() === 2
        && (await a.locator("[data-report-id]").innerText()).includes(pastedQuestion));
      stage("confirmed-paste", { owner_reports: 2, saved_only_after_confirmation: true });
      const amaSlug = await publish(a, "ama"), joonSlug = await publish(j, "joon");
      const publicPage = await anonymous.newPage(); await publicPage.goto(base + "/board/" + amaSlug);
      verify("public posting is readable without exposing account identity", (await publicPage.locator("body").innerText()).includes(postings.ama.text)
        && !(await publicPage.locator("body").innerText()).includes("ama@example.invalid") && !(await publicPage.locator("body").innerText()).includes("Ama Test"));
      await capture(publicPage, "04-public-posting"); stage("publication", { explicit_publications: 2 });
      await j.goto(base + "/board/" + amaSlug);
      await navigate(j, j.getByRole("link", { name: "Ask for an introduction", exact: true }));
      await j.getByLabel("Your note", { exact: true }).fill(note);
      await j.getByLabel("Why you would like to meet", { exact: true }).fill(reason);
      await navigate(j, j.getByRole("button", { name: "Preview request", exact: true }));
      await j.getByText("The posting you are responding to", { exact: true }).click();
      verify("second person previews exact note and both approved postings", (await j.locator("body").innerText()).includes(note)
        && (await j.locator("body").innerText()).includes(postings.ama.text) && (await j.locator("body").innerText()).includes(postings.joon.text));
      await navigate(j, j.getByRole("button", { name: "Send request", exact: true }));
      const introduction = await j.locator("[data-introduction-id]").getAttribute("data-introduction-id");
      verify("request waits for the first person's choice", await j.locator("[data-introduction-status]").getAttribute("data-introduction-status") === "requested"
        && (await j.locator("body").innerText()).includes("No conversation is open yet."));
      await a.goto(base + "/app/conversations");
      verify("request alone opens no conversation", await a.locator("[data-conversation-id]").count() === 0);
      evidence = await local.tick();
      verify("incoming request reaches the bell with optional email off", eventCount(evidence, "ama", "introduction_request") === 1 && mailCount(evidence, "ama", "introduction_request") === 0);
      await a.goto(base + "/app/introductions/" + introduction); await capture(a, "05-awaiting-consent");
      stage("introduction-request", { conversations_before_acceptance: 0, optional_email: 0 });
      await navigate(a, a.getByRole("button", { name: "Yes", exact: true }));
      const conversation = await a.locator("[data-conversation-id]").getAttribute("data-conversation-id");
      verify("first person's acceptance opens the shared conversation", typeof conversation === "string"
        && await a.locator("[data-conversation-status]").getAttribute("data-conversation-status") === "open");
      await j.goto(base + "/app/conversations/" + conversation);
      verify("second person now sees the same open conversation without JavaScript", await j.locator("[data-conversation-id]").getAttribute("data-conversation-id") === conversation
        && await j.getByLabel("Your message", { exact: true }).count() === 1);
      stage("mutual-consent", { conversations_after_acceptance: 1 });
      await send(a, conversation, messages.ama); await j.reload();
      verify("second person reads the first message as inert text", (await j.locator(".interaction-message .interaction-words").allTextContents()).includes(messages.ama)
        && await j.locator(".conversation-messages script").count() === 0);
      await send(j, conversation, messages.joon); await a.goto(base + "/app/conversations/" + conversation); await a.reload();
      verify("first person reads the reply and neither message becomes executable markup", (await a.locator(".interaction-message .interaction-words").allTextContents()).includes(messages.joon)
        && await a.locator(".conversation-messages script").count() === 0 && await a.evaluate(() => typeof window.journeyExecuted === "undefined"));
      evidence = await local.tick();
      verify("both message events remain in-app with optional email off", ["ama", "joon"].every(person => eventCount(evidence, person, "new_message") === 1 && mailCount(evidence, person, "new_message") === 0));
      await capture(a, "06-conversation"); stage("messages-both-directions", { messages: 2, optional_email: 0 });
      await j.goto(base + "/app/notifications/preferences");
      await j.locator("input[name='new_message']").check();
      await navigate(j, j.getByRole("button", { name: "Save email preferences", exact: true }));
      evidence = await local.snapshot();
      verify("explicit no-JS email opt-in changes only Joon's choice", evidence.users.joon.preferences.new_message === true
        && evidence.users.ama.preferences.new_message === false && evidence.users.joon.preferences.introduction_request === false);
      await send(a, conversation, messages.later); await send(a, conversation, messages.batch);
      evidence = await local.tick();
      verify("opted-in message mail waits for the hour", mailCount(evidence, "joon", "new_message") === 0);
      await local.advance(3599); evidence = await local.tick();
      verify("message mail stays pending before the exact hour", mailCount(evidence, "joon", "new_message") === 0);
      await local.advance(1); evidence = await local.tick();
      verify("two opted-in messages produce exactly one local email", mailCount(evidence, "joon", "new_message") === 1 && evidence.smtp_attempts === 0);
      verify("recorded email has one-click headers without private content", evidence.users.joon.mail.filter(item => item.kind === "new_message").every(item => item.one_click && item.same_origin_unsubscribe && item.has_message_id && !item.contains_private_words));
      await j.goto(base + "/app/notifications");
      verify("email batching leaves all individual message bell events", eventCount(evidence, "joon", "new_message") === 3
        && await j.locator("[data-notification-id]").count() === evidence.users.joon.visible_total);
      await capture(j, "07-notifications");
      stage("optional-hourly-email", { message_events: 3, local_message_emails: 1, smtp_attempts: 0 });
      const amaExport = await exportData(a, "ama"), joonExport = await exportData(j, "joon");
      verify("first person's export contains their reports and key metadata", amaExport.reports.length === 2
        && new Set(amaExport.reports.map(report => report.source)).size === 2
        && amaExport.reports.some(report => report.payload.question.text === pastedQuestion)
        && amaExport.keys.length === 1 && amaExport.keys[0].last_four === key.slice(-4));
      verify("exports contain only each person's authored message bodies", amaExport.messages.length === 3 && amaExport.messages.every(message => message.text !== messages.joon)
        && joonExport.messages.length === 1 && joonExport.messages[0].text === messages.joon && joonExport.reports.length === 0);
      verify("exports retain each person's independent email preference", amaExport.notification_preferences.new_message === false && joonExport.notification_preferences.new_message === true);
      stage("account-exports", { first_owner_reports: 2, first_owner_messages: 3, second_owner_messages: 1 });
      // Keep a copied pre-deletion session only in memory to verify revocation.
      const staleSession = await context(); await staleSession.addCookies(await ama.cookies());
      await a.getByLabel("Type DELETE to confirm", { exact: true }).fill("DELETE");
      await navigate(a, a.getByRole("button", { name: "Delete my account", exact: true }));
      verify("explicit account deletion displays its completed receipt", await a.locator("h1").innerText() === "Your account is deleted.");
      await capture(a, "08-deletion-receipt");
      const stale = await staleSession.request.get(base + "/app/account", { maxRedirects: 0 });
      verify("copied pre-deletion session cannot reopen the account", stale.status() === 303 && stale.headers().location === "/signin");
      verify("deleted key cannot send a report or replay an old receipt", (await ingest(anonymous, key, apiReport, "full-journey-deleted")).status() === 401
        && (await ingest(anonymous, key, apiReport, "full-journey-api")).status() === 401);
      verify("deleted public posting disappears from HTML and JSON", (await anonymous.request.get(base + "/board/" + amaSlug)).status() === 404
        && (await anonymous.request.get(base + "/board/" + amaSlug + ".json")).status() === 404
        && !(await (await anonymous.request.get(base + "/board.json")).json()).items.some(item => item.slug === amaSlug));
      await j.goto(base + "/app/conversations/" + conversation); await j.reload();
      const surviving = await j.locator(".interaction-message .interaction-words").allTextContents();
      verify("survivor keeps their message while the erased author's words disappear", surviving.includes(messages.joon)
        && surviving.filter(text => text === "[deleted]").length === 3 && !Object.values(messages).filter(text => text !== messages.joon).some(text => surviving.includes(text)));
      verify("surviving conversation closes and loses the erased shared reason", await j.locator("[data-conversation-status]").getAttribute("data-conversation-status") === "closed"
        && await j.getByLabel("Your message", { exact: true }).count() === 0 && await j.locator(".why").innerText() === "You both chose to talk");
      await capture(j, "09-survivor-conversation");
      await j.goto(base + "/app/introductions/" + introduction);
      verify("erasure removes both shared posting snapshots and introduction words", await j.locator(".posting-text,.interaction-words,.why").count() === 0
        && !(await j.locator("body").innerText()).includes(note) && !(await j.locator("body").innerText()).includes(reason));
      const survivorExport = await exportData(j, "survivor");
      verify("survivor export remains usable and excludes erased shared content", survivorExport.messages.length === 1 && survivorExport.messages[0].text === messages.joon
        && !JSON.stringify(survivorExport).includes(note) && !JSON.stringify(survivorExport).includes(postings.ama.text)
        && !JSON.stringify(survivorExport).includes(messages.ama));
      evidence = await local.snapshot();
      verify("erasure removes the owner's mail data and peer context notifications", !Object.hasOwn(evidence.users, "ama") && evidence.deleted_owner_rows === 0
        && eventCount(evidence, "joon", "new_message") === 0 && eventCount(evidence, "joon", "introduction_accepted") === 0);
      verify("other person's independent posting and security notification remain", (await anonymous.request.get(base + "/board/" + joonSlug)).status() === 200
        && eventCount(evidence, "joon", "security_first_login") === 1);
      stage("deletion-and-survivor", { stale_session_refused: true, report_key_refused: true, public_posting_removed: true, shared_context_erased: true, survivor_messages: 1 });
      verify("browser requests stayed on loopback", forbidden.length === 0);
      verify("browser execution has no script errors", scriptErrors.length === 0);
      succeeded = true;
    } finally {
      // Never capture a generic failure screenshot: it might expose the key page.
      for (const ctx of contexts) await ctx.close();
      const evidence = await local.stop();
      if (succeeded) {
        verify("both identities used real RSA and PKCE code exchanges", evidence.code_exchanges === 2 && evidence.pkce_verified === 2 && evidence.jwks_reads >= 1);
        verify("one explicit deletion left only the second account", Object.keys(evidence.modes.notifications.users).join() === "joon" && evidence.modes.notifications.deletion_receipts === 1);
        verify("outbox mode made no SMTP attempt", evidence.modes.notifications.smtp_attempts === 0);
        verify("fixture discloses that live Google was not tested", evidence.fixture_only === true && evidence.real_google_tested === false);
      }
    }
  }
  writeFileSync(path.join(out, "verification.json"), JSON.stringify({ checks, passed: checks.length, stages,
    code_source: "repository-source", fixture_only: true, real_google_tested: false, real_email_tested: false,
    viewports: ["desktop", "mobile"], second_person_javascript: false }, null, 2) + "\n");
  console.log(`${checks.length} complete two-person journey checks passed. Evidence: ${out}`);
} finally { await browser.close(); }
