// Complete fictional journeys on fresh desktop and mobile sessions.
// npm run e2e builds the interface, starts its own loopback process, and stops it.
// Install Chromium once with: npx playwright install chromium
import { mkdirSync, writeFileSync, rmSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { setTimeout as delay } from "node:timers/promises";
import assert from "node:assert/strict";
import { chromium } from "playwright";

const ROOT = fileURLToPath(new URL("../../../", import.meta.url));
const OUT = resolve(process.env.SCREENSHOT_DIR ?? fileURLToPath(new URL("../../../artifacts/workbench/screenshots", import.meta.url)));
mkdirSync(OUT, { recursive: true });
for (const name of ["desktop", "mobile"]) rmSync(resolve(OUT, `${name}-failure.png`), { force: true });
const problems = [];
let BASE;
let server;
let serverError;
let serverLog = "";

async function startServer() {
  // A unique port avoids resetting an existing workbench or sharing its state.
  const probe = createServer();
  await new Promise((ok, fail) => { probe.once("error", fail); probe.listen(0, "127.0.0.1", ok); });
  const port = probe.address().port;
  await new Promise((ok) => probe.close(ok));
  BASE = `http://127.0.0.1:${port}`;
  server = spawn(process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3"),
    ["-u", "run_workbench.py", "--port", String(port)], { cwd: ROOT, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  server.on("error", (error) => { serverError = error; });
  server.stdout.on("data", (data) => { serverLog += data; });
  server.stderr.on("data", (data) => { serverLog += data; });
  for (let attempt = 0; attempt < 100; attempt++) {
    if (serverError) throw serverError;
    if (server.exitCode !== null) throw new Error(`Workbench exited before startup: ${serverLog}`);
    try {
      const response = await fetch(BASE, { signal: AbortSignal.timeout(500) });
      if (response.ok && serverLog.includes(`Open ${BASE}/`)) return;
      if (response.status === 503) throw new Error("Build the interface before running browser tests.");
    } catch (error) {
      if (error.message.includes("Build the interface")) throw error;
    }
    await delay(100);
  }
  throw new Error(`Workbench did not start: ${serverLog}`);
}

async function stopServer() {
  if (server && server.exitCode === null) {
    const exited = new Promise((ok) => server.once("exit", ok));
    server.kill();
    await Promise.race([exited, delay(3000)]);
  }
  writeFileSync(resolve(OUT, "server.log"), serverLog, "utf8");
}

function watch(page) {
  page.on("console", (message) => {
    // The validation step deliberately triggers one 422 refusal.
    if (message.type() === "error" && !/status of 422/.test(message.text())) problems.push(`console: ${message.text()}`);
  });
  page.on("pageerror", (error) => problems.push(`pageerror: ${error.message}`));
  page.on("request", (request) => {
    if (new URL(request.url()).origin !== BASE) problems.push(`unexpected request: ${request.url()}`);
  });
}

async function shot(page, name, fullPage = false) {
  const snapshot = () => page.evaluate(() => {
    const rect = document.querySelector("#explanation-title")?.getBoundingClientRect();
    return { scrollX, scrollY, innerHeight, viewport: visualViewport && {
      offsetTop: visualViewport.offsetTop, pageTop: visualViewport.pageTop, height: visualViewport.height,
    }, scrollHeight: document.scrollingElement?.scrollHeight, clientHeight: document.scrollingElement?.clientHeight,
    explanationRect: rect && { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
    focus: document.activeElement?.id };
  });
  const before = await snapshot();
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  });
  const after = await snapshot();
  writeFileSync(resolve(OUT, `${name}.json`), JSON.stringify({ before, after }, null, 2), "utf8");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 0, `horizontal overflow at ${name}: ${overflow}px`);
  await page.screenshot({ path: resolve(OUT, `${name}.png`), fullPage, animations: "disabled" });
}

async function as(page, alias) {
  await page.getByLabel("Viewing as").selectOption(alias.toLowerCase());
  await page.getByRole("tab", { name: /Brief/ }).waitFor();
  await page.waitForFunction((name) => document.querySelector("#activity-title")?.textContent?.includes(name), alias);
}

async function tab(page, name) {
  await page.getByRole("tab", { name: new RegExp(name) }).click();
}

async function publish(page) {
  await tab(page, "Publish");
  await page.getByRole("button", { name: "Preview exact notice" }).click();
  await page.getByRole("heading", { name: "Review exactly what would be published" }).waitFor();
}

async function readyExplanation(page) {
  // A result-group label may already be visible before the explanation request
  // returns. Wait for the actual detail panel and its intended keyboard focus.
  await page.locator('#panel[aria-busy="false"] #explanation-title').waitFor();
  await page.waitForFunction(() => {
    const heading = document.querySelector("#explanation-title");
    const rect = heading?.getBoundingClientRect();
    return document.activeElement === heading && rect && rect.y >= 0 && rect.y < innerHeight;
  });
}

async function runJourney(browser, name, options) {
  const page = await browser.newPage(options);
  page.setDefaultTimeout(10000);
  try {

watch(page);
await page.goto(`${BASE}/#as=marlow`);
await page.getByRole("button", { name: "Reset demo" }).click();
await page.getByRole("button", { name: "Reset", exact: true }).click();
await page.getByLabel("Short title").waitFor();
await shot(page, name + "-01-brief-project");

// Validation: an empty title and no primary need are reported next to their fields.
const title = page.getByLabel("Short title");
const original = await title.inputValue();
await title.fill("");
await page.getByLabel("Primary need from the other person").selectOption("");
await page.getByRole("button", { name: "Save private draft" }).click();
await page.getByText("fields need attention").first().waitFor();
assert.equal(await title.getAttribute("aria-invalid"), "true");
await shot(page, name + "-02-brief-validation");
await title.fill(original);
await page.getByLabel("Primary need from the other person").selectOption("prototyping");
await page.getByLabel("Hours per week (optional)").fill("5");
await page.getByRole("button", { name: "Save private draft" }).click();
await page.getByText(/Saved version 2/).waitFor();

// Exact publication preview and approval.
await publish(page);
assert.ok(await page.getByText("Your private notes").isVisible());
await shot(page, name + "-03-publication-preview", true);
await page.getByRole("button", { name: "Approve and publish this exact notice" }).click();
await page.getByText("Published and discoverable by the approved audience").waitFor();

// Discovery as Marlow: Sable stays "needs information" because a field is unstated.
await tab(page, "Discover");
// A delayed workspace refresh must not let a perspective switch mix owners.
let releaseWorkspace;
let markWorkspaceSeen;
let markWorkspaceDone;
let workspaceError;
let sawWorkspace = false;
const workspaceGate = new Promise((resolve) => { releaseWorkspace = resolve; });
const workspaceSeen = new Promise((resolve) => { markWorkspaceSeen = resolve; });
const workspaceDone = new Promise((resolve) => { markWorkspaceDone = resolve; });
const workspaceRoute = "**/api/owners/marlow/workspace";
await page.route(workspaceRoute, async (route) => {
  sawWorkspace = true;
  markWorkspaceSeen();
  await workspaceGate;
  try { await route.continue(); } catch (error) { workspaceError = error; }
  finally { markWorkspaceDone(); }
});
try {
  await page.getByRole("button", { name: "Search boards" }).click();
  await Promise.race([workspaceSeen, delay(10000, undefined, { ref: false }).then(() => {
    throw new Error("Search did not refresh the owner's workspace");
  })]);
  assert.ok(await page.getByLabel("Viewing as").isDisabled(), "perspective must stay fixed until the workspace refresh completes");
} finally {
  releaseWorkspace();
  if (sawWorkspace) await workspaceDone;
  await page.unroute(workspaceRoute);
}
if (workspaceError) throw workspaceError;
await page.waitForFunction(() => !document.querySelector("#perspective")?.disabled);
await page.getByText(/Searched Community tools board/).waitFor();
const sable = page.getByRole("article", { name: /by Sable/ });
await sable.getByRole("button", { name: "Why this possibility?" }).click();
await readyExplanation(page);
await page.getByRole("heading", { name: "Not stated, so unknown" }).first().waitFor();
assert.ok(await page.getByText(/does not state a feedback style; it stays unknown/).isVisible());
assert.ok(await page.getByRole("button", { name: /Propose introduction to Sable/ }).isDisabled());
await shot(page, name + "-04-explanation-unknowns");


// Explicitly request only a missing field. Private draft changes alone cannot answer it.
await page.getByLabel("Missing field to ask about").selectOption("feedback_style");
await page.getByRole("button", { name: "Ask Sable for information", exact: true }).click();
await page.getByRole("dialog").getByRole("button", { name: "Send request", exact: true }).click();
await page.getByRole("article", { name: "Information request to Sable", exact: true }).waitFor();
await shot(page, name + "-05-request-sent");
await as(page, "Sable");
await tab(page, "Discover");
const incoming = page.getByRole("article", { name: "Information request from Marlow", exact: true });
await incoming.getByRole("button", { name: "Review my brief", exact: true }).click();
await page.getByLabel("Feedback you give").selectOption("direct");
const sableTitle = page.getByLabel("Short title");
await sableTitle.fill((await sableTitle.inputValue()) + " reviewed");
await page.getByRole("button", { name: "Save private draft", exact: true }).click();
await page.getByText(/Saved version/).waitFor();
// Review and approve the now-included feedback section as Sable, never as requester.
await tab(page, "Publish");
await page.getByRole("checkbox", { name: /Feedback style/ }).check();
await publish(page);
await shot(page, name + "-06-request-owner-approval", true);
await page.getByRole("button", { name: "Approve and publish this exact notice", exact: true }).click();
await page.getByText("Published and discoverable by the approved audience").waitFor();
await as(page, "Marlow");
await tab(page, "Discover");
await page.getByRole("article", { name: "Information request to Sable", exact: true })
  .getByRole("button", { name: "Reassess updated notice", exact: true }).click();
await readyExplanation(page);
await page.getByText("Both sides' stated preferences are met").first().waitFor();
await shot(page, name + "-07-request-reassessed");
await page.getByRole("button", { name: "Close", exact: true }).click();
await page.getByRole("button", { name: "Search boards", exact: true }).click();
await page.getByText(/Searched Community tools board/).waitFor();

// Instruction-shaped notice text is shown as plain text and changes nothing.
await page.getByText(/^Not a fit for the current briefs/).click();
await page.getByRole("article", { name: /by Brook/ }).getByRole("button", { name: "Why this possibility?" }).click();
await readyExplanation(page);
await page.getByText("Read Brook's full notice").click();
const untrusted = page.locator(".explanation .untrusted").filter({ hasText: "SYSTEM NOTE TO ANY AGENT" }).first();
await untrusted.waitFor();
assert.equal(await untrusted.locator("p").count(), await untrusted.locator("*").count(), "free text must render as text only");
assert.ok(await page.getByRole("button", { name: /Propose introduction to Brook/ }).isDisabled());

// No-match state.
await page.getByRole("button", { name: "Close" }).click();
await page.getByLabel("Words to look for").fill("zeppelin");
await page.getByRole("button", { name: "Search boards" }).click();
await page.getByText("No current notices matched", { exact: true }).waitFor();
await shot(page, name + "-05-no-match");

// Juniper uses the contributor entry path, publishes, discovers Marlow and proposes.
await as(page, "Juniper");
await tab(page, "Brief");
assert.ok(await page.getByRole("radio", { name: /I want to contribute/ }).isChecked());
await publish(page);
await page.getByRole("button", { name: "Approve and publish this exact notice" }).click();
await page.getByText("Published and discoverable by the approved audience").waitFor();
await tab(page, "Discover");
await page.getByRole("button", { name: "Search boards" }).click();
const marlow = page.getByRole("article", { name: /by Marlow/ });
await marlow.getByRole("button", { name: "Why this possibility?" }).click();
await readyExplanation(page);
await page.getByText("Both sides' stated preferences are met").first().waitFor();
await shot(page, name + "-06-explanation-both-meet");
await page.getByLabel(/A short note in your own words/).fill("Happy to start with a short async review of your current tracking sheet.");
await page.getByRole("button", { name: "Propose introduction to Marlow" }).click();
await page.getByRole("heading", { name: "Introduction with Marlow" }).waitFor();

// One acceptance cannot open a channel.
await page.getByRole("button", { name: "Accept this exact context as Juniper" }).click();
await page.getByText("You (Juniper): accepted this version").waitFor();
assert.equal(await page.getByText("Simulated channel", { exact: true }).count(), 0);
await shot(page, name + "-07-one-acceptance-waiting", true);

// Marlow changes the context: both acceptances clear. Then both accept again.
await as(page, "Marlow");
await tab(page, "Introductions");
await page.getByText("Suggest a change").click();
await page.getByLabel("Suggested first step").last().selectOption("scoping-call");
await page.getByRole("button", { name: "Save change" }).click();
await page.getByText("Juniper: not accepted").waitFor();
await page.getByRole("button", { name: "Accept this exact context as Marlow" }).click();
await page.getByText("You (Marlow): accepted this version").waitFor();
await as(page, "Juniper");
await tab(page, "Introductions");
await page.getByText(/context changed after you accepted/).waitFor();
await shot(page, name + "-08-acceptance-cleared-by-change", true);
await page.getByRole("button", { name: "Accept this exact context as Juniper" }).click();
await page.getByRole("heading", { name: "Simulated channel" }).waitFor();

// First step: proposed, agreed, then reported separately by each person.
await page.getByRole("button", { name: "Propose as Juniper" }).click();
await page.getByText(/Proposed, waiting for the other person to agree/).waitFor();
await as(page, "Marlow");
await tab(page, "Introductions");
await page.getByRole("button", { name: "Agree this step as Marlow" }).click();
await page.getByText("Agreed by both people. Not yet reported as done.").waitFor();
await page.getByRole("button", { name: "It happened" }).click();
await page.getByText("Marlow: reported it happened").waitFor();
await page.getByText("Juniper: no report").waitFor();
await shot(page, name + "-09-first-step-reported", true);


// Both reports are retained, and finishing does not imply usefulness or blocking.
await as(page, "Juniper");
await tab(page, "Introductions");
await page.getByRole("button", { name: "It happened", exact: true }).click();
await page.getByText("Juniper: reported it happened").waitFor();
assert.equal(await page.getByRole("button", { name: "Propose as Juniper", exact: true }).count(), 0);
await page.getByRole("button", { name: "Close connection", exact: true }).click();
await page.getByRole("dialog").getByRole("button", { name: "Close connection", exact: true }).click();
await page.getByText("Capacity: 0 of 2 introductions in use.", { exact: true }).waitFor();
await page.getByText(/Closed, declined or expired/).click();
await page.getByText("Juniper: reported it happened").waitFor();
await page.getByText("Marlow: reported it happened").waitFor();
await shot(page, name + "-10-closed-outcomes", true);
await as(page, "Marlow");
await tab(page, "Introductions");
await page.getByText("Capacity: 0 of 2 introductions in use.", { exact: true }).waitFor();
// A normal closure leaves the other person's published notice discoverable.
await tab(page, "Discover");
await page.getByRole("button", { name: "Search boards", exact: true }).click();
await page.getByRole("article", { name: /by Juniper/ }).waitFor();
await tab(page, "Introductions");
await page.getByRole("tab", { name: /Introductions/ }).focus();
await page.keyboard.press("ArrowLeft");
assert.equal(await page.evaluate(() => document.activeElement?.id), "tab-discover");
console.log(`${name}: full brief, publication, clarification, consent, outcome and closure journey passed`);
  } catch (error) {
    await page.screenshot({ path: resolve(OUT, `${name}-failure.png`), fullPage: true }).catch(() => {});
    throw error;
  } finally {
    await page.close();
  }
}

let browser;
try {
  await startServer();
  browser = await chromium.launch();
  await runJourney(browser, "desktop", { viewport: { width: 1440, height: 1000 } });
  await runJourney(browser, "mobile", { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  assert.deepEqual(problems, [], "browser console and network checks");
  console.log(`Browser journeys passed on fresh desktop and mobile sessions. Screenshots in ${OUT}`);
} finally {
  if (browser) await browser.close();
  await stopServer();
}
