// Browser checks for the Connect the Bots site (desktop and mobile Chromium, plus reduced motion).
// Serves site/ over a local HTTP server (as nginx would) and fails on script errors, sideways
// scroll, any non-local request, a prompt that differs from the download,
// or a dashboard that implies something is live or that the other person agreed.
//   node site/tools/check_site.mjs [--out artifacts/site]
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { mkdirSync, readFileSync, writeFileSync, existsSync, statSync } from "node:fs";
import http from "node:http";
import path from "node:path";
import assert from "node:assert/strict";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outArg = process.argv.indexOf("--out");
const out = path.resolve(outArg > 0 ? process.argv[outArg + 1] : "artifacts/site");
mkdirSync(out, { recursive: true });
const baseArg = process.argv.indexOf("--base-url");
let externalBase;
if (baseArg > 0) {
  const value = new URL(process.argv[baseArg + 1]);
  if (!["http:", "https:"].includes(value.protocol) || !["localhost", "127.0.0.1", "[::1]"].includes(value.hostname)
      || value.username || value.password || value.pathname !== "/" || value.search || value.hash) {
    throw new Error("--base-url must be a loopback HTTP(S) origin for local staging");
  }
  externalBase = value.href;
}

const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".woff2": "font/woff2", ".md": "text/markdown", ".jpg": "image/jpeg", ".mp4": "video/mp4", ".vtt": "text/vtt" };
// Film play counter, answered like nginx does (server-setup.sh): POST /v/play|sound|done -> 204.
const counted = [];
const server = http.createServer((req, res) => {
  const counter = new URL(req.url, "http://x").pathname.match(/^\/v\/(play|sound|done)$/);
  if (counter) { if (req.method === "POST") counted.push(counter[1]); res.writeHead(204); res.end(); return; }
  const rel = decodeURIComponent(new URL(req.url, "http://x").pathname).replace(/^\/+/, "") || "index.html";
  const file = path.join(site, rel);
  if (!file.startsWith(site) || !existsSync(file) || statSync(file).isDirectory()) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { "Content-Type": TYPES[path.extname(file)] || "application/octet-stream" });
  res.end(readFileSync(file));
});
if (!externalBase) await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = externalBase || `http://127.0.0.1:${server.address().port}/`;
const promptMd = readFileSync(path.join(site, "assets", "connect-the-bots-prompt.md"), "utf8");

const browser = await chromium.launch();
let checks = 0;
function ok(cond, msg) { assert.ok(cond, msg); checks++; }

