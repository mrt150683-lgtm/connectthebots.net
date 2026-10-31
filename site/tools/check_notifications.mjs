// Fictional OIDC accounts exercise notifications and a local outbox; never SMTP.
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
const out = path.resolve(outArg > 0 ? process.argv[outArg + 1] : "artifacts/notifications-browser");
mkdirSync(out, { recursive: true });
const checks = [], kinds = ["security_first_login", "introduction_request", "introduction_accepted", "new_message", "checkin_missed", "weekly_summary"];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
function check(name, condition) { assert.ok(condition, name); checks.push({ check: name, passed: true }); }
async function navigate(page, control) {
  await Promise.all([page.waitForEvent("framenavigated", frame => frame === page.mainFrame()), control.click()]);
  await page.waitForLoadState("domcontentloaded");
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
    // Unsubscribe authority is used only in memory, never kept as an artifact.
    for (const filename of ["notifications-ack.json", "notifications-ack.pending"]) {
      const target = path.join(directory, filename); if (existsSync(target)) unlinkSync(target);
    }
    writeFileSync(path.join(directory, "stop"), "stop\n");
    const code = await Promise.race([finished, delay(10000).then(() => "timeout")]);
    writeFileSync(path.join(directory, "process.log"), output);
    if (code === "timeout") { child.kill(); throw new Error("Notification fixture did not stop"); }
    assert.equal(code, 0, output || "Notification fixture failed");
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
    throw new Error("Fictional notification command timed out");
  }
  for (let attempt = 0; attempt < 120; attempt++) {
    if (existsSync(path.join(directory, "ready.json"))) return {
      ...JSON.parse(readFileSync(path.join(directory, "ready.json"), "utf8")), stop,
      snapshot: () => command("notifications", { action: "snapshot" }),
      tick: () => command("notifications", { action: "tick" }),
      mailURL: (person, kind) => command("notifications", { action: "mail_url", person, kind }),
      advance: seconds => command("clock", { seconds }),
    };
    if (spawnError || child.exitCode !== null) throw new Error(output || String(spawnError));
    await delay(250);
  }
  child.kill(); throw new Error("Notification fixture startup timed out");
}

