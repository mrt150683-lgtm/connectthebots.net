/** Exercise the real read-only viewer against an already-running runtime.
 * The parent verifier supplies a viewer key through the environment. Nothing
 * writes that key into evidence, URL parameters, logs or browser storage.
 */
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const { chromium } = await import(pathToFileURL(resolve(root, "workbench/ui/node_modules/playwright/index.mjs")));
const rawUrl = process.env.AGENTMATCH_VIEWER_URL;
const key = process.env.AGENTMATCH_VIEWER_TOKEN;
const canary = process.env.AGENTMATCH_VIEWER_CANARY;
const output = resolve(root, "artifacts/agent-runtime/screenshots");
let phase = "configuration";
let browser;

try {
  assert.ok(rawUrl && key, "Viewer URL and token are required through the environment.");
  const url = new URL(rawUrl);
  assert.ok(["http:", "https:"].includes(url.protocol) && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname));
  assert.equal(url.search, "");
  assert.equal(url.hash, "");
  assert.equal(url.username, "");
  assert.equal(url.password, "");
  assert.ok(!rawUrl.includes(key));
  mkdirSync(output, { recursive: true });
  phase = "browser startup";
  browser = await chromium.launch();
  const journeys = [];
  for (const [name, options] of [
    ["desktop", { viewport: { width: 1440, height: 1000 } }],
    ["mobile", { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 }],
  ]) {
    phase = `${name} connection`;
    const context = await browser.newContext(options);
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    let unsafeRequests = 0;
    let pageErrors = 0;
    page.on("pageerror", () => { pageErrors += 1; });
    page.on("request", (request) => {
      const requestUrl = new URL(request.url());
      if (requestUrl.origin !== url.origin || request.method() !== "GET" || request.url().includes(key)) unsafeRequests += 1;
    });
    await page.goto(rawUrl);
    await page.getByLabel("Viewer key", { exact: true }).fill(key);
    await page.getByRole("button", { name: "Connect viewer", exact: true }).click();
    await page.locator("#workspace-title").waitFor();
    await page.waitForFunction(() => document.activeElement?.id === "workspace-title");
    phase = `${name} read-only workspace`;
    await page.getByRole("heading", { name: "Current brief", exact: true }).waitFor();
    await page.getByRole("heading", { name: "Published notices", exact: true }).waitFor();
    await page.getByRole("heading", { name: "Introductions and first steps", exact: true }).waitFor();
    await page.getByRole("heading", { name: "Agent operation receipts", exact: true }).waitFor();
    assert.deepEqual(await page.getByRole("button").allTextContents(), ["Refresh view", "Disconnect", "Newest receipts", "Older receipts"]);
    assert.equal(await page.getByLabel("Viewer key", { exact: true }).count(), 0);
    assert.equal(await page.locator("select,textarea,input").count(), 0);
    assert.equal(await page.locator("img,iframe,object,embed").count(), 0);
    assert.ok(!page.url().includes(key));
    assert.ok(!(await page.content()).includes(key));
    assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
    if (canary) await page.getByText(canary, { exact: false }).first().waitFor();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth));
    await page.screenshot({ path: resolve(output, `${name}-viewer-viewport.png`), fullPage: false });
    await page.screenshot({ path: resolve(output, `${name}-viewer.png`), fullPage: true });
    phase = `${name} browser history cleanup`;
    // Navigate to an existing same-origin static resource, then use actual
    // browser history. Both a reload and a bfcache restoration must clear data.
    await page.goto(new URL("/style.css", url).href);
    await page.goBack();
    await page.getByLabel("Viewer key", { exact: true }).waitFor();
    assert.equal(await page.locator("#workspace-title").count(), 0);
    assert.equal(await page.getByLabel("Viewer key", { exact: true }).inputValue(), "");
    await page.getByLabel("Viewer key", { exact: true }).fill(key);
    await page.getByRole("button", { name: "Connect viewer", exact: true }).click();
    await page.locator("#workspace-title").waitFor();
    phase = `${name} refresh`;
    await page.getByRole("button", { name: "Refresh view", exact: true }).click();
    await page.getByRole("button", { name: "Refresh view", exact: true }).waitFor();
    await page.waitForFunction(() => document.querySelector("#refresh")?.disabled === false);
    assert.equal(await page.getByRole("alert").count(), 0);
    phase = `${name} disconnect`;
    await page.getByRole("button", { name: "Disconnect", exact: true }).click();
    await page.getByLabel("Viewer key", { exact: true }).waitFor();
    assert.equal(await page.getByLabel("Viewer key", { exact: true }).inputValue(), "");
    assert.equal(await page.locator("#workspace-title").count(), 0);
    assert.equal(await page.getByRole("heading", { name: "Current brief", exact: true }).count(), 0);
    assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
    assert.equal(unsafeRequests, 0);
    assert.equal(pageErrors, 0);
    journeys.push({ viewport: name, passed: true, only_get_same_origin: true, no_saved_key: true, disconnect_clears_workspace: true, history_clears_workspace: true, inert_markup: true });
    await context.close();
  }
  const evidence = { success: true, source: "real local agent runtime", journeys, screenshots: "artifacts/agent-runtime/screenshots" };
  writeFileSync(resolve(output, "viewer-verification.json"), JSON.stringify(evidence, null, 2) + "\n", "utf8");
  process.stdout.write(JSON.stringify(evidence) + "\n");
} catch {
  // Do not print Playwright call logs: an input call can include the key.
  process.stderr.write(`Viewer verification failed during ${phase}.\n`);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
}