for (const [name, opts] of [
  ["desktop", { viewport: { width: 1440, height: 900 } }],
  ["mobile", { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 }],
]) {
  const ctx = await browser.newContext({ ...opts, reducedMotion: "no-preference", permissions: ["clipboard-read", "clipboard-write"] });
  const page = await ctx.newPage();
  const errors = [], remote = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("request", (r) => { if (!r.url().startsWith(base) && !r.url().startsWith("data:")) remote.push(r.url()); });
  const noSideways = () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

  // Home: only the film and one Enter action; the film plays by itself, once, with sound where allowed
  await page.goto(base + "index.html");
  await page.evaluate(() => document.fonts.ready);
  const signin = page.locator("[data-enter]");
  ok(await signin.isVisible() && /^\s*Enter\s*$/.test(await signin.innerText()) && (await signin.getAttribute("href")) === "what.html", `${name}: home has the Enter action to What this is`);
  ok((await page.locator("[data-signin]").getAttribute("href")) === "signin.html", `${name}: returning members can still sign in from home`);
  ok(await page.locator("main a.btn, main button.btn").count() === 1, `${name}: home has exactly one primary action`);
  await page.waitForFunction(() => { const v = document.querySelector("[data-trailer]"); return v.currentTime > 0.3; }, null, { timeout: 15000 });
  const home = await page.evaluate(() => window.__home.state());
  ok(!home.paused, `${name}: film autoplays`);
  const startedMuted = home.muted;
  ok(home.src.endsWith(name === "mobile" ? "assets/trailer-480.mp4" : "assets/trailer.mp4"), `${name}: ${name === "mobile" ? "phones get the light 480p film" : "desktop gets the 720p film"}`);
  const box = await page.locator("[data-trailer]").boundingBox();
  const vp = page.viewportSize();
  ok(box.width >= vp.width * 0.8 && box.y >= 0 && box.y + box.height <= vp.height, `${name}: film is a large framed screen, not cropped`);
  const cta = await signin.boundingBox();
  ok(cta.y + cta.height <= vp.height, `${name}: Enter is on screen without scrolling`);
  ok(/Sascha Ende/.test(await page.locator("[data-music-credit]").innerText()) && /CC BY 4\.0/.test(await page.locator("[data-music-credit]").innerText()), `${name}: home credits the music (CC BY 4.0)`);
  for (const [label, href] of [["Privacy", "/privacy"], ["Terms", "/terms"], ["How it works", "about.html"]]) {
    ok((await page.getByRole("link", { name: label, exact: true }).getAttribute("href")) === href, `${name}: home links ${label}`);
  }
  if (!startedMuted) { await page.locator("[data-sound]").click(); ok((await page.evaluate(() => window.__home.state())).muted, `${name}: sound can be turned off`); }
  await page.locator("[data-sound]").click();
  ok(!(await page.evaluate(() => window.__home.state())).muted && (await page.locator("[data-sound-label]").innerText()) === "Sound off", `${name}: sound can be turned on`);
  if (!externalBase) {
    await page.waitForTimeout(300);
    ok(counted.includes("play") && counted.includes("sound"), `${name}: film start and sound are counted (same-site, no cookies)`);
  }
  ok(await noSideways(), `${name}: home has no sideways scroll`);
  ok((await page.request.get(base + "assets/trailer-480.mp4")).ok(), `${name}: light film is served`);
  await page.evaluate(() => { const v = document.querySelector("[data-trailer]"); v.muted = true; v.currentTime = 25; v.pause(); });
  await page.waitForTimeout(700);
  await page.screenshot({ path: path.join(out, `home-${name}.png`) });

  // What this is: the page behind Enter, ending in Google sign-in
  await page.goto(base + "what.html");
  await page.evaluate(() => document.fonts.ready);
  ok(/open board where AI agents meet/.test(await page.locator("h1").innerText()), `${name}: what-this-is page opens with the statement`);
  ok(await page.locator(".steps > li").count() === 7 && /The big one/i.test(await page.locator(".steps .big").innerText()), `${name}: seven steps, fingerprint matching marked as the big one`);
  const whatSignin = page.locator("[data-signin]");
  ok(await whatSignin.isVisible() && /Sign in with Google/.test(await whatSignin.innerText()) && (await whatSignin.getAttribute("href")) === "signin.html", `${name}: what-this-is page has Sign in with Google on the first screen`);
  ok(/Let.s connect/.test(await page.locator(".what-sign").innerText()), `${name}: page ends "Let's connect... the bots."`);
  ok(await noSideways(), `${name}: what-this-is page has no sideways scroll`);
  await page.screenshot({ path: path.join(out, `what-${name}.png`), fullPage: true });

  // How it works: the longer page with the prompt, kept at about.html
  await page.goto(base + "about.html");
  await page.evaluate(() => document.fonts.ready);
  ok(/Connecting\s+human\s+intelligence/.test(await page.locator("h1").innerText()), `${name}: headline`);
  const primary = page.locator(".opening .btn-primary");
  ok((await primary.innerText()) === "Get started" && await primary.isVisible(), `${name}: primary action visible at load`);
  const opening = page.locator(".opening [data-trailer]");
  ok(await opening.count() === 1 && await page.locator(".film-stage").count() === 0, `${name}: the trailer is the opening film and the old film is gone`);
  ok(await page.evaluate(() => { const v = document.querySelector("[data-trailer]"); return v.paused && v.readyState === 0; }), `${name}: trailer does not autoplay or preload`);
  ok(await noSideways(), `${name}: landing has no sideways scroll`);

  // Trailer: loads nothing until played, has captions and a poster; agents section stays honest
  const tr = page.locator("[data-trailer]");
  ok((await tr.getAttribute("preload")) === "none" && (await tr.getAttribute("poster")) === "assets/trailer-poster.jpg", `${name}: trailer waits for a click and has a poster`);
  ok((await page.request.get(base + "assets/trailer.mp4")).ok() && (await page.request.get(base + "assets/trailer-poster.jpg")).ok(), `${name}: trailer and poster are served`);
  ok(/^WEBVTT/.test(await (await page.request.get(base + "assets/trailer.en.vtt")).text()), `${name}: trailer has English captions`);
  ok(/Sascha Ende/.test(await page.locator("[data-music-credit]").innerText()) && /CC BY 4\.0/.test(await page.locator("[data-music-credit]").innerText()), `${name}: trailer music is credited (CC BY 4.0)`);
  ok((await page.locator(".agents li").count()) >= 7 && /not a partner|None of them is a partner/.test(await page.locator(".agents-note").innerText()), `${name}: any-agent section lists agents without claiming partnerships`);

  // Prompt: shown text equals the download, copy works, download link present
  ok((await page.locator("[data-prompt-text]").textContent()) === promptMd, `${name}: shown prompt matches the downloadable file`);
  ok((await page.locator("[data-download]").getAttribute("href")) === "assets/connect-the-bots-prompt.md", `${name}: download link`);
  await page.locator("[data-copy]").click();
  await page.waitForTimeout(200);
  ok(/Copied|Selected/.test(await page.locator("[data-copied]").innerText()), `${name}: copy gives feedback`);
  // Windows clipboard APIs translate LF to CRLF; every other character must match exactly.
  if (name === "desktop") ok((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, "\n") === promptMd, "desktop: clipboard holds the prompt");
  await page.screenshot({ path: path.join(out, `about-${name}.png`), fullPage: true });

  // Dashboard states
  const expect = {
    setup: "Your agent isn't set up yet.", working: "Nothing needs you right now.", quiet: "Nothing worth your time yet.",
    question: "Would you pay for a short, scoped piece of firmware work?", connection: "your farms, his firmware",
    paused: "Check-ins are paused", failed: "This morning's check-in didn't report back",
  };
  for (const [state, text] of Object.entries(expect)) {
    await page.goto("about:blank");
    await page.goto(base + "dashboard.html#" + state);
    await page.waitForTimeout(150);
    ok((await page.locator("[data-view]").innerText()).includes(text), `${name}: ${state} state`);
    ok(await noSideways(), `${name}: ${state} has no sideways scroll`);
    if (state === "quiet" || state === "working") {
      ok(await page.locator(".conn-card, .notify").count() === 0, `${name}: ${state} creates no urgency`);
    }
    if (state === "setup") ok(await page.getByText("Needs integration").isVisible(), `${name}: setup is honest about integration`);
    await page.screenshot({ path: path.join(out, `dashboard-${state}-${name}.png`), fullPage: true });
  }
  await page.goto("about:blank");
  await page.goto(base + "dashboard.html#connection");
  ok(await page.locator(".notify").getByText("One person worth meeting").isVisible(), `${name}: connection notification`);
  ok(await page.locator(".notify").getByText("Example notification").isVisible(), `${name}: notification labelled as example`);

  // Connection: invitation is not acceptance; conversation opens only after both say yes
  await page.goto("about:blank");
  await page.goto(base + "dashboard.html#talk");
  await page.waitForTimeout(100);
  ok(await page.getByText("This conversation isn't open yet.").isVisible(), `${name}: no conversation before both agree`);
  await page.goto(base + "dashboard.html#joon");
  for (const h of ["Why now", "Evidence", "Still unknown", "A small first step"]) ok(await page.getByRole("heading", { name: h }).isVisible(), `${name}: connection explains ${h}`);
  ok(!/\d+\s?%/.test(await page.locator("[data-view]").innerText()), `${name}: no percentages`);
  await page.getByRole("button", { name: "Ask for an introduction" }).click();
  ok(await page.getByText("Waiting for Joon to decide").isVisible(), `${name}: invitation shown as waiting`);
  ok(!(await page.locator("[data-view]").innerText()).includes("You both said yes"), `${name}: invitation is not acceptance`);
  await page.screenshot({ path: path.join(out, `connection-${name}.png`), fullPage: true });
  await page.getByRole("button", { name: /Joon says yes/ }).click();
  await page.getByRole("link", { name: "Start the conversation" }).click();
  await page.getByRole("button", { name: "Help me start" }).click();
  await page.getByRole("button", { name: "Put a draft in the box to edit" }).click();
  ok((await page.locator("#msg").inputValue()).startsWith("Hi Joon"), `${name}: draft lands in the person's own box`);
  await page.getByRole("button", { name: "Send" }).click();
  ok(await page.getByText("not delivered (prototype)").isVisible(), `${name}: sent message labelled as not delivered`);
  await page.screenshot({ path: path.join(out, `conversation-${name}.png`), fullPage: true });

  if (externalBase) {
    const signIn = await page.request.get(base + "signin.html");
    ok(new URL(signIn.url()).pathname === "/signin" && [200, 503].includes(signIn.status())
       && !(await signIn.text()).includes("No Google account is contacted"), `${name}: hosted sign-in link reaches the account service`);
  } else {
    await page.goto(base + "signin.html");
    ok(await page.getByText("No Google account is contacted").isVisible(), `${name}: sign-in preview disclosure`);
  }

  ok(errors.length === 0, `${name}: script errors: ${errors.join("; ")}`);
  ok(remote.length === 0, `${name}: remote requests: ${remote.join(", ")}`);
  await ctx.close();
}

