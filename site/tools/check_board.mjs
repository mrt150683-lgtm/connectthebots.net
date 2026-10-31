// Real two-account board journeys, using only fictional local OIDC identities.
// Public JSON responses are checked against docs/BOARD_SCHEMA.json by jsonschema.
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { spawn, spawnSync } from "node:child_process";
import path from "node:path";
import assert from "node:assert/strict";
import { isDeepStrictEqual } from "node:util";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const localPython = path.join(repository, ".venv", "Scripts", "python.exe");
const python = process.env.PYTHON || (process.platform === "win32" && existsSync(localPython) ? localPython : "python3");
const schemaPython = process.env.SCHEMA_PYTHON || python;
const outArg = process.argv.indexOf("--out");
const out = path.resolve(outArg > 0 ? process.argv[outArg + 1] : "artifacts/board-browser");
mkdirSync(out, { recursive: true });
const checks = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
function check(name, condition) { assert.ok(condition, name); checks.push({ check: name, passed: true }); }

const schemaReady = spawnSync(schemaPython, ["-c", "from jsonschema import Draft202012Validator"], { encoding: "utf8" });
assert.equal(schemaReady.status, 0, String(schemaReady.error || schemaReady.stderr || "Install development-only jsonschema==4.26.0 for board validation"));

