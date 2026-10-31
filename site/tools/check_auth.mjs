// Real-browser account journeys against a test-only local RSA OIDC provider.
// No Google account, SMTP server or production endpoint is contacted.
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import assert from "node:assert/strict";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const localPython = path.join(repository, ".venv", "Scripts", "python.exe");
const python = process.env.PYTHON || (process.platform === "win32" && existsSync(localPython) ? localPython : "python3");
const outArg = process.argv.indexOf("--out");
const out = path.resolve(outArg > 0 ? process.argv[outArg + 1] : "artifacts/auth-browser");
mkdirSync(out, { recursive: true });
const checks = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
function check(name, condition) {
  assert.ok(condition, name);
  checks.push({ check: name, passed: true });
}

async function fixture(name) {
  const directory = mkdtempSync(path.join(out, `fixture-${name}-`));
  const child = spawn(python, [path.join(repository, "tests_server", "oidc_fixture.py"), "--out", directory], {
    cwd: repository, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", PYTHONUNBUFFERED: "1" }, stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  let spawnError;
  child.stdout.on("data", data => { output += data.toString(); });
  child.stderr.on("data", data => { output += data.toString(); });
  const finished = new Promise(resolve => {
    child.once("error", error => { spawnError = error; resolve(-1); });
    child.once("exit", code => resolve(code));
  });
  async function stop() {
    writeFileSync(path.join(directory, "stop"), "stop\n");
    const code = await Promise.race([finished, delay(10000).then(() => "timeout")]);
    if (code === "timeout") { child.kill(); throw new Error("OIDC fixture did not stop"); }
    writeFileSync(path.join(directory, "process.log"), output);
    assert.equal(code, 0, output || "OIDC fixture failed");
    const evidence = JSON.parse(readFileSync(path.join(directory, "fixture-evidence.json"), "utf8"));
    writeFileSync(path.join(out, `fixture-evidence-${name}.json`), JSON.stringify(evidence, null, 2) + "\n");
    return evidence;
  }
  for (let attempt = 0; attempt < 120; attempt++) {
    if (existsSync(path.join(directory, "ready.json"))) {
      return { ...JSON.parse(readFileSync(path.join(directory, "ready.json"), "utf8")), stop };
    }
    if (spawnError || child.exitCode !== null) throw new Error(output || String(spawnError));
    await delay(250);
  }
  child.kill();
  throw new Error("OIDC fixture startup timed out: " + output);
}

const browser = await chromium.launch();
try {
  for (const [name, viewport] of [["desktop", { width: 1440, height: 900 }], ["mobile", { width: 390, height: 844 }]]) {
    const local = await fixture(name);
    const contexts = [];
    const forbiddenRequests = [];
    async function context(options = {}) {
      const ctx = await browser.newContext({ viewport, ...options });
      contexts.push(ctx);
      await ctx.route("**/*", route => {
        const host = new URL(route.request().url()).hostname;
        if (["localhost", "127.0.0.1", "[::1]"].includes(host)) return route.continue();
        forbiddenRequests.push(host);
        return route.abort();
      });
      return ctx;
    }
    async function login(ctx, mode, { person = "ama", scenario = "valid", invite, expectedError } = {}) {
      const page = await ctx.newPage();
      await page.goto(local.modes[mode].url + "/signin");
      if (invite) await page.getByLabel("Invite code (needed for a new account)").fill(invite);
      if (mode === "invite") {
        const [startRequest] = await Promise.all([
          page.waitForRequest(request => request.url() === local.modes[mode].url + "/auth/google/start"),
          page.getByRole("button", { name: "Continue with invite", exact: true }).click(),
        ]);
        assert.equal(await startRequest.headerValue("origin"), local.modes[mode].url, "native sign-in form keeps its same-origin header");
        await page.getByRole("link", { name: "Continue to Google", exact: true }).click();
      } else {
        await page.getByRole("link", { name: "Continue with Google", exact: true }).click();
      }
      await page.waitForURL(local.provider_url + "/authorize?**");
      await page.getByLabel("Fictional person").selectOption(person);
      await page.getByLabel("Token scenario").selectOption(scenario);
      const [callback] = await Promise.all([
        page.waitForResponse(response => response.url().startsWith(local.modes[mode].url + "/auth/google/callback")),
        page.getByRole("button", { name: "Use fictional account", exact: true }).click(),
      ]);
      if (expectedError) {
        check(`${name} ${mode}: ${expectedError}`, callback.status() === 400);
        check(`${name} ${mode}: refused sign-in creates no session`, !(await ctx.cookies()).some(cookie => cookie.name === "__Host-ctb_session"));
      } else {
        await page.waitForURL(local.modes[mode].url + "/app");
        check(`${name} ${mode}: real authorization-code callback completes`, callback.status() === 303);
        const displayName = person[0].toUpperCase() + person.slice(1)
          + (scenario === "escaped-name" ? ' <script>alert("fixture")</script> Test' : " Test");
        check(`${name} ${mode}: signed-in account belongs to chosen fictional person`,
          (await page.locator("h1").innerText()) === "Your dashboard."
          && (await page.locator("body").innerText()).includes(displayName));
      }
      return { page, callbackURL: callback.url() };
    }
    let journeySucceeded = false;
    try {
      const open = await context();
      const { page, callbackURL } = await login(open, "open");
      const session = (await open.cookies()).find(cookie => cookie.name === "__Host-ctb_session");
      check(`${name}: session cookie has required browser protections`, session && session.secure && session.httpOnly && session.sameSite === "Lax" && session.path === "/");
      check(`${name}: account page fits viewport`, await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
      await page.screenshot({ path: path.join(out, `account-${name}.png`), fullPage: true });
      check(`${name}: consumed OAuth state cannot be replayed`, (await open.request.get(callbackURL)).status() === 400);
      const csrf = await page.locator("form[action='/auth/logout'] input[name='csrf']").inputValue();
      const refused = await open.request.post(local.modes.open.url + "/auth/logout", { form: { csrf }, headers: { Origin: "https://untrusted.example" } });
      check(`${name}: cross-site logout is rejected`, refused.status() === 403);
      await page.getByRole("button", { name: "Sign out", exact: true }).click();
      await page.waitForURL(local.modes.open.url + "/signin");
      check(`${name}: logout clears the browser session`, !(await open.cookies()).some(cookie => cookie.name === "__Host-ctb_session"));
      const replay = await context();
      await replay.addCookies([session]);
      const replayPage = await replay.newPage();
      await replayPage.goto(local.modes.open.url + "/app");
      check(`${name}: copied session fails after logout`, new URL(replayPage.url()).pathname === "/signin");

      const rejected = await context();
      await login(rejected, "open", { scenario: "tampered-state", expectedError: "tampered state is refused" });

      const noScript = await context({ javaScriptEnabled: false });
      const noScriptLogin = await login(noScript, "open", { person: "joon" });
      await noScriptLogin.page.getByRole("button", { name: "Sign out", exact: true }).click();
      await noScriptLogin.page.waitForURL(local.modes.open.url + "/signin");
      check(`${name}: sign-in and logout work without JavaScript`, !(await noScript.cookies()).some(cookie => cookie.name === "__Host-ctb_session"));

      const invited = await context();
      const invitedLogin = await login(invited, "invite", { invite: local.modes.invite.invite });
      await invitedLogin.page.getByRole("button", { name: "Sign out", exact: true }).click();
      await invitedLogin.page.waitForURL(local.modes.invite.url + "/signin");
      const reusedInvite = await context();
      await login(reusedInvite, "invite", { person: "joon", invite: local.modes.invite.invite, expectedError: "single-use invite cannot create a second account" });

      const closed = await context({ javaScriptEnabled: false });
      const waitlist = await closed.newPage();
      await waitlist.goto(local.modes.closed.url + "/signin");
      await waitlist.getByLabel("Email address", { exact: true }).fill(`fictional-${name}@example.invalid`);
      await waitlist.locator("input[name='consent']").check();
      await waitlist.screenshot({ path: path.join(out, `waitlist-${name}.png`), fullPage: true });
      await waitlist.getByRole("button", { name: "Join the waitlist", exact: true }).click();
      check(`${name}: explicit waitlist consent is accepted without JavaScript`, (await waitlist.locator("h1").innerText()) === "Request received.");
      const removal = await waitlist.getByLabel("Removal code", { exact: true }).inputValue();
      await waitlist.getByRole("link", { name: "Remove an email from the waitlist" }).click();
      await waitlist.getByLabel("Removal code", { exact: true }).fill(removal);
      await waitlist.getByRole("button", { name: "Remove my email", exact: true }).click();
      check(`${name}: waitlist removal works without JavaScript`, (await waitlist.locator("h1").innerText()) === "Removal complete.");
      const closedSignup = await context();
      await login(closedSignup, "closed", { expectedError: "closed signups refuse a new Google identity" });

      const ownData = await context();
      const dataLogin = await login(ownData, "data", { scenario: "escaped-name" });
      check(`${name}: identity display name is escaped text`, await dataLogin.page.locator("script").count() === 0
            && (await dataLogin.page.locator("body").innerText()).includes('<script>alert("fixture")</script>'));
      await dataLogin.page.getByRole("link", { name: "Your data", exact: true }).click();
      await dataLogin.page.screenshot({ path: path.join(out, `account-data-${name}.png`), fullPage: true });
      const [download] = await Promise.all([
        dataLogin.page.waitForEvent("download"),
        dataLogin.page.getByRole("button", { name: "Download my data", exact: true }).click(),
      ]);
      const exportPath = path.join(out, `fictional-export-${name}.json`);
      await download.saveAs(exportPath);
      const exported = JSON.parse(readFileSync(exportPath, "utf8"));
      const dataSession = (await ownData.cookies()).find(cookie => cookie.name === "__Host-ctb_session");
      check(`${name}: export downloads only the signed-in fictional account`, exported.profile.email === "ama@example.invalid"
            && !JSON.stringify(exported).includes(dataSession.value) && exported.messages.length === 0);
      check(`${name}: account export excludes an anonymous waitlist request with the same email`,
            !Object.hasOwn(exported, "waitlist") && !JSON.stringify(exported).includes("Independent anonymous fixture consent"));
      await dataLogin.page.getByLabel("Type DELETE to confirm", { exact: true }).fill("delete");
      const [notConfirmed] = await Promise.all([
        dataLogin.page.waitForResponse(response => response.url().endsWith("/app/account/delete")),
        dataLogin.page.getByRole("button", { name: "Delete my account", exact: true }).click(),
      ]);
      check(`${name}: deletion requires the exact confirmation`, notConfirmed.status() === 422);
      await dataLogin.page.goto(local.modes.data.url + "/app/account");
      await dataLogin.page.getByLabel("Type DELETE to confirm", { exact: true }).fill("DELETE");
      await dataLogin.page.getByRole("button", { name: "Delete my account", exact: true }).click();
      check(`${name}: confirmed deletion completes`, (await dataLogin.page.locator("h1").innerText()) === "Your account is deleted.");
      const deletedSession = await context();
      await deletedSession.addCookies([dataSession]);
      const deletedPage = await deletedSession.newPage();
      await deletedPage.goto(local.modes.data.url + "/app");
      check(`${name}: deleted account rejects its previous session`, new URL(deletedPage.url()).pathname === "/signin");
      check(`${name}: browser made no third-party requests`, forbiddenRequests.length === 0);
      journeySucceeded = true;
    } finally {
      if (!journeySucceeded) {
        for (let index = 0; index < contexts.length; index++) {
          const pages = contexts[index].pages();
          if (pages.length) await pages.at(-1).screenshot({ path: path.join(out, `failure-${name}-${index}.png`), fullPage: true }).catch(() => {});
        }
      }
      for (const ctx of contexts) await ctx.close();
      const evidence = await local.stop();
      if (journeySucceeded) {
        check(`${name}: every token exchange verified real PKCE`, evidence.code_exchanges > 0 && evidence.code_exchanges === evidence.pkce_verified);
        check(`${name}: application fetched RSA signing keys`, evidence.jwks_reads >= 3);
        check(`${name}: closed signup and waitlist removal leave no account or email`, evidence.modes.closed.users === 0 && evidence.modes.closed.waitlist === 0);
        check(`${name}: one invite created exactly one account`, evidence.modes.invite.users === 1);
        check(`${name}: account deletion removes identity and notifications with a date-only receipt`, evidence.modes.data.users === 0
              && evidence.modes.data.email_outbox === 0 && evidence.modes.data.deletion_receipts === 1);
        check(`${name}: account deletion preserves separate anonymous waitlist authority`, evidence.modes.data.waitlist === 1);
        check(`${name}: logout revoked all successful sessions`, Object.values(evidence.modes).every(mode => mode.sessions === 0));
      }
    }
  }
  writeFileSync(path.join(out, "verification.json"), JSON.stringify({ passed: true, fixture_only: true, real_google_tested: false, checks }, null, 2) + "\n");
  console.log(`account browser checks passed: ${checks.length}; synthetic OIDC, real RSA/PKCE/HTTP (evidence in ${out})`);
} finally {
  await browser.close();
}
