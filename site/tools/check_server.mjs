// Browser evidence for the disabled hosted service, against local nginx staging only.
// This deliberately does not simulate or claim Google sign-in/account features.
import { createRequire } from "node:module";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import assert from "node:assert/strict";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const args = process.argv.slice(2);
const value = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const origin = new URL(value("--base-url", "http://localhost"));
if (!["http:", "https:"].includes(origin.protocol) || !["localhost", "127.0.0.1", "[::1]"].includes(origin.hostname)
    || origin.username || origin.password || origin.pathname !== "/" || origin.search || origin.hash) {
  throw new Error("--base-url must be a loopback HTTP(S) origin for local staging");
}
const out = path.resolve(value("--out", "artifacts/server-staging/browser"));
mkdirSync(out, { recursive: true });
const checks = [];
function check(name, condition) {
  assert.ok(condition, name);
  checks.push({ check: name, passed: true });
}
const browser = await chromium.launch();
try {
  for (const [name, viewport] of [["desktop", { width: 1440, height: 900 }], ["mobile", { width: 390, height: 844 }]]) {
    const context = await browser.newContext({ viewport });
    try {
      const page = await context.newPage();
      const scriptErrors = [];
      const externalRequests = [];
      page.on("pageerror", error => scriptErrors.push(error.message));
      page.on("request", request => {
        if (new URL(request.url()).origin !== origin.origin) externalRequests.push(request.url());
      });
      const health = await page.request.get(new URL("/healthz", origin).href);
      check(`${name}: installed service is healthy through nginx`, health.status() === 200 && (await health.json()).status === "ok");
      const securityContact = await page.request.get(new URL("/.well-known/security.txt", origin).href);
      check(`${name}: security contact refuses unconfigured HTTP staging without inventing a contact`, securityContact.status() === 503
            && await securityContact.text() === "Security contact information is not currently available.\n"
            && securityContact.headers()["content-type"].startsWith("text/plain; charset=utf-8")
            && securityContact.headers()["cache-control"].includes("no-store"));
      // The home page is only the trailer and one sign-in button; the footer
      // and header sign-in links live on the how-it-works page.
      await page.goto(new URL("about.html", origin).href);
      check(`${name}: footer keeps its sign-in link`, await page.getByRole("navigation", { name: "Footer" })
            .getByRole("link", { name: "Sign in", exact: true }).getAttribute("href") === "signin.html");
      await page.getByRole("banner").getByRole("link", { name: "Sign in", exact: true }).click();
      check(`${name}: landing-page sign-in reaches the hosted account boundary`, new URL(page.url()).pathname === "/signin"
            && (await page.locator("h1").innerText()) === "Accounts are not open yet.");
      for (const route of ["/signin", "/app"]) {
        const response = await page.goto(new URL(route, origin).href);
        await page.evaluate(() => document.fonts.ready);
        check(`${name} ${route}: returns service-unavailable status`, response.status() === 503);
        check(`${name} ${route}: explains that accounts are not open`, (await page.locator("h1").innerText()) === "Accounts are not open yet.");
        check(`${name} ${route}: offers no fake authentication control`, await page.locator("form, [href*='accounts.google.com'], [href*='/auth/google']").count() === 0);
        check(`${name} ${route}: stays within viewport`, await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
      }
      let reachedExample = false;
      for (let tab = 0; tab < 8 && !reachedExample; tab++) {
        await page.keyboard.press("Tab");
        reachedExample = await page.locator("a[href='/example']").evaluate(link => document.activeElement === link);
      }
      check(`${name}: labelled example link is keyboard reachable`, reachedExample);
      await page.screenshot({ path: path.join(out, `foundation-${name}.png`), fullPage: true });
      await page.locator("a[href='/example']").click();
      check(`${name}: example route is available`, new URL(page.url()).pathname === "/example");
      check(`${name}: example remains visibly labelled`, /example|fictional/i.test(await page.locator("body").innerText()));
      const board = await page.goto(new URL("/board", origin).href);
      check(`${name}: public board honestly refuses disabled access`, board.status() === 503
            && (await page.locator("body").innerText()).includes("The board is not open yet.")
            && await page.locator("[data-board-slug]").count() === 0);
      check(`${name}: disabled board notice stays within viewport`, await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
      const boardJSON = await page.request.get(new URL("/board.json", origin).href);
      check(`${name}: public board JSON has the exact disabled response`, boardJSON.status() === 503
            && JSON.stringify(await boardJSON.json()) === JSON.stringify({ error: "The board is not open yet." }));
      for (const route of ["/app/introductions", "/app/conversations"]) {
        const response = await page.goto(new URL(route, origin).href);
        check(`${name} ${route}: honestly refuses disabled interaction access`, response.status() === 503
              && (await page.locator("body").innerText()).includes("Introductions and conversations are not open yet.")
              && await page.locator("form").count() === 0);
        check(`${name} ${route}: disabled notice stays within viewport`, await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
      }
      for (const route of ["/app/notifications", "/app/notifications/preferences"]) {
        const response = await page.goto(new URL(route, origin).href);
        check(`${name} ${route}: refuses disabled notification access`, response.status() === 503
              && (await page.locator("body").innerText()).includes("Sign-in is not open yet.")
              && await page.locator("form").count() === 0);
        check(`${name} ${route}: disabled notice stays within viewport`, await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
      }
      const reporting = await page.goto(new URL("/app/report", origin).href);
      check(`${name}: concern reporting refuses disabled access`, reporting.status() === 503
            && (await page.locator("body").innerText()).includes("Reporting is not open yet.")
            && await page.locator("form").count() === 0);
      check(`${name}: reporting notice stays within viewport`, await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
      check(`${name}: no script errors`, scriptErrors.length === 0);
      check(`${name}: no external browser requests`, externalRequests.length === 0);
    } finally {
      await context.close();
    }
  }
  const context = await browser.newContext({ javaScriptEnabled: false });
  try {
    const page = await context.newPage();
    await page.goto(new URL("/signin", origin).href);
    check("account status is readable without JavaScript", (await page.locator("h1").innerText()) === "Accounts are not open yet.");
    await page.locator("a[href='/']").click();
    check("return-to-site link works without JavaScript", new URL(page.url()).pathname === "/");
  } finally {
    await context.close();
  }
  writeFileSync(path.join(out, "verification.json"), JSON.stringify({ passed: true, base_url: origin.origin, checks }, null, 2) + "\n");
  console.log(`hosted foundation browser checks passed: ${checks.length} (evidence in ${out})`);
} finally {
  await browser.close();
}
