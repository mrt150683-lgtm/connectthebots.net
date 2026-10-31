// Real local operator CLI plus fictional OIDC browser reporting journeys.
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
const out = path.resolve(outArg > 0 ? process.argv[outArg + 1] : "artifacts/moderation-browser");
mkdirSync(out, { recursive: true });
const checks = [], delay = ms => new Promise(resolve => setTimeout(resolve, ms));
function check(name, value) { assert.ok(value, name); checks.push({ check: name, passed: true }); }
async function navigate(page, control) {
  await Promise.all([page.waitForEvent("framenavigated", frame => frame === page.mainFrame()), control.click()]);
  await page.waitForLoadState("domcontentloaded");
}
async function fixture(name) {
  const directory = mkdtempSync(path.join(out, `fixture-${name}-`));
  const child = spawn(python, [path.join(repository, "tests_server", "oidc_fixture.py"), "--moderation", "--out", directory], {
    cwd: repository, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", PYTHONUNBUFFERED: "1" }, stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "", error;
  child.stdout.on("data", data => { output += data.toString(); }); child.stderr.on("data", data => { output += data.toString(); });
  const finished = new Promise(resolve => { child.once("error", value => { error = value; resolve(-1); }); child.once("exit", resolve); });
  async function command(value) {
    const id = "moderation-" + Date.now() + "-" + Math.random().toString(16).slice(2);
    const pending = path.join(directory, "moderation-command.pending");
    writeFileSync(pending, JSON.stringify({ id, ...value })); renameSync(pending, path.join(directory, "moderation-command.json"));
    for (let attempt = 0; attempt < 600; attempt++) {
      const ack = path.join(directory, "moderation-ack.json");
      if (existsSync(ack)) {
        try { const result = JSON.parse(readFileSync(ack, "utf8")); if (result.id === id) { unlinkSync(ack); return result; } }
        catch (value) { if (!(value instanceof SyntaxError) && !["EACCES", "EPERM", "EBUSY", "ENOENT"].includes(value.code)) throw value; }
      }
      if (error || child.exitCode !== null) throw new Error(output || String(error));
      await delay(100);
    }
    throw new Error("Fictional moderation command timed out");
  }
  async function stop() {
    writeFileSync(path.join(directory, "stop"), "stop\n");
    const code = await Promise.race([finished, delay(10000).then(() => "timeout")]);
    writeFileSync(path.join(directory, "process.log"), output);
    if (code === "timeout") { child.kill(); throw new Error("Moderation fixture did not stop"); }
    assert.equal(code, 0, output);
    const evidence = JSON.parse(readFileSync(path.join(directory, "fixture-evidence.json"), "utf8"));
    writeFileSync(path.join(out, `fixture-evidence-${name}.json`), JSON.stringify(evidence, null, 2) + "\n"); return evidence;
  }
  for (let attempt = 0; attempt < 120; attempt++) {
    if (existsSync(path.join(directory, "ready.json"))) return { ...JSON.parse(readFileSync(path.join(directory, "ready.json"), "utf8")), stop,
      snapshot: () => command({ action: "snapshot" }), operator: (operation, target = {}) => command({ action: "operator", operation, ...target }),
      pauseToken: () => command({ action: "pause_token" }), resumeToken: () => command({ action: "resume_token" }) };
    if (error || child.exitCode !== null) throw new Error(output || String(error)); await delay(250);
  }
  child.kill(); throw new Error("Moderation fixture startup timed out");
}
const posting = person => ({ text: `I build fictional ${person} workshop tools. I can offer lab measurements. I am looking for repair documentation.`, tags: "fictional, tools", looking_for: "Repair documentation", offering: "Fictional lab measurements" });
const details = "Fictional concern about this posting's claims. Please review its stated evidence.";
const privateConcern = "Fictional private concern about this conversation. Please review the conduct.";
const browser = await chromium.launch();
try {
  for (const [name, viewport] of [["desktop", { width: 1440, height: 900 }], ["mobile", { width: 390, height: 844 }]]) {
    const local = await fixture(name), base = local.modes.moderation.url;
    const contexts = [], forbidden = [], errors = [], verify = (label, value) => check(`${name}: ${label}`, value);
    async function context(options = {}) {
      const ctx = await browser.newContext({ viewport, ...options }); contexts.push(ctx);
      ctx.on("page", page => page.on("pageerror", error => errors.push(error.message)));
      await ctx.route("**/*", route => { const host = new URL(route.request().url()).hostname;
        if (["localhost", "127.0.0.1", "[::1]"].includes(host)) return route.continue(); forbidden.push(host); return route.abort(); }); return ctx;
    }
    async function login(ctx, person, suspended = false) {
      const page = await ctx.newPage(); await page.goto(base + "/signin");
      await navigate(page, page.getByRole("link", { name: "Continue with Google", exact: true }));
      await page.waitForURL(local.provider_url + "/authorize?**"); await page.getByLabel("Fictional person").selectOption(person);
      const [callback] = await Promise.all([page.waitForResponse(response => new URL(response.url()).pathname === "/auth/google/callback"), navigate(page, page.getByRole("button", { name: "Use fictional account", exact: true }))]);
      if (suspended) verify("suspended person cannot complete a fresh OIDC login", callback.status() === 400 && (await page.locator("body").innerText()).includes("This account is not available. Please contact the service operator.") && (await local.snapshot()).users[person].sessions === 0);
      else { await page.waitForURL(base + "/app"); verify(`${person} signs in through actual OIDC`, (await page.locator("body").innerText()).includes(person[0].toUpperCase() + person.slice(1) + " Test")); }
      return page;
    }
    const form = (page, action) => page.locator(`form[action='${action}']`).evaluate(node => Object.fromEntries(new FormData(node)));
    const post = (ctx, route, data) => ctx.request.post(base + route, { form: data, headers: { Origin: base }, maxRedirects: 0 });
    async function operator(operation, target = {}) { const result = await local.operator(operation, target); assert.equal(result.cli.exit_code, 0, result.cli.error); return result; }
    async function publish(page, person) {
      const payload = posting(person); await page.goto(base + "/app/posting");
      for (const [label, value] of [["Public posting", payload.text], ["Tags (comma separated, up to five)", payload.tags], ["Looking for", payload.looking_for], ["Offering", payload.offering]]) await page.getByLabel(label, { exact: true }).fill(value);
      for (const button of ["Save draft", "Preview publication", "Publish"]) await navigate(page, page.getByRole("button", { name: button, exact: true }));
      return (await (await page.context().request.get(base + "/board.json")).json()).items.find(item => item.text === payload.text);
    }
    async function intro(page, slug) {
      await page.goto(base + "/app/introductions/new?slug=" + slug); await page.getByLabel("Your note", { exact: true }).fill("Could we review fictional tool measurements?");
      await page.getByLabel("Why you would like to meet", { exact: true }).fill("Our fictional documentation work overlaps.");
      for (const button of ["Preview request", "Send request"]) await navigate(page, page.getByRole("button", { name: button, exact: true }));
      return page.locator("[data-introduction-id]").getAttribute("data-introduction-id");
    }
    async function accept(page, id) { await page.goto(base + "/app/introductions/" + id); await navigate(page, page.getByRole("button", { name: "Yes", exact: true })); return page.locator("[data-conversation-id]").getAttribute("data-conversation-id"); }
    async function send(page, id, text) { await page.goto(base + "/app/conversations/" + id); await page.getByLabel("Your message", { exact: true }).fill(text); await navigate(page, page.getByRole("button", { name: "Send message", exact: true })); }
    async function exportText(page) { await page.goto(base + "/app/account"); const data = await form(page, "/app/account/export"); const response = await post(page.context(), "/app/account/export", data); assert.equal(response.status(), 200); return response.text(); }
    let succeeded = false;
    try {
      const ama = await context(), joon = await context({ javaScriptEnabled: false }), priya = await context(), anonymous = await context({ javaScriptEnabled: false });
      let a = await login(ama, "ama"), j = await login(joon, "joon"), p = await login(priya, "priya");
      const mine = await publish(a, "ama"), third = await publish(p, "priya");
      let theirs = await publish(j, "joon");
      verify("operator endpoints are absent from HTTP", (await anonymous.request.get(base + "/admin")).status() === 404 && (await ama.request.get(base + "/app/admin")).status() === 404);
      await a.goto(base + "/board/" + theirs.slug); await navigate(a, a.getByRole("link", { name: "Report", exact: true }));
      verify("posting Report opens a native concern form", await a.locator("h1").innerText() === "Report a concern.");
      verify("GET report form has no stored or email effect", (await local.snapshot()).abuse_reports === 0 && (await local.snapshot()).operator_email_records === 0);
      await a.getByLabel("Reason", { exact: true }).selectOption("impersonation"); await a.getByLabel("Details (optional)", { exact: true }).fill(details);
      const submitted = await form(a, "/app/report");
      verify("reporting requires the session CSRF token", (await post(ama, "/app/report", { ...submitted, csrf: "invalid" })).status() === 403);
      verify("reporter or subject cannot be supplied by a form", (await post(ama, "/app/report", { ...submitted, reporter_id: "fixture-joon" })).status() === 400);
      await p.goto(base + "/app/report?kind=posting&target=" + theirs.slug);
      const otherOwnerForm = await form(p, "/app/report");
      const copied = await post(priya, "/app/report", { ...submitted, csrf: otherOwnerForm.csrf });
      verify("a copied report token cannot be used by another signed-in owner", copied.status() === 400 && (await copied.text()).includes("This report form expired or changed.") && (await local.snapshot()).abuse_reports === 0);
      verify("script-shaped report details are refused", (await post(ama, "/app/report", { ...submitted, details: "<script>alert(1)</script>" })).status() === 422);
      verify("people cannot report their own posting", (await ama.request.get(base + "/app/report?kind=posting&target=" + mine.slug)).status() === 404);
      await a.screenshot({ path: path.join(out, `report-form-${name}.png`), fullPage: true });
      await navigate(a, a.getByRole("button", { name: "Send report", exact: true }));
      const receipt = await a.locator("[data-report-receipt]").getAttribute("data-report-id");
      await a.screenshot({ path: path.join(out, `report-received-${name}.png`), fullPage: true });
      verify("explicit submission confirms one report without changing publication", await a.locator("h1").innerText() === "Report received." && (await anonymous.request.get(base + "/board/" + theirs.slug)).status() === 200);
      let evidence = await local.snapshot();
      verify("one report produces one operator alert and local email record", evidence.abuse_reports === 1 && evidence.moderation_receipts === 1 && evidence.operator_alerts === 1 && evidence.operator_email_records === 1);
      const replay = await post(ama, "/app/report", submitted);
      verify("repeated submission returns the same receipt without duplicate alerts", replay.status() === 200 && (await replay.text()).includes(receipt) && (await local.snapshot()).abuse_reports === 1 && (await local.snapshot()).operator_email_records === 1);
      verify("changed details cannot reuse the same report nonce", (await post(ama, "/app/report", { ...submitted, details: "Different fictional report." })).status() === 409);
      verify("export includes own submitted concern and excludes another person's concern", (await exportText(a)).includes(details) && !(await exportText(j)).includes(details));
      evidence = await operator("reports"); verify("real reports CLI can list the recorded concern", JSON.stringify(evidence.cli.result).includes(receipt));
      await a.goto(base + "/app/report?kind=posting&target=" + theirs.slug); const oldGrantForm = await form(a, "/app/report");
      theirs = await publish(j, "joon");
      verify("copied report form cannot approve a changed publication grant", (await post(ama, "/app/report", { ...oldGrantForm, reason: "other", details: "Fictional outdated publication." })).status() === 409 && (await local.snapshot()).abuse_reports === 1);
      await a.goto(base + "/app/report?kind=posting&target=" + theirs.slug); const hiddenForm = await form(a, "/app/report");
      await operator("hide", { slug: theirs.slug });
      verify("hidden posting disappears from both public formats", (await anonymous.request.get(base + "/board/" + theirs.slug)).status() === 410 && (await anonymous.request.get(base + "/board/" + theirs.slug + ".json")).status() === 410);
      verify("copied form rechecks a now-hidden target", (await post(ama, "/app/report", { ...hiddenForm, reason: "other", details: "Fictional stale target." })).status() === 404 && (await local.snapshot()).abuse_reports === 1);
      await operator("unhide", { slug: theirs.slug });
      const waiting = await intro(a, theirs.slug); await j.goto(base + "/app/introductions/" + waiting); const oldYes = await form(j, `/app/introductions/${waiting}/accept`);
      // No HTTP read between hide and unhide: the mutation itself must revoke pending consent.
      await operator("hide", { slug: theirs.slug }); await operator("unhide", { slug: theirs.slug });
      verify("hide then unhide cannot revive pending acceptance", (await post(joon, `/app/introductions/${waiting}/accept`, oldYes)).status() === 410);
      const visible = await (await anonymous.request.get(base + "/board/" + theirs.slug + ".json")).json();
      verify("unhide retains exactly the previously approved posting", visible.text === theirs.text && visible.published_at === theirs.published_at);
      const shared = await accept(j, await intro(a, theirs.slug)); await send(a, shared, "Ama's fictional message to erase."); await send(j, shared, "Joon's own fictional message to retain.");
      verify("nonparticipant cannot report a private conversation", (await priya.request.get(base + "/app/report?kind=conversation&target=" + shared)).status() === 404);
      await navigate(j, j.getByRole("button", { name: "Block", exact: true }));
      await navigate(j, j.getByRole("link", { name: "Report", exact: true }));
      await j.getByLabel("Reason", { exact: true }).selectOption("harassment"); await j.getByLabel("Details (optional)", { exact: true }).fill(privateConcern);
      await navigate(j, j.getByRole("button", { name: "Send report", exact: true }));
      verify("participant can report a retained blocked conversation without JavaScript", await j.locator("[data-report-receipt]").count() === 1 && (await local.snapshot()).abuse_reports === 2);
      verify("reporting does not restore blocked visibility or contact", (await ama.request.get(base + "/board/" + theirs.slug)).status() === 404 && (await joon.request.get(base + "/board/" + mine.slug)).status() === 404 && (await anonymous.request.get(base + "/board/" + theirs.slug)).status() === 200);
      verify("reported person cannot export the reporter's private concern", !(await exportText(a)).includes(privateConcern));
      await a.goto(base + "/app/keys/new"); await a.getByLabel("Key name", { exact: true }).fill("Fictional operator verification"); await navigate(a, a.getByRole("button", { name: "Create key and prompt", exact: true }));
      const key = await a.locator("#created-report-key").innerText(); assert.match(key, /^ctb_[A-Z2-7]{52}$/);
      verify("report key works before suspension", (await ama.request.get(base + "/api/v1/me", { headers: { Authorization: "Bearer " + key } })).status() === 200);
      const otherConversation = await accept(p, await intro(a, third.slug));
      const oldMessage = await form(p, `/app/conversations/${otherConversation}/messages`);
      const oldCookie = (await ama.cookies()).find(cookie => cookie.name === "__Host-ctb_session");
      evidence = await operator("suspend", { person: "ama" });
      verify("suspension revokes existing sessions and report keys", evidence.users.ama.suspended && evidence.users.ama.sessions === 0 && evidence.users.ama.active_keys === 0);
      verify("suspended key cannot read account metadata", (await ama.request.get(base + "/api/v1/me", { headers: { Authorization: "Bearer " + key } })).status() === 401);
      verify("suspension hides the user's public posting", (await anonymous.request.get(base + "/board/" + mine.slug)).status() === 410);
      verify("suspension closes contact even for the still-active other participant", (await post(priya, `/app/conversations/${otherConversation}/messages`, { ...oldMessage, text: "Must not send after suspension." })).status() === 409);
      await ama.clearCookies(); await login(ama, "ama", true);
      await operator("unsuspend", { person: "ama" });
      const retained = await context(); await retained.addCookies([oldCookie]);
      verify("unsuspend cannot restore a previous session", (await retained.request.get(base + "/app", { maxRedirects: 0 })).status() === 303);
      verify("unsuspend cannot restore old report keys or publication", (await ama.request.get(base + "/api/v1/me", { headers: { Authorization: "Bearer " + key } })).status() === 401 && (await anonymous.request.get(base + "/board/" + mine.slug)).status() === 410);
      await ama.clearCookies(); a = await login(ama, "ama");
      await p.goto(base + "/app/conversations/" + otherConversation);
      verify("fresh login does not reopen a closed conversation", await p.locator("[data-conversation-status]").getAttribute("data-conversation-status") === "closed");
      await j.goto(base + "/app/conversations/" + shared); await j.screenshot({ path: path.join(out, `reported-conversation-${name}.png`), fullPage: true });
      verify("reporting controls fit the viewport", await j.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await operator("show", { person: "ama" }); await operator("stats");
      const unfinished = await context(), unfinishedPage = await unfinished.newPage();
      await unfinishedPage.goto(base + "/signin");
      await navigate(unfinishedPage, unfinishedPage.getByRole("link", { name: "Continue with Google", exact: true }));
      await unfinishedPage.waitForURL(local.provider_url + "/authorize?**");
      await unfinishedPage.getByLabel("Fictional person").selectOption("ama");
      await local.pauseToken();
      const lateCallback = unfinishedPage.waitForResponse(response => new URL(response.url()).pathname === "/auth/google/callback");
      const approval = navigate(unfinishedPage, unfinishedPage.getByRole("button", { name: "Use fictional account", exact: true }));
      let responsePaused = false;
      for (let attempt = 0; attempt < 100; attempt++) {
        if ((await local.snapshot()).token_response_pending) { responsePaused = true; break; }
        await delay(100);
      }
      verify("erasure race pauses an actual verified-PKCE token exchange", responsePaused);
      await operator("delete", { person: "ama" });
      await local.resumeToken();
      const completedCallback = await lateCallback; await approval;
      verify("an in-flight sign-in cannot recreate an operator-deleted account", completedCallback.status() === 400 && (await unfinishedPage.locator("body").innerText()).includes("This sign-in attempt is no longer current. Please start again.") && (await unfinished.request.get(base + "/app", { maxRedirects: 0 })).status() === 303);
      evidence = await local.snapshot();
      verify("operator deletion erases reports and alerts involving the deleted person", !Object.hasOwn(evidence.users, "ama") && evidence.abuse_reports === 0 && evidence.moderation_receipts === 0 && evidence.operator_alerts === 0 && evidence.operator_email_records === 0);
      await j.goto(base + "/app/conversations/" + shared);
      const words = await j.locator(".interaction-message .interaction-words").allTextContents();
      verify("operator deletion preserves only the surviving author's message", words.includes("[deleted]") && words.includes("Joon's own fictional message to retain.") && !words.includes("Ama's fictional message to erase."));
      verify("deleted posting is absent rather than retained as identifying tombstone", (await anonymous.request.get(base + "/board/" + mine.slug + ".json")).status() === 404);
      verify("browser contacted no external host", forbidden.length === 0); verify("browser has no script errors", errors.length === 0); succeeded = true;
    } finally {
      for (const ctx of contexts) await ctx.close(); const evidence = await local.stop();
      if (succeeded) { verify("six real fixture OIDC exchanges verify PKCE", evidence.code_exchanges === 6 && evidence.pkce_verified === 6); verify("fictional evidence does not claim live Google verification", evidence.fixture_only === true && evidence.real_google_tested === false); }
    }
  }
  writeFileSync(path.join(out, "verification.json"), JSON.stringify({ checks, passed: checks.length, fixture_only: true, real_google_tested: false }, null, 2) + "\n");
  console.log(`${checks.length} moderation browser checks passed. Evidence: ${out}`);
} finally { await browser.close(); }
