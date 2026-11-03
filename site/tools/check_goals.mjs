// Real loopback OIDC, agent API and native owner forms; fictional accounts only.
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import assert from "node:assert/strict";

const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const localPython = path.resolve(repository, "../../app/.venv/Scripts/python.exe");
const python = process.env.PYTHON || (process.platform === "win32" && existsSync(localPython) ? localPython : "python3");
const outIndex = process.argv.indexOf("--out");
const out = path.resolve(outIndex > 0 ? process.argv[outIndex + 1] : "artifacts/goals-browser");
mkdirSync(out, { recursive: true });
const checks = [], snapshots = [], layouts = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
function check(name, value) { assert.ok(value, name); checks.push({ check: name, passed: true }); }
async function navigate(page, button) {
  await Promise.all([page.waitForEvent("framenavigated", frame => frame === page.mainFrame()), button.click()]);
  await page.waitForLoadState("domcontentloaded");
}
async function bounded(promise, milliseconds, label) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(label)), milliseconds);
    })]);
  } finally { clearTimeout(timer); }
}
async function visualReady(page) {
  await page.waitForLoadState("load", { timeout: 15000 });
  await bounded(page.evaluate(async () => { await document.fonts.ready; }), 5000, "Local page fonts did not become ready");
}

async function fixture(name) {
  const directory = mkdtempSync(path.join(out, `fixture-${name}-`));
  const child = spawn(python, [path.join(repository, "tests_server/goal_browser_fixture.py"), "--out", directory], {
    cwd: repository, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", PYTHONUNBUFFERED: "1" },
    windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "", spawnError;
  child.stdout.on("data", bytes => { output += bytes.toString(); });
  child.stderr.on("data", bytes => { output += bytes.toString(); });
  const finished = new Promise(resolve => {
    child.once("error", error => { spawnError = error; resolve(-1); });
    child.once("exit", code => resolve(code));
  });
  async function command(action, extra = {}) {
    const id = `${action}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    writeFileSync(path.join(directory, "goals-command.pending"), JSON.stringify({ id, action, ...extra }));
    renameSync(path.join(directory, "goals-command.pending"), path.join(directory, "goals-command.json"));
    for (let attempt = 0; attempt < (action === "operator" ? 600 : 300); attempt++) {
      const ack = path.join(directory, "goals-ack.json");
      if (existsSync(ack)) {
        try {
          const result = JSON.parse(readFileSync(ack, "utf8"));
          if (result.id === id) {
            unlinkSync(ack); // Cleartext fictional agent keys are never saved as evidence.
            if (result.error) throw new Error(result.error);
            return result;
          }
        } catch (error) {
          if (!(error instanceof SyntaxError) && !["EACCES", "EPERM", "EBUSY", "ENOENT"].includes(error.code)) throw error;
        }
      }
      if (spawnError || child.exitCode !== null) throw new Error(output || String(spawnError));
      await delay(100);
    }
    throw new Error("Fictional goal command timed out");
  }
  async function stop() {
    for (const filename of ["goals-ack.json", "goals-ack.pending"]) {
      const candidate = path.join(directory, filename); if (existsSync(candidate)) unlinkSync(candidate);
    }
    writeFileSync(path.join(directory, "stop"), "stop\n");
    const code = await Promise.race([finished, delay(10000).then(() => "timeout")]);
    writeFileSync(path.join(directory, "process.log"), output);
    if (code === "timeout") { child.kill(); throw new Error("Goal fixture did not stop"); }
    assert.equal(code, 0, output || "Goal fixture failed");
    return JSON.parse(readFileSync(path.join(directory, "fixture-evidence.json"), "utf8"));
  }
  for (let attempt = 0; attempt < 120; attempt++) {
    const ready = path.join(directory, "ready.json");
    if (existsSync(ready)) return { ...JSON.parse(readFileSync(ready, "utf8")), command, stop };
    if (spawnError || child.exitCode !== null) throw new Error(output || String(spawnError));
    await delay(250);
  }
  child.kill(); throw new Error("Goal fixture startup timed out");
}

const browser = await chromium.launch();
try {
  for (const [name, viewport] of [["desktop", { width: 1440, height: 900 }], ["mobile", { width: 390, height: 844 }]]) {
    const local = await fixture(name), base = local.url;
    const contexts = [], forbidden = [], errors = [];
    let visualPage;
    const verify = (label, value) => check(`${name}: ${label}`, value);
    const take = async label => {
      const result = (await local.command("snapshot")).snapshot;
      snapshots.push({ mode: name, label, ...result });
      verify(`${label} preserves foreign-key integrity`, result.foreign_keys_valid);
      return result;
    };
    async function context(options = {}) {
      const ctx = await browser.newContext({ viewport, ...options }); contexts.push(ctx);
      ctx.on("page", page => page.on("pageerror", error => errors.push(error.message)));
      await ctx.route("**/*", route => {
        const host = new URL(route.request().url()).hostname;
        if (["localhost", "127.0.0.1", "[::1]"].includes(host)) return route.continue();
        forbidden.push(host); return route.abort();
      });
      return ctx;
    }
    async function login(ctx, person) {
      const page = await ctx.newPage();
      visualPage = page;
      const signin = await page.goto(base + "/signin");
      verify(`${person} sign-in page is available`, signin.status() === 200);
      await navigate(page, page.getByRole("link", { name: "Continue with Google", exact: true }));
      await page.waitForURL(local.provider_url + "/authorize?**");
      await page.getByLabel("Fictional person").selectOption(person);
      await navigate(page, page.getByRole("button", { name: "Use fictional account", exact: true }));
      await page.waitForURL(base + "/app");
      verify(`${person} completes local RSA/PKCE sign-in`, (await page.locator("body").innerText()).includes(person[0].toUpperCase() + person.slice(1) + " Test"));
      return page;
    }
    async function visibleGoalControls(page, label) {
      visualPage = page;
      await visualReady(page);
      const layout = await page.evaluate(() => {
        const inspect = selector => {
          const node = document.querySelector(selector);
          if (!node) return { visible: false, missing: true };
          const rect = node.getBoundingClientRect();
          const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
          const hit = document.elementFromPoint(x, y);
          return { top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right,
            visible: rect.top >= 0 && rect.bottom <= innerHeight && rect.left >= 0 && rect.right <= innerWidth &&
              (hit === node || node.contains(hit)),
            covering: hit?.className || hit?.tagName || null };
        };
        return { heading: inspect("#common-goals-heading"), stop: inspect("[data-goal-stop-all] button"),
          anchorTop: document.querySelector("#common-goals").getBoundingClientRect().top,
          scrollMarginTop: getComputedStyle(document.querySelector("#common-goals")).scrollMarginTop,
          sticky: [...document.querySelectorAll(".site-head,.report-nav")].map(node => ({
            class: node.className, top: node.getBoundingClientRect().top, bottom: node.getBoundingClientRect().bottom,
            position: getComputedStyle(node).position })) };
      });
      layouts.push({ mode: name, label, ...layout });
      writeFileSync(path.join(out, `layout-${name}.json`), JSON.stringify(layouts.filter(row => row.mode === name), null, 2) + "\n");
      verify(`${label}: Common goals heading is fully visible and unoccluded`, layout.heading.visible);
      verify(`${label}: Stop all is fully visible and clickable`, layout.stop.visible);
    }
    async function dashboard(page) {
      visualPage = page;
      await page.goto(base + "/app", { waitUntil: "load" });
      await page.reload({ waitUntil: "load" });
      await visualReady(page);
      // Use the same native fragment scrolling as the real form redirects;
      // scrolling/screenshotting an oversized section can otherwise hide its
      // header under sticky navigation and conceal an actual anchor defect.
      await page.goto(base + "/app#common-goals", { waitUntil: "load" });
      await visibleGoalControls(page, "native dashboard anchor");
    }
    const formData = locator => locator.evaluate(form => Object.fromEntries(new FormData(form)));
    const post = (ctx, route, data, origin = base) => ctx.request.post(base + route, { form: data, headers: { Origin: origin }, maxRedirects: 0 });
    const allocation = (state, person, slug) => state.allocations.find(row => row.person === person && row.slug === slug);
    const session = (state, id) => state.sessions.find(row => row.id === id);
    let succeeded = false;
    try {
      const ama = await context(name === "mobile" ? { javaScriptEnabled: false, reducedMotion: "reduce" } : {});
      const joon = await context({ javaScriptEnabled: false, reducedMotion: "reduce" });
      const reader = await context({ javaScriptEnabled: false, reducedMotion: "reduce" });
      const publicPage = await reader.newPage();
      let priya;
      const a = await login(ama, "ama"), j = await login(joon, "joon");
      const seeded = await local.command("seed"), [first, second] = seeded.slugs;
      let serial = 0;
      async function api(person, method, route, body, expected = 200) {
        const response = await (person === "ama" ? ama : person === "joon" ? joon : priya).request.fetch(base + route, {
          method, headers: { Authorization: "Bearer " + seeded.keys[person], Origin: base,
            ...(body === undefined ? {} : { "Content-Type": "application/json", "Idempotency-Key": `goals-${name}-${++serial}` }) },
          ...(body === undefined ? {} : { data: body }), maxRedirects: 0,
        });
        assert.equal(response.status(), expected, `${method} ${route}: ${await response.text()}`);
        return response.json();
      }
      async function permit(person, slug, minutes, showName = false) {
        return api(person, "PUT", `/api/v1/goals/${slug}/allocation`, {
          sessions_per_week: 3, minutes_per_session: minutes, until: seeded.until,
          show_first_name: showName, charter_revision: 1, notice_version: seeded.notice_version, approved_by_owner: true,
          ...(person === "ama" ? { lead_notice_version: seeded.lead_notice_version, lead_approved_by_owner: true } : {}),
        });
      }
      async function preview(page, slug, minutes, showName = false) {
        visualPage = page;
        await dashboard(page);
        const card = page.locator(`[data-goal-slug='${slug}']`), details = card.locator("details.goal-change");
        await details.locator("summary").focus(); await page.keyboard.press("Enter");
        verify("Change time opens with the keyboard", await details.getAttribute("open") !== null);
        const form = details.locator("[data-goal-change]");
        await form.locator("input[name='minutes_per_session']").fill(String(minutes));
        await form.locator("input[name='show_first_name']").setChecked(showName);
        const button = form.getByRole("button", { name: "Review time allowance", exact: true });
        await button.focus(); verify("time review button accepts keyboard focus", await button.evaluate(node => node === document.activeElement));
        await navigate(page, button);
        // DOMContentLoaded can precede stylesheets when JavaScript is disabled.
        // Judge the rendered page, retaining the same strict overflow check.
        await visualReady(page);
        const quote = page.locator("[data-goal-contribution]");
        verify("native preview displays the exact proposed minute limit", (await quote.innerText()).includes(`for up to ${minutes} minutes each`));
        verify("permission preview fits the viewport", await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        const choices = await page.locator(".goal-choice").evaluateAll(labels => labels.map(label => {
          const input = label.querySelector("input"), box = input.getBoundingClientRect();
          const text = [...label.childNodes].find(node => node.nodeType === Node.TEXT_NODE && node.textContent.trim());
          if (!text) return false;
          const range = document.createRange(); range.selectNodeContents(text);
          const line = [...range.getClientRects()].find(rect => rect.width > 0 && rect.height > 0);
          return !!line && box.right <= line.left && box.top < line.bottom && box.bottom > line.top;
        }));
        verify("approval checkboxes sit beside the first line of their labels", choices.length > 0 && choices.every(Boolean));
        return formData(page.locator("[data-goal-confirm]"));
      }
      async function confirm(page) {
        await page.locator("input[name='approve']").check();
        if (await page.locator("input[name='approve_lead']").count()) await page.locator("input[name='approve_lead']").check();
        await navigate(page, page.getByRole("button", { name: "Confirm time allowance", exact: true }));
        await page.waitForURL(base + "/app#common-goals");
        await visibleGoalControls(page, "native confirmation redirect");
      }
      async function publicView(route, banner = null, page = publicPage) {
        visualPage = page;
        const response = await page.goto(base + route, { waitUntil: "load" });
        await visualReady(page);
        verify(`public page ${route} is readable without a work permission`, response.status() === 200 && await page.locator("body.common-goals-public").count() === 1);
        verify(`public page ${route} fits the viewport`, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        const text = await page.locator("body").innerText();
        verify(`public page ${route} makes no ranking or live-work claim`, !/(top contributors|contribution ranking|agents? working now|live work progress)/i.test(text));
        if (banner) {
          const notices = await page.locator("[data-goal-banner]").allTextContents();
          verify(`public page ${route} repeats exact T4`, notices.length > 0 && notices.every(value => value.trim() === banner));
        }
        return text;
      }
      async function redactedPage(slug, entry, needles, label, banner = null) {
        for (const [audience, page] of [["anonymous reader", publicPage], ["signed-in author/lead", a]]) {
          const text = await publicView(`/goals/${slug}`, banner, page);
          verify(`${label}: ${audience} retains the finding envelope`, await page.locator(`[data-goal-entry='${entry.id}']`).count() === 1 && (await page.locator(`[data-goal-entry='${entry.id}']`).innerText()).includes("content is not publicly available"));
          verify(`${label}: ${audience} cannot read unavailable or dependent prose`, needles.every(needle => !text.includes(needle)) && !text.includes("Fictional private M4 reporter canary"));
        }
      }
      async function checkin(person, label) {
        const before = (await local.command("snapshot")).snapshot;
        const result = await api(person, "POST", "/api/v1/agent/checkin", {});
        const after = (await local.command("snapshot")).snapshot;
        verify(`${label}: check-in preserves the existing response fields`, ["checked_in_at", "interview", "matches", "boards", "ranks", "referrals", "campaign", "sharing_suggestions", "texts_version"].every(key => key in result));
        verify(`${label}: check-in creates no work or permission changes`, before.work_digest === after.work_digest && before.sessions.length === after.sessions.length);
        return result;
      }
      async function changedFinding(entry, expectedStatus, expectedPublic, label) {
        const result = await checkin("ama", label), item = result.goals.your_findings.find(row => row.entry_id === entry.id);
        verify(`${label}: current own finding status is delivered`, item?.finding_status === expectedStatus && item.public === expectedPublic && !["content", "summary", "detail", "sources"].some(key => key in item));
        verify(`${label}: sensitive change repeats exact T4`, result.goals.banner === safety.banner && item.banner === safety.banner);
        const again = await checkin("ama", label + " repeated at frozen clock");
        verify(`${label}: the same status is delivered once at a frozen clock`, result.checked_in_at === again.checked_in_at && !again.goals.your_findings.some(row => row.entry_id === entry.id));
      }

      await permit("ama", first, 20, true); await permit("ama", second, 15);
      await permit("joon", first, 20); await permit("joon", second, 15);
      const due = await checkin("ama", "allocated idle owner");
      verify("check-in gives due links without starting work", due.goals.enabled && [first, second].every(slug => due.goals.due.some(row => row.goal === slug && row.start === `POST /api/v1/goals/${slug}/sessions` && row.sessions_left === 3)));
      verify("recent goals do not invent lead reminders", due.goals.lead.length === 0);
      const originalAma = (await api("ama", "POST", `/api/v1/goals/${first}/sessions`, {}, 201)).session_id;
      const originalJoon = (await api("joon", "POST", `/api/v1/goals/${first}/sessions`, {}, 201)).session_id;
      const taskContext = await api("ama", "GET", `/api/v1/goals/sessions/${originalAma}`);
      const finding = (await api("ama", "POST", `/api/v1/goals/sessions/${originalAma}/findings`, {
        task_id: taskContext.task.id, kind: "proof_step", summary: "Fictional proof: successive odd counts differ by two.",
        detail: "Subtract 2n+1 from 2(n+1)+1 and simplify.", method: "Expand parentheses and cancel matching terms.",
        sources: [], confidence: "tentative", unknowns: [], builds_on: [],
      }, 201)).entry;
      verify("findings are marked as written by an assistant", finding.content.written_by === "assistant");
      await api("joon", "POST", `/api/v1/goals/sessions/${originalJoon}/reviews`, {
        finding_id: finding.id, verdict: "supports", note: "Fictional independent check: expanding the expression gives a difference of two.", checked: ["proof", "method"],
      }, 201);
      const secondFinding = (await api("ama", "POST", `/api/v1/goals/sessions/${originalAma}/findings`, {
        task_id: taskContext.task.id, kind: "proof_step", summary: "Fictional second proof: adding zero preserves the count.",
        detail: "For every integer n, n plus zero is n.", method: "Apply the additive identity to the same integer.",
        sources: [], confidence: "tentative", unknowns: [], builds_on: [],
      }, 201)).entry;
      const summaryText = "Fictional public summary: the checked difference is two.";
      await api("ama", "POST", `/api/v1/goals/${first}/summary`, {
        session_id: originalAma, text: summaryText, finding_ids: [finding.id], disputed: [],
      }, 201);
      const occupied = await checkin("ama", "owner with a live session");
      verify("a current work window suppresses due-start reminders", occupied.goals.due.length === 0);
      let publicText = await publicView(`/goals/${first}`);
      verify("public standard page shows aim, scope, tasks, latest summary and checked findings", ["The aim", "In scope", "Open tasks", summaryText, finding.content.summary, "Checked", "Led by Ama"].every(text => publicText.includes(text)));
      await publicPage.screenshot({ path: path.join(out, `goal-public-standard-${name}.png`), fullPage: true });
      await publicView(`/goals/${first}?limit=1`);
      verify("finding pagination begins with one retained envelope", await publicPage.locator("[data-goal-entry]").count() === 1 && await publicPage.locator(`[data-goal-entry='${finding.id}']`).count() === 1);
      await navigate(publicPage, publicPage.getByRole("link", { name: "More findings", exact: true })); await visualReady(publicPage);
      verify("finding pagination reaches the next entry without duplication", await publicPage.locator("[data-goal-entry]").count() === 1 && await publicPage.locator(`[data-goal-entry='${secondFinding.id}']`).count() === 1 && await publicPage.getByRole("link", { name: "First findings", exact: true }).count() === 1);
      await publicView("/goals?category=mathematics&limit=1");
      const firstCard = await publicPage.locator("[data-goals-list] [data-goal-slug]").getAttribute("data-goal-slug");
      verify("public directory supports category and bounded pages", await publicPage.locator("[data-goals-list] [data-goal-slug]").count() === 1 && await publicPage.getByRole("link", { name: "More goals", exact: true }).count() === 1);
      await navigate(publicPage, publicPage.getByRole("link", { name: "More goals", exact: true })); await visualReady(publicPage);
      verify("directory pagination preserves the category and reaches a different goal", new URL(publicPage.url()).searchParams.get("category") === "mathematics" && await publicPage.locator("[data-goals-list] [data-goal-slug]").count() === 1 && await publicPage.locator("[data-goals-list] [data-goal-slug]").getAttribute("data-goal-slug") !== firstCard);
      async function publicRecord() {
        const response = await ama.request.get(base + `/goals/${first}/record.jsonl`);
        verify("public record download is available", response.status() === 200);
        return (await response.text()).trim().split("\n").filter(Boolean).map(line => JSON.parse(line));
      }
      let publicEntries = await publicRecord();
      verify("an independent supporting check changes the public finding label", publicEntries.find(row => row.id === finding.id).finding_status === "checked");
      const heads = await (await ama.request.get(base + "/goals/heads.json")).json();
      verify("public heads identify the latest full record envelope", heads.goals.some(row => row.slug === first && row.seq === publicEntries.at(-1).seq && row.entry_sha256 === publicEntries.at(-1).entry_sha256));
      const withdrawalPath = `/app/goals/entries/${finding.id}/withdraw`;
      verify("another owner cannot preview a withdrawal", (await joon.request.get(base + withdrawalPath)).status() === 404);
      await a.goto(base + withdrawalPath, { waitUntil: "load" }); await visualReady(a); visualPage = a;
      verify("native withdrawal preview identifies the exact authored content", (await a.locator(".goal-entry-preview").innerText()).includes("successive odd counts"));
      verify("withdrawal explains retained hashes and outside copies", (await a.locator("body").innerText()).includes("cannot be recalled") && (await a.locator("body").innerText()).includes("hashes remain"));
      verify("withdrawal preview fits the viewport", await a.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await a.screenshot({ path: path.join(out, `goal-withdraw-${name}.png`), fullPage: true });
      await a.locator("input[name='confirm']").check();
      await navigate(a, a.getByRole("button", { name: "Withdraw this entry", exact: true }));
      await visibleGoalControls(a, "native withdrawal redirect");
      let withdrawalState = await take("native contribution withdrawal");
      const erased = withdrawalState.entries.find(row => row.id === finding.id);
      verify("independent SQL confirms withdrawn content is erased with hashes intact", !erased.content_present && erased.status === "withdrawn" && erased.entry_sha256 === finding.entry_sha256 && erased.content_sha256 === finding.content_sha256);
      publicEntries = await publicRecord();
      const tombstone = publicEntries.find(row => row.id === finding.id);
      verify("public download preserves the erased envelope without its content", tombstone.content === null && tombstone.entry_sha256 === finding.entry_sha256);
      await redactedPage(first, finding, [finding.content.summary, summaryText], "withdrawn standard source and dependent summary");
      verify("public download has no private account or session links", publicEntries.every(row => !["author_user_id", "user_id", "session_id"].some(key => key in row)));
      verify("withdrawal leaves both owners' work windows unchanged", session(withdrawalState, originalAma).end_reason === null && session(withdrawalState, originalJoon).end_reason === null);
      verify("opted-in lead name is shown", (await api("joon", "GET", `/api/v1/goals/${first}`)).goal.lead.first_name === "Ama");
      await dashboard(a);
      verify("dashboard names both fictional goals and current usage", await a.locator("[data-goal-slug]").count() === 2 && (await a.locator(`[data-goal-slug='${first}'] [data-goal-usage]`).innerText()).includes("1 of 3 sessions"));
      verify("dashboard fits the viewport", await a.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await a.screenshot({ path: path.join(out, `goals-${name}.png`) });
      const initialPreview = await preview(a, first, 10, true);
      await a.screenshot({ path: path.join(out, `goal-confirm-${name}.png`), fullPage: true });
      await confirm(a);
      let state = await take("lowered allowance");
      verify("lowering time ends the old lease and retains its full reservation", allocation(state, "ama", first).minutes_per_session === 10 && session(state, originalAma).end_reason === "allocation_replaced" && session(state, originalAma).reserved_minutes === 20);
      verify("lowering one allowance leaves the other owner's work unchanged", allocation(state, "joon", first).minutes_per_session === 20 && session(state, originalJoon).end_reason === null);
      await api("ama", "POST", `/api/v1/goals/sessions/${originalAma}/end`, { minutes_used: 1, did: "Fictional late completion." }, 409);
      const currentAma = (await api("ama", "POST", `/api/v1/goals/${first}/sessions`, {}, 201)).session_id;

      await dashboard(a); await dashboard(j);
      const ownStop = await formData(a.locator(`[data-goal-slug='${first}'] [data-goal-stop]`));
      const joonStop = await formData(j.locator(`[data-goal-slug='${first}'] [data-goal-stop]`));
      verify("stop refuses an invalid CSRF token", (await post(ama, `/app/goals/${first}/stop`, { ...ownStop, csrf: "invalid" })).status() === 403);
      verify("stop refuses a cross-origin form", (await post(ama, `/app/goals/${first}/stop`, ownStop, "https://other.example.invalid")).status() === 403);
      verify("a form cannot select another owner", (await post(ama, `/app/goals/${first}/stop`, { ...ownStop, user_id: seeded.owners.joon })).status() === 400);
      verify("signed preview is bound to its owner", (await post(joon, `/app/goals/${first}/allocation/confirm`, { ...initialPreview, csrf: joonStop.csrf, approve: "yes", approve_lead: "yes" })).status() === 400);
      const tampered = initialPreview.token.slice(0, -1) + (initialPreview.token.endsWith("0") ? "1" : "0");
      verify("tampered preview is refused", (await post(ama, `/app/goals/${first}/allocation/confirm`, { ...initialPreview, token: tampered, approve: "yes", approve_lead: "yes" })).status() === 400);
      state = await take("rejected owner form attempts");
      verify("rejected forms preserve both owners' active sessions", session(state, currentAma).end_reason === null && session(state, originalJoon).end_reason === null);

      const stale = await preview(a, first, 15);
      const stopPage = await ama.newPage(); await dashboard(stopPage);
      await navigate(stopPage, stopPage.locator(`[data-goal-slug='${first}'] [data-goal-stop] button`));
      verify("a stale preview cannot restart a stopped grant", (await post(ama, `/app/goals/${first}/allocation/confirm`, { ...stale, approve: "yes", approve_lead: "yes" })).status() === 409);
      state = await take("one goal stopped");
      verify("Stop goal immediately ends the live lease", !allocation(state, "ama", first).active && session(state, currentAma).end_reason === "owner_stop");
      verify("stopping clears the per-goal name choice", state.members.find(row => row.person === "ama" && row.slug === first).show_first_name === 0 && !(await api("joon", "GET", `/api/v1/goals/${first}`)).goal.lead.first_name);
      publicText = await publicView(`/goals/${first}`);
      verify("public name opt-out updates current pages", publicText.includes("Led by a member") && !publicText.includes("Led by Ama") && !/\bAma\b/.test(publicText));
      await api("ama", "POST", `/api/v1/goals/${first}/sessions`, {}, 409);

      const expired = await preview(a, first, 10);
      await local.command("advance", { seconds: 601 });
      verify("ten-minute owner previews expire", (await post(ama, `/app/goals/${first}/allocation/confirm`, { ...expired, approve: "yes", approve_lead: "yes" })).status() === 400);
      await preview(j, second, 10); await confirm(j);
      state = await take("no-JavaScript allowance confirmation");
      verify("no-JavaScript owner can review and confirm time", allocation(state, "joon", second).minutes_per_session === 10 && allocation(state, "joon", second).active);
      verify("changing a second goal preserves the first goal's lease", session(state, originalJoon).end_reason === null);

      const secondAma = (await api("ama", "POST", `/api/v1/goals/${second}/sessions`, {}, 201)).session_id;
      await local.command("feature_off");
      await dashboard(stopPage);
      verify("disabled feature retains owner stop controls", await stopPage.locator("[data-goal-stop-all]").count() === 1 && await stopPage.locator("[data-goal-stop]").count() === 2 && await stopPage.locator("[data-goal-change]").count() === 0);
      await navigate(stopPage, stopPage.locator("[data-goal-stop-all] button"));
      state = await take("feature-off owner stop all");
      verify("Stop all ends every owner allowance and current session", state.allocations.filter(row => row.person === "ama").every(row => !row.active) && session(state, secondAma).end_reason === "stop_all");
      verify("Stop all cannot stop another owner's allocations", state.allocations.filter(row => row.person === "joon").every(row => row.active) && session(state, originalJoon).end_reason === null);
      await api("ama", "POST", `/api/v1/goals/${second}/sessions`, {}, 404);
      await dashboard(j);
      await navigate(j, j.locator("[data-goal-stop-all] button"));
      state = await take("no-JavaScript feature-off stop all");
      verify("no-JavaScript stop all works with the feature off", state.allocations.every(row => !row.active) && session(state, originalJoon).end_reason === "stop_all");
      verify("stopped dashboard fits the viewport", await j.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await visibleGoalControls(j, "native Stop all redirect");
      await j.screenshot({ path: path.join(out, `goals-stopped-${name}.png`) });

      // M4 extends the completed ordinary journey; its limits and operator
      // controls exist only in this disposable fictional fixture.
      const safety = await local.command("seed_safety");
      Object.assign(seeded.keys, safety.keys); seeded.until = safety.until;
      const sensitive = safety.slug;
      const sensitiveNotice = (label, result) => verify(`${label} repeats the exact approved sensitive notice`, result.banner === safety.banner);
      sensitiveNotice("sensitive detail", (await api("joon", "GET", `/api/v1/goals/${sensitive}`)).goal);
      sensitiveNotice("sensitive eligibility", await api("joon", "GET", `/api/v1/goals/${sensitive}/eligibility`));
      sensitiveNotice("sensitive lead allowance", await permit("ama", sensitive, 20));
      sensitiveNotice("sensitive contributor allowance", await permit("joon", sensitive, 20));
      await preview(a, sensitive, 20);
      verify("native sensitive time preview repeats exact T4", (await a.locator("body").innerText()).includes(safety.banner));
      const safeAma = await api("ama", "POST", `/api/v1/goals/${sensitive}/sessions`, {}, 201);
      const safeJoon = await api("joon", "POST", `/api/v1/goals/${sensitive}/sessions`, {}, 201);
      sensitiveNotice("sensitive lead start", safeAma); sensitiveNotice("sensitive contributor start", safeJoon);
      const dose = (await api("ama", "POST", `/api/v1/goals/sessions/${safeAma.session_id}/findings`, {
        task_id: safeAma.task.id, kind: "result", summary: "The cited fictional paper reports a dose of 5 mg/kg.",
        detail: "The fictional report describes a group observation and its limitations.",
        method: "Read the published methods and compare the stated sample count.",
        sources: [{ url: "https://papers.example.test/fictional-study", title: "A fictional published study",
          quote: "The study enrolled 40 dogs.", accessed: safety.accessed }],
        confidence: "tentative", unknowns: [], builds_on: [],
      }, 201));
      sensitiveNotice("held finding response", dose);
      verify("a dose description is accepted into a safety hold", dose.entry.status === "held" && dose.entry.safety.message === safety.hold_notice);
      state = await take("M4 safety-held finding");
      verify("independent SQL retains held cleartext and its original hashes", state.entries.some(row => row.id === dose.entry.id && row.status === "held" && row.content_present && row.entry_sha256 === dose.entry.entry_sha256));
      let reviewContext = await api("joon", "GET", `/api/v1/goals/sessions/${safeJoon.session_id}`);
      sensitiveNotice("sensitive review context", reviewContext);
      verify("a safety hold is absent from the independent review offers", !reviewContext.reviews.some(row => row.id === dose.entry.id) && !JSON.stringify(reviewContext).includes("5 mg/kg"));
      async function safetyRecord() {
        const response = await ama.request.get(base + `/goals/${sensitive}/record.jsonl`);
        verify("sensitive JSONL has the exact notice header", response.status() === 200 && response.headers()["x-goal-banner"] === safety.banner);
        const text = await response.text();
        verify("public record contains no private reporter canary or identity fields", !text.includes("Fictional private M4 reporter canary") && !text.includes('"reporter_id"') && !text.includes('"details"'));
        const rows = text.trim().split("\n").filter(Boolean).map(line => JSON.parse(line));
        verify("each sensitive JSONL envelope repeats exact T4", rows.length > 0 && rows.every(row => row.banner === safety.banner));
        return rows;
      }
      let safetyEntries = await safetyRecord();
      verify("held content is null in public JSONL", safetyEntries.find(row => row.id === dose.entry.id).content === null);
      await redactedPage(sensitive, dose.entry, ["5 mg/kg", "The fictional report describes"], "safety-held sensitive finding", safety.banner);
      await changedFinding(dose.entry, "held", false, "safety-held own finding");
      const peerRecord = await api("joon", "GET", `/api/v1/goals/${sensitive}/record`);
      sensitiveNotice("sensitive agent record", peerRecord);
      verify("ordinary peer record does not disclose held content", peerRecord.entries.find(row => row.id === dose.entry.id).content === null);
      const sensitiveHeads = await (await ama.request.get(base + "/goals/heads.json")).json();
      verify("sensitive record head repeats exact T4", sensitiveHeads.goals.find(row => row.slug === sensitive).banner === safety.banner);
      const heldWithdrawal = `/app/goals/entries/${dose.entry.id}/withdraw`;
      await a.goto(base + heldWithdrawal, { waitUntil: "load" }); await visualReady(a); visualPage = a;
      const heldPage = await a.locator("body").innerText();
      verify("held author preview contains exact T4 and the fixed hold notice", heldPage.includes(safety.banner) && heldPage.includes(safety.hold_notice));
      verify("held author can review the exact target for withdrawal", (await a.locator(".goal-entry-preview").innerText()).includes("5 mg/kg"));
      verify("held author page fits the viewport", await a.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await a.screenshot({ path: path.join(out, `goal-held-withdraw-${name}.png`), fullPage: true });
      const released = await local.command("operator", { operator_action: "release", entry_id: dose.entry.id });
      verify("the local operator CLI releases the safety hold", released.operator.success);
      state = await take("M4 operator release");
      verify("operator CLI uses the advanced frozen fixture clock", state.entries.filter(row => row.slug === sensitive).at(-1).created_at === state.clock);
      verify("release restores pending peer status while retaining hashes", state.entries.some(row => row.id === dose.entry.id && row.status === "pending_peer_check" && row.content_present && row.entry_sha256 === dose.entry.entry_sha256));
      safetyEntries = await safetyRecord();
      verify("operator release alone does not publish the pending finding", safetyEntries.find(row => row.id === dose.entry.id).content === null);
      await redactedPage(sensitive, dose.entry, ["5 mg/kg", "The fictional report describes"], "released pending sensitive finding", safety.banner);
      await changedFinding(dose.entry, "pending_peer_check", false, "released pending own finding");
      reviewContext = await api("joon", "GET", `/api/v1/goals/sessions/${safeJoon.session_id}`);
      verify("eligible active reviewer receives the released pending finding", reviewContext.reviews.some(row => row.id === dose.entry.id && row.content?.summary.includes("5 mg/kg")));
      const checkedDose = await api("joon", "POST", `/api/v1/goals/sessions/${safeJoon.session_id}/reviews`, {
        finding_id: dose.entry.id, verdict: "supports", note: "Compared the fictional published sample count and method.", checked: ["sources", "method"],
      }, 201);
      sensitiveNotice("independent sensitive review", checkedDose);
      safetyEntries = await safetyRecord();
      verify("independent support makes released content checked and public", safetyEntries.find(row => row.id === dose.entry.id).finding_status === "checked" && safetyEntries.find(row => row.id === dose.entry.id).content !== null);
      const sensitiveSummary = "Fictional derived summary of the published sample comparison.";
      const taskQuestion = "Fictional derived task: compare the recorded sample description with the checked source.";
      await api("ama", "POST", `/api/v1/goals/${sensitive}/summary`, {
        session_id: safeAma.session_id, text: sensitiveSummary, finding_ids: [dose.entry.id], disputed: [],
      }, 201);
      const revisedTask = Object.fromEntries(["kind", "title", "question", "deliverable", "done_when", "capabilities"].map(key => [key, safeAma.task[key]]));
      revisedTask.question = taskQuestion;
      await api("ama", "PATCH", `/api/v1/goals/${sensitive}/tasks/${safeAma.task.id}`, {
        session_id: safeAma.session_id, task: revisedTask, reason: "Use the checked fictional comparison to refine the question.", finding_ids: [dose.entry.id],
      });
      publicText = await publicView(`/goals/${sensitive}`, safety.banner);
      verify("checked sensitive page shows source title, quotation, date, summary, task and derived status", ["A fictional published study", "The study enrolled 40 dogs.", safety.accessed, sensitiveSummary, taskQuestion, "Checked", "5 mg/kg"].every(text => publicText.includes(text)));
      verify("the cited source link preserves the submitted HTTPS URL without fetching it", await publicPage.getByRole("link", { name: "A fictional published study", exact: true }).getAttribute("href") === "https://papers.example.test/fictional-study");
      const methodToggle = publicPage.locator(`[data-goal-entry='${dose.entry.id}'] details summary`);
      await methodToggle.focus();
      verify("public method disclosure accepts keyboard focus", await methodToggle.evaluate(node => node === document.activeElement));
      await publicPage.keyboard.press("Enter");
      verify("the public source method opens without JavaScript", await publicPage.locator(`[data-goal-entry='${dose.entry.id}'] details`).getAttribute("open") !== null);
      await publicPage.screenshot({ path: path.join(out, `goal-public-sensitive-method-${name}.png`) });
      await publicPage.evaluate(() => window.scrollTo(0, 0));
      await publicPage.screenshot({ path: path.join(out, `goal-public-sensitive-${name}.png`), fullPage: true });
      await changedFinding(dose.entry, "checked", true, "independently checked own finding");
      const reportBody = { entry_id: dose.entry.id, reason: "misinformation", details: "Fictional private M4 reporter canary." };
      sensitiveNotice("private concern report", await api("joon", "POST", "/api/v1/goals/report", reportBody, 201));
      state = await take("M4 reported finding hidden");
      verify("fixture threshold hides reversibly without changing the envelope", state.entries.some(row => row.id === dose.entry.id && row.status === "hidden" && row.content_present && row.entry_sha256 === dose.entry.entry_sha256 && row.content_sha256 === dose.entry.content_sha256));
      safetyEntries = await safetyRecord();
      verify("report hiding removes public finding and dependent review content", safetyEntries.find(row => row.id === dose.entry.id).content === null && safetyEntries.find(row => row.id === checkedDose.entry.id).content === null);
      await redactedPage(sensitive, dose.entry, ["5 mg/kg", sensitiveSummary, taskQuestion], "hidden sensitive source and dependent projections", safety.banner);
      await changedFinding(dose.entry, "hidden", false, "moderated own finding");
      const unhidden = await local.command("operator", { operator_action: "unhide", entry_id: dose.entry.id });
      verify("the local operator CLI reverses the moderation hide", unhidden.operator.success);
      state = await take("M4 operator unhide");
      const reviewedReports = state.reports.filter(row => row.entry_id === dose.entry.id);
      verify("unhide restores pending state and marks the old report reviewed", state.entries.find(row => row.id === dose.entry.id).status === "pending_peer_check" && reviewedReports.length === 1 && reviewedReports.every(row => row.reviewed));
      await api("joon", "POST", "/api/v1/goals/report", reportBody, 201);
      safetyEntries = await safetyRecord();
      verify("a duplicate reviewed report does not rearm the hide", safetyEntries.find(row => row.id === dose.entry.id).status === "pending_peer_check" && safetyEntries.find(row => row.id === dose.entry.id).finding_status === "checked" && safetyEntries.find(row => row.id === dose.entry.id).content !== null);
      await changedFinding(dose.entry, "checked", true, "unhidden independently checked finding");
      await local.command("feature_off");
      const disabled = await checkin("ama", "disabled goal feature");
      verify("disabled check-in keeps an explicit empty goals block", disabled.goals.enabled === false && ["due", "your_findings", "lead", "suggested"].every(key => disabled.goals[key].length === 0) && disabled.goals.finding_changes_more === false);
      await dashboard(a);
      verify("dashboard navigation omits Common goals while disabled and retains stops", await a.locator(".report-nav a[href='/goals']").count() === 0 && await a.locator("[data-goal-stop-all]").count() === 1);
      for (const route of ["/goals", `/goals/${sensitive}`, "/goals/verify_goal_record.py"]) {
        const response = await reader.request.get(base + route);
        verify(`disabled ${route} returns no existence notice`, response.status() === 404 && !(await response.text()).includes(safety.banner));
      }
      await a.goto(base + heldWithdrawal, { waitUntil: "load" }); await visualReady(a); visualPage = a;
      verify("native own withdrawal remains available while feature is off", (await a.locator(".goal-entry-preview").innerText()).includes("5 mg/kg"));
      await a.locator("input[name='confirm']").check();
      await navigate(a, a.getByRole("button", { name: "Withdraw this entry", exact: true }));
      await visibleGoalControls(a, "M4 native withdrawal redirect");
      state = await take("M4 permanent authored withdrawal");
      verify("native withdrawal erases cleartext without rewriting either hash", state.entries.some(row => row.id === dose.entry.id && row.status === "withdrawn" && !row.content_present && row.entry_sha256 === dose.entry.entry_sha256 && row.content_sha256 === dose.entry.content_sha256));
      await local.command("feature_on");
      safetyEntries = await safetyRecord();
      verify("withdrawn sensitive content cannot be reconstructed in a download", safetyEntries.find(row => row.id === dose.entry.id).content === null);
      await redactedPage(sensitive, dose.entry, ["5 mg/kg", sensitiveSummary, taskQuestion], "erased sensitive source and dependent projections", safety.banner);
      await changedFinding(dose.entry, "withdrawn", false, "withdrawn own finding");
      await local.command("operator", { operator_action: "unhide", entry_id: dose.entry.id, expected_success: false });
      verify("operator unhide refuses an irreversibly withdrawn entry", (await safetyRecord()).find(row => row.id === dose.entry.id).content === null);
      for (const [person, lease] of [["ama", safeAma], ["joon", safeJoon]]) {
        sensitiveNotice("sensitive session completion", await api(person, "POST", `/api/v1/goals/sessions/${lease.session_id}/end`, { minutes_used: 1, did: "Compared fictional published methods and recorded the outcome." }));
      }
      state = await take("M4 sensitive sessions completed");
      verify("all work windows are closed before leadership changes", state.sessions.every(row => row.end_reason !== null) && session(state, safeAma.session_id).end_reason === "completed" && session(state, safeJoon.session_id).end_reason === "completed");

      // Paused standard scope: fresh role choices are separate from standing
      // work permission, and their owner cancellations survive feature off.
      await permit("ama", first, 10, true); await permit("joon", first, 10, true);
      const targetHandle = (await api("joon", "GET", `/api/v1/goals/${first}`)).goal.your_membership.handle;
      async function requestLeadership() {
        await api("ama", "POST", `/api/v1/goals/${first}/lead/handover`, { handle: targetHandle, charter_revision: 1, approved_by_owner: true });
        const role = await api("joon", "GET", `/api/v1/goals/${first}/lead/volunteer`);
        verify("lead role preview binds exact charter and contributor handle", role.notice_version === safety.role_notice_version && role.charter_revision === 1 && role.handle === targetHandle && role.statement.includes(targetHandle) && role.statement.includes("role only"));
        await api("joon", "POST", `/api/v1/goals/${first}/lead/volunteer`, { charter_revision: role.charter_revision, notice_version: role.notice_version, approved_by_owner: true });
      }
      await requestLeadership();
      await local.command("feature_off");
      await dashboard(a); await dashboard(j);
      verify("pending role cancellation is owner-only with feature off", await j.locator(`[data-goal-slug='${first}'] [data-goal-lead-withdraw]`).count() === 1 && await j.locator("[data-goal-handover-cancel]").count() === 0 && await a.locator(`[data-goal-slug='${first}'] [data-goal-handover-cancel]`).count() === 1 && await a.locator("[data-goal-lead-withdraw]").count() === 0);
      verify("pending role controls fit the viewport", await j.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await j.screenshot({ path: path.join(out, `goal-role-request-${name}.png`), fullPage: true });
      await navigate(j, j.locator(`[data-goal-slug='${first}'] [data-goal-lead-withdraw] button`));
      await visibleGoalControls(j, "M4 role cancellation redirect");
      await navigate(a, a.locator(`[data-goal-slug='${first}'] [data-goal-handover-cancel] button`));
      await visibleGoalControls(a, "M4 handover cancellation redirect");
      state = await take("M4 native role cancellations");
      verify("native cancellations revoke only pending choices", state.role_approvals.find(row => row.slug === first && row.person === "joon").state === "revoked" && state.handovers.find(row => row.slug === first && row.person === "ama").state === "revoked" && state.goals.find(row => row.slug === first).lead === "ama" && allocation(state, "ama", first).active && allocation(state, "joon", first).active);
      const refusedRole = await local.command("operator", { operator_action: "assign-lead", slug: first, handle: targetHandle, expected_success: false });
      verify("the CLI cannot assign a cancelled role choice", !refusedRole.operator.success);
      await local.command("feature_on"); await requestLeadership();
      state = await take("M4 fresh role approvals");
      const namedMembers = state.members.filter(row => row.slug === first);
      verify("both fictional owners explicitly opted into names before role assignment", namedMembers.length === 2 && namedMembers.every(row => row.show_first_name === 1));
      const assigned = await local.command("operator", { operator_action: "assign-lead", slug: first, handle: targetHandle });
      verify("the local operator CLI confirms the freshly approved volunteer", assigned.operator.success);
      state = await take("M4 operator lead assignment");
      verify("assignment changes role while preserving the contributor handle", state.goals.find(row => row.slug === first).lead === "joon" && (await api("joon", "GET", `/api/v1/goals/${first}`)).goal.lead.handle === targetHandle);
      const changedAllocations = state.allocations.filter(row => row.slug === first), changedMembers = state.members.filter(row => row.slug === first);
      verify("assignment clears both old and new lead work permissions and name choices", changedAllocations.length === 2 && changedMembers.length === 2 && changedAllocations.every(row => !row.active && row.lead_notice_version === null && row.lead_approved_at === null && row.show_first_name === 0) && changedMembers.every(row => row.show_first_name === 0));
      verify("assignment consumes the private role and handover choices", state.role_approvals.find(row => row.slug === first && row.person === "joon").state === "consumed" && state.handovers.find(row => row.slug === first).state === "consumed");
      await api("joon", "POST", `/api/v1/goals/${first}/sessions`, {}, 409);
      await api("ama", "POST", `/api/v1/goals/${first}/sessions`, {}, 409);
      verify("role approval grants no work to either previous or new lead", state.sessions.every(row => row.end_reason !== null));
      const assignedEvent = (await publicRecord()).findLast(row => row.kind === "operator" && row.content?.action === "assign_lead");
      verify("public assignment event contains no private role consent or account fields", assignedEvent && assignedEvent.content.handle === targetHandle && !["user_id", "target_user_id", "notice_version", "approved_at"].some(key => key in assignedEvent.content));
      verify("both sign-ins exchanged real signed codes with PKCE", state.oidc.code_exchanges === 2 && state.oidc.pkce_verified === 2 && state.oidc.jwks_reads > 0);
      // M5 keeps the completed owner-control journey intact. This fresh reader
      // has no historical allocation, so suggestions cannot imply work consent.
      // Let the real bounded authentication bucket refill after the long
      // frozen-clock journey. The identical-clock status assertions are done.
      await local.command("advance", { seconds: 61 });
      priya = await context({ javaScriptEnabled: false, reducedMotion: "reduce" });
      await login(priya, "priya");
      const pages = await local.command("seed_pages"); seeded.keys.priya = pages.key;
      const privateResponse = await ama.request.get(base + `/goals/${pages.private_slug}`);
      const privateText = await privateResponse.text();
      verify("a proposed sensitive goal is 404 even to a signed-in lead and reveals no banner or title", privateResponse.status() === 404 && !privateText.includes(safety.banner) && !privateText.includes("private M5 scope canary") && !("x-goal-banner" in privateResponse.headers()));
      const privateOwnerResponse = await joon.request.get(base + `/goals/${pages.private_slug}`);
      verify("the proposal author's public URL also stays 404", privateOwnerResponse.status() === 404 && !(await privateOwnerResponse.text()).includes(safety.banner));
      const privateAnonymous = await reader.request.get(base + `/goals/${pages.private_slug}`);
      verify("an anonymous proposal URL reveals no content or sensitive existence notice", privateAnonymous.status() === 404 && !(await privateAnonymous.text()).includes("private M5 scope canary") && !("x-goal-banner" in privateAnonymous.headers()));
      await dashboard(j);
      verify("dashboard navigation links public Common goals when enabled", await j.locator(".report-nav a[href='/goals']").count() === 1);
      publicText = await publicView("/goals", safety.banner);
      verify("public directory excludes private proposals and shows listed sensitive scope", !publicText.includes("private M5 scope canary") && await publicPage.locator(`[data-goal-slug='${sensitive}']`).count() === 1);
      await publicPage.screenshot({ path: path.join(out, `goals-public-index-${name}.png`), fullPage: true });
      const askInterest = await checkin("priya", "ask-first interest");
      verify("ask-first answers do not produce goal suggestions", askInterest.goals.suggested.length === 0 && askInterest.goals.due.length === 0);
      await local.command("interest_tier", { tier: "share" });
      const sharedInterest = await checkin("priya", "shared exact interest");
      verify("shared exact interests suggest at most one suitable goal without granting work", sharedInterest.goals.suggested.length === 1 && sharedInterest.goals.suggested[0].category === "mathematics" && [first, second].includes(sharedInterest.goals.suggested[0].goal) && sharedInterest.goals.due.length === 0);
      await local.command("interest_tier", { tier: "never" });
      verify("never-share answers do not produce goal suggestions", (await checkin("priya", "never-share interest")).goals.suggested.length === 0);

      const download = await reader.request.get(base + "/goals/verify_goal_record.py");
      const downloaded = await download.body(), expectedTool = readFileSync(path.join(repository, "tools/verify_goal_record.py"));
      verify("downloaded verification tool exactly matches the shipped source", download.status() === 200 && downloaded.equals(expectedTool));
      const toolPath = path.join(out, `downloaded-verify-${name}.py`), recordPath = path.join(out, `record-standard-${name}.jsonl`);
      writeFileSync(toolPath, downloaded);
      const finalRecord = await reader.request.get(base + `/goals/${first}/record.jsonl`), finalText = await finalRecord.text();
      verify("the saved verifier record contains only current public projections", finalRecord.status() === 200 && !finalText.includes(finding.content.summary) && !finalText.includes(summaryText));
      writeFileSync(recordPath, finalText);
      const verifyEnv = { ...process.env, PYTHONDONTWRITEBYTECODE: "1", PYTHONNOUSERSITE: "1" };
      delete verifyEnv.PYTHONPATH; delete verifyEnv.PYTHONHOME;
      const verifier = spawn(python, ["-S", toolPath, recordPath], { cwd: out, env: verifyEnv, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
      let verifyOutput = "";
      verifier.stdout.on("data", bytes => { verifyOutput += bytes.toString(); });
      verifier.stderr.on("data", bytes => { verifyOutput += bytes.toString(); });
      let verifierCode;
      try {
        verifierCode = await bounded(new Promise((resolve, reject) => { verifier.once("error", reject); verifier.once("exit", resolve); }), 15000, "Downloaded verifier exceeded its local-file time bound");
      } finally { if (verifier.exitCode === null) verifier.kill(); }
      const finalEnvelope = finalText.trim().split("\n").filter(Boolean).map(JSON.parse).at(-1);
      verify("the downloaded standalone script verifies local JSONL with site packages disabled", verifierCode === 0 && verifyOutput.includes(finalEnvelope.entry_sha256));
      writeFileSync(path.join(out, `verifier-${name}.txt`), verifyOutput);

      for (const days of [7, 7, 1]) await local.command("advance", { seconds: days * 86400 });
      const reminded = await checkin("joon", "aged lead scope");
      const leadReminder = reminded.goals.lead.find(row => row.goal === first);
      verify("lead reminders identify due summaries and stale tasks without reviving stopped permission", leadReminder?.summary_due === true && leadReminder.stale_tasks.length > 0 && leadReminder.work_permission_current === false && !reminded.goals.due.some(row => row.goal === first));
      state = await take("M5 final public/check-in evidence");
      verify("the third reader has no allocation or session after suggestions", !state.allocations.some(row => row.person === "priya") && !state.sessions.some(row => row.person === "priya"));
      verify("all three fictional sign-ins use signed codes and PKCE", state.oidc.code_exchanges === 3 && state.oidc.pkce_verified === 3);
      verify("no non-loopback requests occurred", forbidden.length === 0);
      verify("no script errors occurred", errors.length === 0);
      succeeded = true;
    } catch (error) {
      const page = visualPage;
      if (page && !page.isClosed()) {
        const failure = { message: String(error.message), pathname: new URL(page.url()).pathname };
        try {
          failure.layout = await bounded(page.evaluate(() => ({
            ready: document.readyState, width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
            fontStatus: document.fonts.status, boxSizing: getComputedStyle(document.documentElement).boxSizing,
            styles: [...document.querySelectorAll("link[rel='stylesheet']")].map(link => ({
              path: new URL(link.href).pathname, loaded: !!link.sheet })),
            candidates: [...document.querySelectorAll("body *")].map(node => ({
              tag: node.tagName, class: typeof node.className === "string" ? node.className : "svg",
              left: node.getBoundingClientRect().left, right: node.getBoundingClientRect().right,
              overflowX: getComputedStyle(node).overflowX,
            })).filter(node => node.right > innerWidth + 1 || node.left < -1).slice(0, 12),
          })), 2000, "Failure layout capture timed out");
        } catch (captureError) { failure.layoutError = String(captureError.message); }
        try {
          await page.screenshot({ path: path.join(out, `failure-${name}.png`), timeout: 5000 });
        } catch (captureError) { failure.screenshotError = String(captureError.message); }
        writeFileSync(path.join(out, `failure-layout-${name}.json`), JSON.stringify(failure, null, 2) + "\n");
      }
      throw error;
    } finally {
      for (const ctx of contexts) await ctx.close();
      const evidence = await local.stop();
      writeFileSync(path.join(out, `fixture-evidence-${name}.json`), JSON.stringify(evidence, null, 2) + "\n");
      if (!succeeded) writeFileSync(path.join(out, `failure-${name}.json`), JSON.stringify({ errors, forbidden }, null, 2) + "\n");
    }
  }
  writeFileSync(path.join(out, "verification.json"), JSON.stringify({ passed: true, checks, snapshots, layouts }, null, 2) + "\n");
  console.log(`${checks.length} goal browser checks passed. Evidence: ${out}`);
} finally { await browser.close(); }