const postings = {
  ama: { text: "I build fictional soil sensors. I can offer field measurements. I am looking for low-power firmware help.", tags: "sensors, firmware", looking_for: "Low-power firmware help", offering: "Fictional field measurements" },
  joon: { text: "I repair fictional workshop tools. I can offer repair notes. I am looking for a documentation partner.", tags: "repair, documentation", looking_for: "A documentation partner", offering: "Fictional repair notes" },
};
const countKind = (snapshot, person, kind, field = "events") => snapshot.users[person][field].filter(item => item.kind === kind).length;
// Cancelled rows remain local records; resolving a request or unsubscribing
// deliberately prevents these historical outbox examples from later delivery.
const recorded = (snapshot, person, kind) => snapshot.users[person].mail.filter(item => item.kind === kind && ["outbox", "cancelled"].includes(item.state)).length;
const browser = await chromium.launch();
try {
  for (const [name, viewport] of [["desktop", { width: 1440, height: 900 }], ["mobile", { width: 390, height: 844 }]]) {
    const local = await fixture(name), base = local.modes.notifications.url;
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
      const page = await ctx.newPage(); await page.goto(base + "/signin");
      await navigate(page, page.getByRole("link", { name: "Continue with Google", exact: true }));
      await page.waitForURL(local.provider_url + "/authorize?**");
      await page.getByLabel("Fictional person").selectOption(person);
      await navigate(page, page.getByRole("button", { name: "Use fictional account", exact: true }));
      await page.waitForURL(base + "/app");
      verify(`${person} signs in through actual OIDC`, (await page.locator("body").innerText()).includes(person[0].toUpperCase() + person.slice(1) + " Test"));
      return page;
    }
    const form = async (page, action) => page.locator(`form[action='${action}']`).evaluate(node => Object.fromEntries(new FormData(node)));
    const post = (ctx, route, data, headers = { Origin: base }) => ctx.request.post(base + route, { form: data, headers, maxRedirects: 0 });
    async function publish(page, payload) {
      await page.goto(base + "/app/posting");
      await page.getByLabel("Public posting", { exact: true }).fill(payload.text);
      await page.getByLabel("Tags (comma separated, up to five)", { exact: true }).fill(payload.tags);
      await page.getByLabel("Looking for", { exact: true }).fill(payload.looking_for);
      await page.getByLabel("Offering", { exact: true }).fill(payload.offering);
      for (const button of ["Save draft", "Preview publication", "Publish"]) await navigate(page, page.getByRole("button", { name: button, exact: true }));
      return (await (await page.context().request.get(base + "/board.json")).json()).items.find(item => item.text === payload.text).slug;
    }
    async function request(page, slug) {
      await page.goto(base + "/app/introductions/new?slug=" + slug);
      await page.getByLabel("Your note", { exact: true }).fill("A fictional private note for this request.");
      await page.getByLabel("Why you would like to meet", { exact: true }).fill("Fictional repair notes could explain sensor measurements.");
      for (const button of ["Preview request", "Send request"]) await navigate(page, page.getByRole("button", { name: button, exact: true }));
      return page.locator("[data-introduction-id]").getAttribute("data-introduction-id");
    }
    async function accept(page, intro) {
      await page.goto(base + "/app/introductions/" + intro);
      await navigate(page, page.getByRole("button", { name: "Yes", exact: true }));
      return page.locator("[data-conversation-id]").getAttribute("data-conversation-id");
    }
    async function send(page, conversation, words) {
      await page.goto(base + "/app/conversations/" + conversation);
      await page.getByLabel("Your message", { exact: true }).fill(words);
      await navigate(page, page.getByRole("button", { name: "Send message", exact: true }));
    }
    async function preferences(page, enabled) {
      await page.goto(base + "/app/notifications/preferences");
      for (const kind of kinds) await page.locator(`input[name='${kind}']`).setChecked(enabled.includes(kind));
      await navigate(page, page.getByRole("button", { name: "Save email preferences", exact: true }));
    }
    async function schedule(page, paused) {
      await page.goto(base + "/app");
      await page.getByLabel("Expected check-in", { exact: true }).selectOption("1440");
      await page.getByLabel("Website report intake", { exact: true }).selectOption(String(paused));
      await navigate(page, page.getByRole("button", { name: "Save check-in settings", exact: true }));
    }
    let succeeded = false;
    try {
      const ama = await context(), joon = await context({ javaScriptEnabled: false }), priya = await context({ javaScriptEnabled: false }), anonymous = await context({ javaScriptEnabled: false });
      let a = await login(ama, "ama"), j = await login(joon, "joon"), p = await login(priya, "priya");
      let evidence = await local.tick();
      verify("new accounts default to security email only", ["ama", "joon", "priya"].every(person => kinds.every(kind => evidence.users[person].preferences[kind] === (kind === "security_first_login"))));
      verify("first-login email is recorded locally for each account", ["ama", "joon", "priya"].every(person => recorded(evidence, person, "security_first_login") === 1));
      await j.goto(base + "/app/notifications/preferences");
      verify("no-JS preference form visibly reflects security-only defaults", await j.locator("input[name='security_first_login']").isChecked() && !(await j.locator("input[name='new_message']").isChecked()) && !(await j.locator("input[name='weekly_summary']").isChecked()));
      await publish(a, postings.ama); const joonSlug = await publish(j, postings.joon);
      const firstIntro = await request(a, joonSlug);
      evidence = await local.tick();
      verify("incoming request creates a bell item with optional email off", countKind(evidence, "joon", "introduction_request") === 1 && recorded(evidence, "joon", "introduction_request") === 0);
      await j.goto(base + "/app/notifications");
      const unread = evidence.users.joon.unread_total;
      verify("bell count equals unread events", Number(await j.locator("[data-notification-count]").innerText()) === unread);
      const incoming = evidence.users.joon.events.find(item => item.kind === "introduction_request");
      verify("notification text is generic and links to its request", (await j.locator(`[data-notification-id='${incoming.id}']`).innerText()).includes("Someone would like an introduction") && (await j.locator(`[data-notification-id='${incoming.id}'] a`).getAttribute("href")) === "/app/introductions/" + firstIntro);
      await j.reload();
      verify("reading the notification list does not mark events read", (await local.snapshot()).users.joon.unread_total === unread);
      await a.goto(base + "/app/notifications");
      const ownCSRF = await a.locator("form input[name='csrf']").first().inputValue();
      verify("another account cannot mark a notification read", (await post(ama, `/app/notifications/${incoming.id}/read`, { csrf: ownCSRF })).status() === 404);
      await navigate(j, j.locator(`[data-notification-id='${incoming.id}']`).getByRole("button", { name: "Mark as read", exact: true }));
      verify("explicit no-JS read action updates only that item and the bell", Number(await j.locator("[data-notification-count]").innerText()) === unread - 1 && await j.locator(`[data-notification-id='${incoming.id}']`).getAttribute("data-notification-read") === "true");
      const firstConversation = await accept(j, firstIntro);
      await send(a, firstConversation, "Fictional private message with optional email off.");
      evidence = await local.tick();
      verify("accepted and message events remain in the bell when optional mail is off", countKind(evidence, "ama", "introduction_accepted") === 1 && countKind(evidence, "joon", "new_message") === 1 && recorded(evidence, "ama", "introduction_accepted") === 0 && recorded(evidence, "joon", "new_message") === 0);
      await navigate(a, a.getByRole("button", { name: "Leave conversation", exact: true }));
      await a.goto(base + "/app/notifications/preferences");
      const originalPreferences = await form(a, "/app/notifications/preferences");
      verify("preference updates require the real CSRF token", (await post(ama, "/app/notifications/preferences", { ...originalPreferences, csrf: "invalid", new_message: "on" })).status() === 403);
      verify("preference fields cannot select another owner", (await post(ama, "/app/notifications/preferences", { ...originalPreferences, user_id: "fixture-joon" })).status() === 400);
      verify("unexpected checkbox values are refused", (await post(ama, "/app/notifications/preferences", { ...originalPreferences, new_message: "true" })).status() === 400);
      await local.advance(60);
      await preferences(a, kinds);
      evidence = await local.snapshot();
      verify("saving one person's preferences leaves the other person's defaults", kinds.every(kind => evidence.users.ama.preferences[kind]) && kinds.every(kind => evidence.users.joon.preferences[kind] === (kind === "security_first_login")));
      await preferences(j, kinds);
      await j.screenshot({ path: path.join(out, `email-preferences-${name}.png`), fullPage: true });
      const secondIntro = await request(a, joonSlug), secondConversation = await accept(j, secondIntro);
      await send(a, secondConversation, "First fictional message in the hourly email batch.");
      await send(a, secondConversation, "Second fictional message in the same hourly email batch.");
      evidence = await local.tick();
      verify("opt-in records introduction and acceptance emails", recorded(evidence, "joon", "introduction_request") === 1 && recorded(evidence, "ama", "introduction_accepted") === 1);
      verify("resolved request mail is cancelled before any future delivery", evidence.users.joon.mail.filter(item => item.kind === "introduction_request").every(item => item.state === "cancelled"));
      verify("email preference does not suppress or merge individual bell events", countKind(evidence, "joon", "introduction_request") === 2 && countKind(evidence, "ama", "introduction_accepted") === 2 && countKind(evidence, "joon", "new_message") === 3);
      verify("new-message email waits for its hour instead of sending immediately", recorded(evidence, "joon", "new_message") === 0);
      await local.advance(3599); evidence = await local.tick();
      verify("batch stays pending before the exact hour", recorded(evidence, "joon", "new_message") === 0);
      await local.advance(1); evidence = await local.tick();
      verify("two messages produce one hourly email", recorded(evidence, "joon", "new_message") === 1);
      await send(a, secondConversation, "A third fictional message starts a later hourly batch.");
      await local.advance(3599); evidence = await local.tick();
      verify("later message cannot trigger a second email within the hour", recorded(evidence, "joon", "new_message") === 1);
      await local.advance(1); evidence = await local.tick();
      verify("later hourly batch produces exactly one more email", recorded(evidence, "joon", "new_message") === 2);
      const unsubscribe = await local.mailURL("joon", "new_message");
      const url = new URL(unsubscribe.url); assert.equal(url.origin, base); assert.ok(url.pathname.startsWith("/email/unsubscribe/"));
      const u = await anonymous.newPage();
      const get = await u.goto(url.href);
      verify("unsubscribe GET is cookie-free and does not mutate preferences", get.status() === 200 && (await local.snapshot()).users.joon.preferences.new_message === true && (await anonymous.cookies()).length === 0);
      verify("unsubscribe page discloses no account identity", !(await u.locator("body").innerText()).includes("joon@example.invalid") && !(await u.locator("body").innerText()).includes("Joon Test"));
      const [unsubscribed] = await Promise.all([u.waitForResponse(response => response.request().method() === "POST"), navigate(u, u.getByRole("button", { name: "Stop these emails", exact: true }))]);
      evidence = await local.snapshot();
      verify("native one-click unsubscribe has no redirect and disables only its owner and kind", unsubscribed.status() === 200 && !unsubscribed.headers().location && !evidence.users.joon.preferences.new_message && evidence.users.ama.preferences.new_message && kinds.filter(kind => kind !== "new_message").every(kind => evidence.users.joon.preferences[kind]));
      verify("provider-style one-click POST is idempotent without cookies", (await post(anonymous, url.pathname, { "List-Unsubscribe": "One-Click" }, {})).status() === 200);
      verify("unsubscribe refuses malformed intent", (await post(anonymous, url.pathname, { "List-Unsubscribe": "Wrong" }, {})).status() === 400);
      const invalidURL = url.pathname.slice(0, -1) + (url.pathname.endsWith("A") ? "B" : "A");
      verify("tampered unsubscribe authority is rejected", (await anonymous.request.get(base + invalidURL)).status() === 400);
      const redirectAttempt = await anonymous.request.get(url.href + "?next=https%3A%2F%2Fexample.invalid", { maxRedirects: 0 });
      verify("unsubscribe cannot become an external redirect", redirectAttempt.status() === 400 && !redirectAttempt.headers().location);
      await send(a, secondConversation, "Another fictional message after email unsubscribe.");
      await local.advance(3600); evidence = await local.tick();
      verify("unsubscribed message still reaches the bell without email", countKind(evidence, "joon", "new_message") === 5 && recorded(evidence, "joon", "new_message") === 2);
      await schedule(a, false); await schedule(j, true);
      await local.advance(86400 + 6 * 3600 - 1); evidence = await local.tick();
      verify("missed check-in waits for the explicit due time and grace", countKind(evidence, "ama", "checkin_missed") === 0);
      await local.advance(1); evidence = await local.tick();
      verify("missed check-in creates one bell item and opted-in email", countKind(evidence, "ama", "checkin_missed") === 1 && recorded(evidence, "ama", "checkin_missed") === 1);
      verify("paused report intake suppresses missed notifications", countKind(evidence, "joon", "checkin_missed") === 0 && recorded(evidence, "joon", "checkin_missed") === 0);
      evidence = await local.tick();
      verify("repeated scheduler ticks do not duplicate missed notices", countKind(evidence, "ama", "checkin_missed") === 1 && recorded(evidence, "ama", "checkin_missed") === 1);
      await local.advance(7 * 86400 - (3 * 3600 + 30 * 3600 + 60) - 1); evidence = await local.tick();
      verify("weekly summary waits for the original full-week schedule", ["ama", "joon", "priya"].every(person => countKind(evidence, person, "weekly_summary") === 0));
      await local.advance(1); evidence = await local.tick();
      verify("weekly email opt-in preserves the original website schedule", recorded(evidence, "ama", "weekly_summary") === 1 && recorded(evidence, "joon", "weekly_summary") === 1);
      verify("all six email types have a recorded local example", kinds.every(kind => ["ama", "joon"].some(person => recorded(evidence, person, kind) > 0)));
      verify("every recorded email has scoped one-click headers and excludes private words", Object.values(evidence.users).flatMap(user => user.mail).every(item => item.one_click && item.same_origin_unsubscribe && item.has_message_id && !item.contains_private_words));
      writeFileSync(path.join(out, `outbox-evidence-${name}.json`), JSON.stringify(evidence, null, 2) + "\n");
      await ama.clearCookies(); await joon.clearCookies(); await priya.clearCookies();
      a = await login(ama, "ama"); j = await login(joon, "joon"); p = await login(priya, "priya");
      await p.goto(base + "/app/notifications");
      const defaultWeekly = evidence.users.priya.events.find(item => item.kind === "weekly_summary");
      verify("weekly bell summary exists with weekly email left off", !!defaultWeekly && await p.locator(`[data-notification-id='${defaultWeekly.id}']`).count() === 1 && !evidence.users.priya.preferences.weekly_summary && recorded(evidence, "priya", "weekly_summary") === 0);
      await j.goto(base + "/app/notifications");
      const weekly = evidence.users.joon.events.find(item => item.kind === "weekly_summary");
      const ending = new Date(new Date(local.clock).getTime() + 7 * 86400000).toISOString().slice(0, 10);
      verify("weekly bell summary reports the actual bounded activity", (await j.locator(`[data-notification-id='${weekly.id}']`).innerText()).includes(`In the seven days ending ${ending}: 0 reports received; 0 available introduction updates; 4 messages received in available conversations.`));
      verify("notification page fits the viewport", await j.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await j.screenshot({ path: path.join(out, `notifications-${name}.png`), fullPage: true });
      await a.goto(base + "/app/account");
      const [download] = await Promise.all([a.waitForEvent("download"), a.getByRole("button", { name: "Download my data", exact: true }).click()]);
      const exportPath = path.join(out, `notification-export-${name}.json`); await download.saveAs(exportPath);
      const exported = JSON.parse(readFileSync(exportPath, "utf8"));
      verify("export contains the owner's notification preferences and event metadata", exported.notification_preferences.new_message === true && exported.notification_events.length > 0 && exported.email_outbox.length > 0);
      const exportedNotifications = JSON.stringify([exported.notification_preferences, exported.notification_events, exported.email_outbox]);
      verify("export excludes unsubscribe authority and email message bodies", !exportedNotifications.includes("/email/unsubscribe/") && !exportedNotifications.includes("payload_json") && !exportedNotifications.includes("List-Unsubscribe"));
      await a.getByLabel("Type DELETE to confirm", { exact: true }).fill("DELETE");
      await navigate(a, a.getByRole("button", { name: "Delete my account", exact: true }));
      verify("explicit account deletion completes", await a.locator("h1").innerText() === "Your account is deleted.");
      evidence = await local.snapshot();
      verify("deletion removes the account's notification preferences and outbox", !Object.hasOwn(evidence.users, "ama") && evidence.deleted_owner_rows === 0);
      verify("deletion removes peer events that referred to erased shared context", countKind(evidence, "joon", "introduction_request") === 0 && countKind(evidence, "joon", "new_message") === 0);
      verify("other person's independent security and weekly notifications survive", countKind(evidence, "joon", "security_first_login") === 1 && countKind(evidence, "joon", "weekly_summary") === 1);
      verify("no browser request left loopback", forbidden.length === 0);
      verify("browser execution has no script errors", scriptErrors.length === 0);
      succeeded = true;
    } finally {
      for (const ctx of contexts) await ctx.close();
      const evidence = await local.stop();
      if (succeeded) {
        verify("fixture uses actual OIDC signatures and PKCE", evidence.code_exchanges === 6 && evidence.pkce_verified === 6 && evidence.jwks_reads >= 1);
        verify("outbox mode never attempted network delivery", evidence.modes.notifications.smtp_attempts === 0);
        verify("fixture clearly distinguishes local tests from live services", evidence.fixture_only === true && evidence.real_google_tested === false);
      }
    }
  }
  writeFileSync(path.join(out, "verification.json"), JSON.stringify({ checks, passed: checks.length, fixture_only: true, real_google_tested: false, real_email_tested: false }, null, 2) + "\n");
  console.log(`${checks.length} notification browser checks passed. Evidence: ${out}`);
} finally { await browser.close(); }