async function fixture(name) {
  const directory = mkdtempSync(path.join(out, `fixture-${name}-`));
  const child = spawn(python, [path.join(repository, "tests_server", "oidc_fixture.py"), "--board", "--out", directory], {
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
    if (code === "timeout") { child.kill(); throw new Error("Board fixture did not stop"); }
    writeFileSync(path.join(directory, "process.log"), output);
    assert.equal(code, 0, output || "Board fixture failed");
    const evidence = JSON.parse(readFileSync(path.join(directory, "fixture-evidence.json"), "utf8"));
    writeFileSync(path.join(out, `fixture-evidence-${name}.json`), JSON.stringify(evidence, null, 2) + "\n");
    return evidence;
  }
  async function seed(count) {
    const id = "fictional-pagination-" + Date.now();
    const pending = path.join(directory, "board-seed-command.pending");
    writeFileSync(pending, JSON.stringify({ id, count }));
    renameSync(pending, path.join(directory, "board-seed-command.json"));
    for (let attempt = 0; attempt < 200; attempt++) {
      const ack = path.join(directory, "board-seed-ack.json");
      if (existsSync(ack) && JSON.parse(readFileSync(ack, "utf8")).id === id) return;
      if (spawnError || child.exitCode !== null) throw new Error(output || String(spawnError));
      await delay(100);
    }
    throw new Error("Fictional board records were not created");
  }
  for (let attempt = 0; attempt < 120; attempt++) {
    if (existsSync(path.join(directory, "ready.json"))) {
      return { ...JSON.parse(readFileSync(path.join(directory, "ready.json"), "utf8")), stop, seed };
    }
    if (spawnError || child.exitCode !== null) throw new Error(output || String(spawnError));
    await delay(250);
  }
  child.kill();
  throw new Error("Board fixture startup timed out: " + output);
}

const fields = value => ({ text: value.text, tags: value.tags, looking_for: value.looking_for, offering: value.offering });
const original = {
  text: "I build fictional soil sensors. I can offer field measurements at https://fictional.example.invalid/notes. I am looking for low-power firmware help.",
  tags: ["sensors", "firmware"], looking_for: "Low-power firmware help", offering: "Fictional field measurements",
};
const intermediate = { ...original, text: "I build fictional soil sensors. I can offer a new test field. I am looking for repair documentation.", tags: ["sensors", "repair"] };
const replacement = {
  text: "I build fictional sensors <script>window.boardExecuted=true</script>. I can offer a test bench. I am looking for repair collaborators.",
  tags: ["sensors", "repair", "fictional"], looking_for: "Repair collaborators", offering: "A fictional test bench",
};
const secondPosting = {
  text: "I repair fictional workshop tools. I can offer repair notes. I am looking for a documentation partner.",
  tags: ["repair", "documentation"], looking_for: "A documentation partner", offering: "Fictional repair notes",
};

const browser = await chromium.launch();
try {
  for (const [name, viewport] of [["desktop", { width: 1440, height: 900 }], ["mobile", { width: 390, height: 844 }]]) {
    const local = await fixture(name);
    const base = local.modes.board.url;
    const contexts = [], samples = [], forbidden = [], scriptErrors = [];
    async function context(options = {}) {
      const ctx = await browser.newContext({ viewport, ...options });
      contexts.push(ctx);
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
      await page.getByRole("link", { name: "Continue with Google", exact: true }).click();
      await page.waitForURL(local.provider_url + "/authorize?**");
      await page.getByLabel("Fictional person").selectOption(person);
      await page.getByRole("button", { name: "Use fictional account", exact: true }).click();
      await page.waitForURL(base + "/app");
      check(`${name}: ${person} signs in through the OIDC callback`, (await page.locator("body").innerText()).includes(person === "ama" ? "Ama Test" : "Joon Test"));
      return page;
    }
    async function getJSON(ctx, route = "/board.json") {
      const response = await ctx.request.get(base + route);
      assert.equal(response.status(), 200, "Public JSON request must succeed: " + route);
      const value = await response.json();
      samples.push(value);
      return value;
    }
    async function save(page, payload) {
      await page.goto(base + "/app/posting");
      await page.getByLabel("Public posting", { exact: true }).fill(payload.text);
      await page.getByLabel("Tags (comma separated, up to five)", { exact: true }).fill(payload.tags.join(", "));
      await page.getByLabel("Looking for", { exact: true }).fill(payload.looking_for);
      await page.getByLabel("Offering", { exact: true }).fill(payload.offering);
      await page.getByRole("button", { name: "Save draft", exact: true }).click();
      await page.waitForURL(base + "/app/posting");
    }
    async function preview(page, payload) {
      await page.getByRole("button", { name: "Preview publication", exact: true }).click();
      const card = page.locator("[data-publication-preview]");
      check(`${name}: publication preview shows the exact saved words`, await card.locator(".posting-text").innerText() === payload.text);
      check(`${name}: publication preview includes exact tags and both fields`,
            isDeepStrictEqual(await card.locator(".posting-tags li").allTextContents(), payload.tags)
            && isDeepStrictEqual(await card.locator("dd").allTextContents(), [payload.looking_for, payload.offering]));
      return {
        token: await page.locator("input[name='publication_token']").inputValue(),
        csrf: await page.locator("form[action='/app/posting/publish'] input[name='csrf']").inputValue(),
      };
    }
    async function fitted(page, label) {
      check(`${name}: ${label} fits the viewport`, await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    }
    let succeeded = false;
    try {
      const owner = await context();
      const other = await context({ javaScriptEnabled: false });
      const anonymous = await context({ javaScriptEnabled: false });
      const page = await login(owner, "ama");
      const plain = await login(other, "joon");
      const publicPage = await anonymous.newPage();
      const empty = await getJSON(anonymous);
      check(`${name}: board starts empty before anybody approves publication`, empty.total === 0 && empty.items.length === 0);
      await publicPage.goto(base + "/board");
      check(`${name}: anonymous visitor can read the honest empty board`, (await publicPage.locator("h1").innerText()) === "Public board."
            && (await publicPage.locator("body").innerText()).includes("No public postings yet."));
      await fitted(publicPage, "empty board");

      await save(page, original);
      check(`${name}: a saved private draft is absent from public JSON`, (await getJSON(anonymous)).total === 0);
      await publicPage.reload();
      check(`${name}: a saved private draft is absent from public HTML`, await publicPage.locator("[data-board-slug]").count() === 0);
      const firstPreview = await preview(page, original);
      check(`${name}: previewing alone does not publish`, (await getJSON(anonymous)).total === 0);
      await plain.goto(base + "/app/posting");
      check(`${name}: another owner cannot see the private draft`, await plain.getByLabel("Public posting", { exact: true }).inputValue() === "");
      const otherCsrf = await plain.locator("form[action='/app/posting'] input[name='csrf']").inputValue();
      const stolen = await other.request.post(base + "/app/posting/publish", {
        form: { csrf: otherCsrf, publication_token: firstPreview.token }, headers: { Origin: base }, maxRedirects: 0,
      });
      check(`${name}: another owner cannot publish a stolen approval token`, stolen.status() === 400 && (await getJSON(anonymous)).total === 0);
      const forgedOwner = await other.request.post(base + "/app/posting", {
        form: { csrf: otherCsrf, ...original, tags: original.tags.join(","), user_id: "fixture-ama" }, headers: { Origin: base }, maxRedirects: 0,
      });
      check(`${name}: owner supplied in a form cannot override session ownership`, forgedOwner.status() === 400);
      await page.getByRole("button", { name: "Publish", exact: true }).click();
      const firstFeed = await getJSON(anonymous);
      const published = firstFeed.items[0];
      check(`${name}: explicit approval publishes exactly the preview payload`, firstFeed.total === 1 && isDeepStrictEqual(fields(published), original));
      check(`${name}: publication advances the public update time`, firstFeed.updated_at > empty.updated_at);
      const slug = published.slug;
      check(`${name}: public permalink JSON matches the feed`, isDeepStrictEqual(await getJSON(anonymous, `/board/${slug}.json`), published));
      await publicPage.goto(base + "/board/" + slug);
      const publicCard = publicPage.locator(`[data-board-slug='${slug}']`);
      check(`${name}: anonymous HTML shows the exact approved text`, await publicCard.locator(".posting-text").innerText() === original.text);
      check(`${name}: typed URL is plain text and cannot become an executable link`, await publicCard.locator("a[href^='https:']").count() === 0
            && (await publicCard.innerText()).includes("https://fictional.example.invalid/notes"));
      check(`${name}: public page does not automatically expose Google profile details`,
            !/Ama Test|Joon Test|ama@example\.invalid|joon@example\.invalid|fixture-ama|fixture-joon/.test(await publicPage.content()));
      const emptyInteractions = await owner.newPage();
      await emptyInteractions.goto(base + "/app/introductions");
      const noRequests = await emptyInteractions.locator("[data-introduction-id]").count() === 0;
      await emptyInteractions.goto(base + "/app/conversations");
      check(`${name}: reading or publishing offers an explicit request without creating contact`,
            (await publicPage.locator("body").innerText()).includes("Publishing or reading this posting does not accept an introduction.")
            && await publicPage.getByRole("link", { name: "Ask for an introduction", exact: true }).getAttribute("href") === "/app/introductions/new?slug=" + slug
            && noRequests && await emptyInteractions.locator("[data-conversation-id]").count() === 0);
      await emptyInteractions.close();
      await fitted(publicPage, "public posting");
      await publicPage.screenshot({ path: path.join(out, `published-${name}.png`), fullPage: true });

      await save(page, intermediate);
      check(`${name}: editing a draft leaves every public field unchanged`, isDeepStrictEqual((await getJSON(anonymous)).items[0], published));
      await publicPage.reload();
      check(`${name}: public HTML keeps the last approved text during edits`, await publicCard.locator(".posting-text").innerText() === original.text);
      const stale = await preview(page, intermediate);
      const concurrentEdit = await owner.newPage();
      await save(concurrentEdit, replacement);
      await concurrentEdit.close();
      const refusedStale = await owner.request.post(base + "/app/posting/publish", {
        form: { csrf: stale.csrf, publication_token: stale.token }, headers: { Origin: base }, maxRedirects: 0,
      });
      check(`${name}: approval for an older draft is refused after an edit`, refusedStale.status() === 409);
      check(`${name}: refused stale approval cannot change the public posting`, isDeepStrictEqual((await getJSON(anonymous)).items[0], published));
      await page.goto(base + "/app/posting");
      const replacementPreview = await preview(page, replacement);
      await page.getByRole("button", { name: "Publish", exact: true }).click();
      const republished = (await getJSON(anonymous)).items[0];
      check(`${name}: fresh approval publishes the new payload with the stable slug`, republished.slug === slug && isDeepStrictEqual(fields(republished), replacement));
      check(`${name}: replacing public content records a newer publication time`, republished.updated_at > published.updated_at && republished.published_at > published.published_at);
      await publicPage.reload();
      check(`${name}: HTML-shaped posting text stays literal`, await publicCard.locator(".posting-text").innerText() === replacement.text
            && await publicCard.locator("script").count() === 0);
      // The public browser has JS disabled; also inspect an enabled browser so
      // an accidental executable script cannot hide behind that test setting.
      await page.goto(base + "/board/" + slug);
      check(`${name}: malicious-looking public text never executes with JavaScript enabled`, await page.locator("[data-board-slug] script").count() === 0
            && await page.evaluate(() => typeof window.boardExecuted === "undefined"));
      await fitted(page, "escaped public posting");
      await page.screenshot({ path: path.join(out, `escaped-posting-${name}.png`), fullPage: true });

      await save(plain, secondPosting);
      await preview(plain, secondPosting);
      await plain.getByRole("button", { name: "Publish", exact: true }).click();
      const two = await getJSON(anonymous);
      const second = two.items.find(item => item.slug !== slug);
      check(`${name}: no-JavaScript save, preview and publish works for a second owner`, two.total === 2 && isDeepStrictEqual(fields(second), secondPosting));
      check(`${name}: newest approved posting appears first`, two.items[0].slug === second.slug);
      await page.goto(base + "/app/posting");
      check(`${name}: second owner publishing leaves the first private draft unchanged`, await page.getByLabel("Public posting", { exact: true }).inputValue() === replacement.text);
      const beforeRemoval = two.updated_at;
      await page.getByRole("button", { name: "Unpublish posting", exact: true }).click();
      const removed = await getJSON(anonymous);
      check(`${name}: unpublish removes the posting from JSON immediately`, removed.total === 1 && removed.items.every(item => item.slug !== slug));
      check(`${name}: unpublish advances the public update time`, removed.updated_at > beforeRemoval);
      const oldApproval = await owner.request.post(base + "/app/posting/publish", {
        form: { csrf: replacementPreview.csrf, publication_token: replacementPreview.token }, headers: { Origin: base }, maxRedirects: 0,
      });
      check(`${name}: unpublish invalidates older approval forms`, oldApproval.status() === 409 && (await getJSON(anonymous)).total === 1);
      await publicPage.goto(base + "/board");
      check(`${name}: unpublish removes the posting from HTML immediately`, await publicPage.locator(`[data-board-slug='${slug}']`).count() === 0
            && await publicPage.locator(`[data-board-slug='${second.slug}']`).count() === 1);
      const gone = await publicPage.goto(base + "/board/" + slug);
      check(`${name}: unpublished permalink returns honest 410 HTML`, gone.status() === 410 && (await publicPage.locator("body").innerText()).includes("no longer public"));
      check(`${name}: unpublished JSON permalink returns 410`, (await anonymous.request.get(base + `/board/${slug}.json`)).status() === 410);
      check(`${name}: unknown posting never exposes a draft`, (await anonymous.request.get(base + "/board/ffffffffffffffffffffffffffffffff.json")).status() === 404);
      await publicPage.screenshot({ path: path.join(out, `unpublished-${name}.png`), fullPage: true });

      await local.seed(21);
      const firstPage = await getJSON(anonymous);
      const secondPage = await getJSON(anonymous, "/board.json?page=2");
      check(`${name}: public feed is paginated without overlap or omission`, firstPage.total === 22 && firstPage.items.length === 20
            && firstPage.has_next && secondPage.items.length === 2 && !secondPage.has_next
            && new Set([...firstPage.items, ...secondPage.items].map(item => item.slug)).size === 22);
      const allItems = [...firstPage.items, ...secondPage.items];
      check(`${name}: publication ordering is deterministic across pages`, allItems.every((item, index) => index === 0
            || allItems[index - 1].updated_at > item.updated_at
            || (allItems[index - 1].updated_at === item.updated_at && allItems[index - 1].slug.localeCompare(item.slug) <= 0)));
      const search = await getJSON(anonymous, "/board.json?q=ORCHARD");
      const searchNext = await getJSON(anonymous, "/board.json?q=ORCHARD&page=2");
      check(`${name}: search is case-insensitive and paginated`, search.total === 21 && search.items.length === 20 && search.has_next
            && searchNext.items.length === 1 && searchNext.items[0].tags.includes("orchard"));
      const literal = await getJSON(anonymous, "/board.json?q=%25");
      check(`${name}: search characters are literal rather than SQL wildcards`, literal.total === 0 && literal.items.length === 0);
      await publicPage.goto(base + "/board");
      await publicPage.getByLabel("Search postings", { exact: true }).fill("ORCHARD");
      await Promise.all([
        publicPage.waitForURL(url => url.pathname === "/board" && url.searchParams.get("q") === "ORCHARD"),
        publicPage.getByRole("button", { name: "Search", exact: true }).click(),
      ]);
      check(`${name}: no-JavaScript public search matches the JSON result`, await publicPage.locator("[data-board-slug]").count() === 20);
      await Promise.all([
        publicPage.waitForURL(url => url.searchParams.get("q") === "ORCHARD" && url.searchParams.get("page") === "2"),
        publicPage.getByRole("link", { name: "Next page", exact: true }).click(),
      ]);
      await publicPage.locator(`[data-board-slug="${searchNext.items[0].slug}"]`).waitFor();
      check(`${name}: next-page link preserves the search`, new URL(publicPage.url()).searchParams.get("q") === "ORCHARD"
            && new URL(publicPage.url()).searchParams.get("page") === "2" && await publicPage.locator("[data-board-slug]").count() === 1);
      await Promise.all([
        publicPage.waitForURL(url => url.searchParams.get("q") === "ORCHARD" && url.searchParams.get("page") === "1"),
        publicPage.getByRole("link", { name: "Previous page", exact: true }).click(),
      ]);
      await publicPage.locator(`[data-board-slug="${search.items[0].slug}"]`).waitFor();
      check(`${name}: previous-page link returns to the searched first page`, await publicPage.locator("[data-board-slug]").count() === 20);
      await fitted(publicPage, "search results");
      await publicPage.screenshot({ path: path.join(out, `board-search-${name}.png`), fullPage: true });
      check(`${name}: all public responses exclude automatic private identity metadata`,
            !/@example\.invalid|"email"|"display_name"|"google_sub"|"user_id"|"payload_hash"|"grant_id"/.test(JSON.stringify(samples)));
      check(`${name}: board journeys made no third-party requests`, forbidden.length === 0);
      check(`${name}: board pages produced no script errors`, scriptErrors.length === 0);

      const sampleFile = path.join(out, `public-json-samples-${name}.json`);
      const schemaEvidence = path.join(out, `schema-verification-${name}.json`);
      writeFileSync(sampleFile, JSON.stringify(samples, null, 2) + "\n");
      const validated = spawnSync(schemaPython, [path.join(repository, "tests_server/board_contract.py"), "--samples", sampleFile, "--out", schemaEvidence],
        { cwd: repository, encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
      assert.equal(validated.status, 0, String(validated.error || validated.stderr || validated.stdout || "Board schema validation failed"));
      const validation = JSON.parse(readFileSync(schemaEvidence, "utf8"));
      check(`${name}: actual public JSON validates against the committed schema with timestamps checked`, validation.passed
            && validation.samples_validated === samples.length && validation.date_time_format_checked);
      succeeded = true;
    } finally {
      for (const ctx of contexts) await ctx.close();
      const evidence = await local.stop();
      if (succeeded) {
        check(`${name}: both real browser identities used PKCE and RSA verification`, evidence.code_exchanges === 2
              && evidence.pkce_verified === 2 && evidence.jwks_reads > 0);
        check(`${name}: draft history is separate from exact publication grants`, evidence.modes.board.postings === 23
              && evidence.modes.board.revisions === 26 && evidence.modes.board.publication_grants === 24 && evidence.modes.board.revoked_grants === 2);
      }
    }
  }
  writeFileSync(path.join(out, "verification.json"), JSON.stringify({ passed: true, fixture_only: true, real_google_tested: false, checks }, null, 2) + "\n");
  console.log(`public board browser checks passed: ${checks.length}; fictional accounts, exact publication (evidence in ${out})`);
} finally { await browser.close(); }
