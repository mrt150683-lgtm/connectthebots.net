// Hosted report journeys using fictional accounts and the real local RSA OIDC flow.
// Private one-time keys stay in process memory; screenshots never include their page.
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import assert from "node:assert/strict";
import { isDeepStrictEqual } from "node:util";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const localPython = path.join(repository, ".venv", "Scripts", "python.exe");
const python = process.env.PYTHON || (process.platform === "win32" && existsSync(localPython) ? localPython : "python3");
const outArg = process.argv.indexOf("--out");
const out = path.resolve(outArg > 0 ? process.argv[outArg + 1] : "artifacts/report-browser");
mkdirSync(out, { recursive: true });
const checks = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
function check(name, condition) {
  assert.ok(condition, name);
  checks.push({ check: name, passed: true });
}

async function fixture(name) {
  const directory = mkdtempSync(path.join(out, `fixture-${name}-`));
  const child = spawn(python, [path.join(repository, "tests_server", "oidc_fixture.py"), "--reports", "--out", directory], {
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
    if (code === "timeout") { child.kill(); throw new Error("Report fixture did not stop"); }
    writeFileSync(path.join(directory, "process.log"), output);
    assert.equal(code, 0, output || "Report fixture failed");
    const evidence = JSON.parse(readFileSync(path.join(directory, "fixture-evidence.json"), "utf8"));
    writeFileSync(path.join(out, `fixture-evidence-${name}.json`), JSON.stringify(evidence, null, 2) + "\n");
    return evidence;
  }
  async function advance(seconds) {
    const id = "browser-clock-" + Date.now();
    const pending = path.join(directory, "clock-command.pending");
    writeFileSync(pending, JSON.stringify({ id, seconds }));
    renameSync(pending, path.join(directory, "clock-command.json"));
    for (let attempt = 0; attempt < 100; attempt++) {
      const ack = path.join(directory, "clock-ack.json");
      if (existsSync(ack)) {
        const result = JSON.parse(readFileSync(ack, "utf8"));
        if (result.id === id) return result.clock;
      }
      await delay(100);
    }
    throw new Error("Test clock did not advance");
  }
  for (let attempt = 0; attempt < 120; attempt++) {
    if (existsSync(path.join(directory, "ready.json"))) {
      return { ...JSON.parse(readFileSync(path.join(directory, "ready.json"), "utf8")), stop, advance };
    }
    if (spawnError || child.exitCode !== null) throw new Error(output || String(spawnError));
    await delay(250);
  }
  child.kill();
  throw new Error("Report fixture startup timed out: " + output);
}

async function downloadText(download) {
  const stream = await download.createReadStream();
  let text = "";
  for await (const chunk of stream) text += chunk.toString("utf8");
  return text;
}

const stateWords = {
  setup: "Your account is ready.", working: "Last seen",
  quiet: "Last seen", question: "One question for you.",
  connection: "A connection worth a look.", paused: "Report intake is paused.",
  failed: "The expected check-in has not arrived.",
};
const browser = await chromium.launch();
try {
  for (const [name, viewport] of [["desktop", { width: 1440, height: 900 }], ["mobile", { width: 390, height: 844 }]]) {
    const local = await fixture(name);
    const base = local.modes.reports.url;
    const contexts = [], forbiddenRequests = [], pageErrors = [];
    async function context(options = {}) {
      const ctx = await browser.newContext({ viewport, ...options });
      contexts.push(ctx);
      ctx.on("page", page => page.on("pageerror", error => pageErrors.push(error.message)));
      await ctx.route("**/*", route => {
        const host = new URL(route.request().url()).hostname;
        if (["localhost", "127.0.0.1", "[::1]"].includes(host)) return route.continue();
        forbiddenRequests.push(host);
        return route.abort();
      });
      return ctx;
    }
    async function login(ctx, person) {
      const page = await ctx.newPage();
      await page.goto(base + "/signin");
      await page.getByRole("link", { name: "Continue with Google", exact: true }).click();
      await page.waitForURL(local.provider_url + "/authorize?**");
      await page.getByLabel("Fictional person").selectOption(person);
      await page.getByRole("button", { name: "Use fictional account", exact: true }).click();
      await page.waitForURL(base + "/app");
      check(`${name}: ${person} signed in through the real OIDC callback`,
            (await page.locator("[data-dashboard-state]").count()) === 1
            && (await page.locator("body").innerText()).includes(person === "ama" ? "Ama Test" : "Joon Test"));
      return page;
    }
    async function state(page, expected, screenshot = true, words = stateWords[expected]) {
      const root = page.locator("[data-dashboard-state]");
      await page.waitForLoadState("load");
      check(`${name}: ${expected} reflects stored activity or an explicit setting`,
            await root.getAttribute("data-dashboard-state") === expected && (await root.innerText()).includes(words));
      check(`${name}: ${expected} fits the viewport`, await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
      if (screenshot) await page.screenshot({ path: path.join(out, `dashboard-${expected}-${name}.png`), fullPage: true });
    }
    async function createKey(page, keyName, copy) {
      await page.goto(base + "/app/keys/new");
      await page.getByLabel("Key name", { exact: true }).fill(keyName);
      await page.getByRole("button", { name: "Create key and prompt", exact: true }).click();
      const key = await page.locator("#created-report-key").innerText();
      const prompt = await page.getByLabel("Your personalised prompt", { exact: true }).inputValue();
      check(`${name}: ${keyName} key has the required random-token format`, /^ctb_[A-Z2-7]{52}$/.test(key));
      check(`${name}: private prompt includes this origin, bearer key and retry instructions`,
            prompt.includes(base + "/api/v1/reports") && prompt.includes("Authorization: Bearer " + key)
            && prompt.includes("Idempotency-Key: UNIQUE-REPORT-ID") && prompt.includes("same identifier and exactly the same body"));
      check(`${name}: private prompt remains selectable without scripting`, await page.locator("#personalised-prompt").getAttribute("readonly") !== null);
      if (copy) {
        await page.getByRole("button", { name: "Copy private prompt", exact: true }).click();
        await page.getByText("Copied. Keep this prompt private.", { exact: true }).waitFor();
        check(`${name}: private prompt copy keeps every character`,
              (await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, "\n") === prompt.replace(/\r\n/g, "\n"));
      } else {
        check(`${name}: no-JavaScript prompt does not offer a non-working copy button`, !(await page.locator("[data-copy-prompt]").isVisible()));
      }
      const [download] = await Promise.all([
        page.waitForEvent("download"), page.getByRole("button", { name: "Download private prompt", exact: true }).click(),
      ]);
      check(`${name}: private prompt download matches the one-time handoff`,
            (await downloadText(download)).replace(/\r\n/g, "\n") === prompt.replace(/\r\n/g, "\n"));
      await page.goto(base + "/app/keys");
      check(`${name}: leaving the handoff hides the full key and private prompt`,
            !(await page.content()).includes(key) && await page.locator("#created-report-key, #personalised-prompt").count() === 0
            && (await page.locator("body").innerText()).includes(key.slice(-4)));
      const row = page.locator("[data-key-id]").filter({ has: page.getByRole("heading", { name: keyName, exact: true }) });
      return { key, id: await row.getAttribute("data-key-id"), name: keyName };
    }
    async function ingest(ctx, key, payload, id) {
      return ctx.request.post(base + "/api/v1/reports", {
        headers: { Authorization: "Bearer " + key, "Idempotency-Key": id, "Content-Type": "application/json" },
        data: JSON.stringify(payload),
      });
    }
    async function settings(page, paused, cadence) {
      await page.goto(base + "/app");
      await page.getByLabel("Website report intake", { exact: true }).selectOption(String(paused));
      await page.getByLabel("Expected check-in", { exact: true }).selectOption(cadence);
      await page.getByRole("button", { name: "Save check-in settings", exact: true }).click();
    }
    let succeeded = false;
    try {
      const owner = await context({ permissions: ["clipboard-read", "clipboard-write"] });
      const page = await login(owner, "ama");
      await state(page, "setup");
      const issued = await createKey(page, "Fictional assistant", true);
      const publicPrompt = await (await owner.request.get(base + "/assets/connect-the-bots-prompt.md")).text();
      check(`${name}: public prompt stays key-free and usable without delivery`, !publicPrompt.includes(issued.key)
            && !publicPrompt.includes("Authorization: Bearer") && publicPrompt.includes("This public prompt contains no report key"));
      await page.goto(base + "/app");
      await state(page, "setup", false, "Waiting for your agent.");
      check(`${name}: no expected time means no missed-report claim`, (await page.locator("body").innerText()).includes("We never call a report missed without a schedule you choose."));

      const common = { checked_at: local.clock, places_checked: [{ name: "Fictional reports board", url: "https://fictional.example.invalid/board" }], posts_read: 17 };
      const nothing = { kind: "nothing", ...common };
      const first = await ingest(owner, issued.key, nothing, "fixture-nothing");
      const receipt = await first.json();
      check(`${name}: HTTPS-shaped report contract is accepted through real local HTTP`, first.status() === 201
            && receipt.status === "received" && typeof receipt.report_id === "string" && typeof receipt.received_at === "string");
      const replay = await ingest(owner, issued.key, nothing, "fixture-nothing");
      check(`${name}: identical HTTP replay returns the original receipt`, replay.status() === 201 && isDeepStrictEqual(await replay.json(), receipt));
      const conflict = await ingest(owner, issued.key, { ...nothing, posts_read: 18 }, "fixture-nothing");
      check(`${name}: changed body with reused idempotency key is refused`, conflict.status() === 409);
      const cookieOnly = await owner.request.post(base + "/api/v1/reports", {
        headers: { "Idempotency-Key": "cookie-is-not-report-authority", "Content-Type": "application/json" }, data: JSON.stringify(nothing),
      });
      check(`${name}: browser session alone cannot submit to the bearer endpoint`, cookieOnly.status() === 400);
      await page.goto(base + "/app");
      await state(page, "quiet");
      check(`${name}: replay produced one saved report`, await page.locator(".report-history").count() === 1);
      await page.locator(".report-history > summary").click();
      await page.locator(".report-history .log summary").click();
      check(`${name}: report shows the submitted place and count`, (await page.locator(".report-history").innerText()).includes("Fictional reports board")
            && (await page.locator(".report-history").innerText()).includes("17."));

      const question = { kind: "question", ...common, question: { text: "Would a short field test help?", why: "The fictional sensor needs one field check." } };
      check(`${name}: question report is accepted`, (await ingest(owner, issued.key, question, "fixture-question")).status() === 201);
      await page.goto(base + "/app");
      await state(page, "question");
      check(`${name}: question presents its reason without pretending to answer the assistant`,
            (await page.locator("[data-report-id]").innerText()).includes(question.question.why)
            && (await page.locator("body").innerText()).includes("Reply in your assistant"));
      await page.getByRole("button", { name: "Mark reviewed", exact: true }).click();
      await state(page, "working");

      const who = 'Joon <script>window.fixtureExecuted=true</script>';
      const paste = `Kind: connection\nChecked at: ${local.clock}\nPlaces checked: Fictional reports board | https://fictional.example.invalid/board\nPosts read: 19\nConnection: ${who} | https://fictional.example.invalid/post\nWhy now: A fictional field trial opens this week.\nThey bring: Low-power firmware experience.\nThey could gain: Field measurements.\nI bring: A fictional sensor and field test site.\nI could gain: A longer battery life.\nEvidence: https://fictional.example.invalid/post | A public request for field measurements.\nStill unknown: Their availability.\nSmall first step: Compare one power trace.\nBoard slug: fictional-joon`;
      await page.goto(base + "/app/reports/paste");
      await page.getByLabel("Report from your assistant", { exact: true }).fill(paste);
      await page.getByRole("button", { name: "Preview report", exact: true }).click();
      check(`${name}: paste shows an explicit unsaved preview`, (await page.locator("h1").innerText()) === "Review this report."
            && (await page.locator("body").innerText()).includes("Nothing has been saved."));
      const previewToken = await page.locator("input[name='preview_token']").inputValue();
      const previewCsrf = await page.locator("form[action='/app/reports/confirm'] input[name='csrf']").inputValue();
      const beforeConfirm = await owner.newPage();
      await beforeConfirm.goto(base + "/app");
      check(`${name}: preview has no saved effect before confirmation`, await beforeConfirm.locator(".report-history").count() === 2
            && !(await beforeConfirm.locator("body").innerText()).includes(who));
      await beforeConfirm.close();
      await page.getByRole("button", { name: "Confirm and save report", exact: true }).click();
      await state(page, "connection");
      await page.reload();
      const card = page.locator("[data-report-id]");
      check(`${name}: confirmed report survives a reload`, (await card.innerText()).includes(who));
      check(`${name}: HTML-shaped report content remains escaped text`, await card.locator("script").count() === 0
            && await page.evaluate(() => typeof window.fixtureExecuted === "undefined"));
      const links = await card.locator("a[href^='https:']").evaluateAll(elements => elements.map(el => el.rel.split(/\s+/)));
      check(`${name}: every report link has untrusted-content protections`, links.length > 0
            && links.every(rel => ["nofollow", "noopener", "ugc"].every(value => rel.includes(value))));
      const emptyInteractions = await owner.newPage();
      await emptyInteractions.goto(base + "/app/introductions");
      const noRequests = await emptyInteractions.locator("[data-introduction-id]").count() === 0;
      await emptyInteractions.goto(base + "/app/conversations");
      const unavailablePosting = await owner.request.get(base + "/app/introductions/new?slug=fictional-joon");
      check(`${name}: board reference requires a current posting and creates no contact automatically`,
            await card.getByRole("link", { name: "Ask for an introduction", exact: true }).getAttribute("href") === "/app/introductions/new?slug=fictional-joon"
            && unavailablePosting.status() === 404 && noRequests
            && await emptyInteractions.locator("[data-conversation-id]").count() === 0);
      await emptyInteractions.close();
      const confirmReplay = await owner.request.post(base + "/app/reports/confirm", {
        form: { csrf: previewCsrf, preview_token: previewToken }, headers: { Origin: base }, maxRedirects: 0,
      });
      check(`${name}: repeated confirmation returns the same saved outcome`, confirmReplay.status() === 303);
      await page.reload();
      check(`${name}: repeated confirmation creates no second card or history row`, await page.locator("[data-report-id]").count() === 1
            && await page.locator(".report-history").count() === 3);
      await page.getByRole("button", { name: "Mark reviewed", exact: true }).click();

      const noScript = await context({ javaScriptEnabled: false });
      const plain = await login(noScript, "joon");
      check(`${name}: another account cannot see the first owner's reports`, await plain.locator(".report-history").count() === 0);
      await createKey(plain, "Fictional manual assistant", false);
      await plain.goto(base + "/app/reports/paste");
      await plain.getByLabel("Report from your assistant", { exact: true }).fill(`Kind: question\nChecked at: ${local.clock}\nPlaces checked: none\nPosts read: 0\nQuestion: Should I check the fictional repair forum?\nWhy: It is outside the places agreed so far.`);
      await plain.getByRole("button", { name: "Preview report", exact: true }).click();
      check(`${name}: no-JavaScript paste still requires a preview`, (await plain.locator("h1").innerText()) === "Review this report.");
      await plain.getByRole("button", { name: "Confirm and save report", exact: true }).click();
      check(`${name}: no-JavaScript confirmation saves the owner's question`, (await plain.locator("[data-report-id]").innerText()).includes("fictional repair forum"));
      await plain.getByRole("button", { name: "Mark reviewed", exact: true }).click();
      await page.goto(base + "/app");
      check(`${name}: second owner's paste stays out of first owner's account`, await page.locator(".report-history").count() === 3
            && !(await page.locator("body").innerText()).includes("fictional repair forum"));

      await settings(page, true, "1440");
      await state(page, "paused");
      check(`${name}: pause describes only website intake`, (await page.locator("body").innerText()).includes("This does not pause your assistant or its searches."));
      const paused = await ingest(owner, issued.key, nothing, "fixture-paused");
      check(`${name}: paused intake refuses a new report`, paused.status() === 409 && (await paused.json()).error.includes("paused"));
      check(`${name}: paused intake still acknowledges an already-saved identical report`, (await ingest(owner, issued.key, nothing, "fixture-nothing")).status() === 201);
      await settings(page, false, "1440");
      await state(page, "working", false);
      check(`${name}: expected time is explicitly labelled as an expectation`, (await page.locator("body").innerText()).includes("Expected by")
            && (await page.locator("body").innerText()).includes("Your assistant keeps the real schedule"));
      const later = await local.advance(30 * 60 * 60 + 1);
      await page.reload();
      await state(page, "failed");
      check(`${name}: missed report does not claim the assistant failed to search`, (await page.locator("body").innerText()).includes("This website cannot tell whether it searched."));
      const recovery = await ingest(owner, issued.key, { ...nothing, checked_at: later }, "fixture-recovered");
      check(`${name}: new report is accepted after the missed expectation`, recovery.status() === 201);
      await page.reload();
      await state(page, "quiet", false);
      await page.goto(base + "/app/keys");
      const keyRow = page.locator(`[data-key-id='${issued.id}']`);
      check(`${name}: key shows real last-use metadata`, !(await keyRow.innerText()).includes("Not used yet"));
      await keyRow.getByRole("button", { name: "Revoke " + issued.name, exact: true }).click();
      check(`${name}: owner can revoke a key visibly`, (await page.locator(`[data-key-id='${issued.id}']`).innerText()).includes("Revoked"));
      check(`${name}: revoked key cannot submit new reports`, (await ingest(owner, issued.key, nothing, "fixture-revoked")).status() === 401);
      check(`${name}: revoked key cannot replay an earlier receipt`, (await ingest(owner, issued.key, nothing, "fixture-nothing")).status() === 401);
      check(`${name}: report journeys made no third-party requests`, forbiddenRequests.length === 0);
      check(`${name}: report pages produced no script errors`, pageErrors.length === 0);
      succeeded = true;
    } finally {
      // Do not save arbitrary failure pages: a failure could be on the private
      // one-time key page. Successful state screenshots contain fictional reports.
      for (const ctx of contexts) await ctx.close();
      const evidence = await local.stop();
      if (succeeded) {
        check(`${name}: every identity exchange used actual PKCE and RSA keys`, evidence.code_exchanges === 2
              && evidence.pkce_verified === 2 && evidence.jwks_reads > 0);
        check(`${name}: only five intended report effects were committed`, evidence.modes.reports.reports === 5
              && evidence.modes.reports.report_receipts === 5);
        check(`${name}: named keys have the expected lifecycle`, evidence.modes.reports.report_keys === 2
              && evidence.modes.reports.revoked_report_keys === 1);
        check(`${name}: raw report keys were never persisted`, evidence.modes.reports.cleartext_report_key_found === false);
      }
    }
  }
  writeFileSync(path.join(out, "verification.json"), JSON.stringify({ passed: true, fixture_only: true, real_google_tested: false, checks }, null, 2) + "\n");
  console.log(`report browser checks passed: ${checks.length}; fictional accounts, real local HTTP (evidence in ${out})`);
} finally {
  await browser.close();
}