// Reduced motion: no autoplay; keyboard reaches the primary action quickly
{
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, reducedMotion: "reduce" });
  const page = await ctx.newPage();
  await page.goto(base + "index.html");
  await page.waitForTimeout(600);
  ok(await page.evaluate(() => document.querySelector("[data-trailer]").paused) && await page.locator("[data-play]").isVisible(), "reduced motion: home film waits for a click");
  let home = false;
  for (let i = 0; i < 4 && !home; i++) {
    await page.keyboard.press("Tab");
    home = (await page.evaluate(() => document.activeElement && document.activeElement.hasAttribute("data-enter")));
  }
  ok(home, "keyboard: home Enter within 4 tabs");
  await page.screenshot({ path: path.join(out, "home-reduced-motion.png") });
  await page.goto(base + "about.html");
  await page.waitForTimeout(600);
  ok(await page.evaluate(() => document.querySelector("[data-trailer]").paused), "reduced motion: trailer does not play by itself");
  let reached = false;
  for (let i = 0; i < 8 && !reached; i++) {
    await page.keyboard.press("Tab");
    reached = (await page.evaluate(() => document.activeElement && document.activeElement.textContent.trim())) === "Get started";
  }
  ok(reached, "keyboard: primary action within 8 tabs");
  await page.screenshot({ path: path.join(out, "landing-reduced-motion.png") });
  await ctx.close();
}

await browser.close();
if (!externalBase) server.close();
writeFileSync(path.join(out, "verification.json"), JSON.stringify({ passed: true, checks, base_url: base }, null, 2) + "\n");
console.log(`site checks passed: ${checks} (screenshots in ${out})`);
