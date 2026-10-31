// Export the opening film: assets/explainer.mp4 (1920x840), assets/explainer-tall.mp4
// (1080x1350, for phones and social) and the poster image. Every frame comes from the
// same film.js the landing page plays, so the video and the page cannot drift.
// Needs Playwright (Chromium) and ffmpeg.
//   node site/tools/record_explainer.mjs [--fps 30]
import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const here = path.dirname(fileURLToPath(import.meta.url));
const site = path.resolve(here, "..");
const fpsArg = process.argv.indexOf("--fps");
const fps = fpsArg > 0 ? Number(process.argv[fpsArg + 1]) : 30;
const browser = await chromium.launch();
for (const [layout, viewport, scale, file] of [
  ["wide", { width: 1600, height: 700 }, 1.2, "explainer.mp4"],
  ["tall", { width: 800, height: 1000 }, 1.35, "explainer-tall.mp4"],
]) {
  const out = path.join(site, "assets", file);
  const page = await browser.newPage({ viewport, deviceScaleFactor: scale });
  await page.goto(pathToFileURL(path.join(site, "film.html")).href + "?t=0&layout=" + layout);
  await page.evaluate(() => document.fonts.ready);
  const duration = await page.evaluate(() => window.CTBFilm.DUR);
  const ff = spawn("ffmpeg", ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(fps), "-c:v", "png", "-i", "-",
    "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out], { stdio: ["pipe", "inherit", "inherit"] });
  const done = new Promise((resolve, reject) => ff.on("close", (code) => (code === 0 ? resolve() : reject(new Error("ffmpeg exited " + code)))));
  const frames = Math.round(duration * fps);
  // hold the final frame for one extra second so the reveal can be read
  for (let i = 0; i <= frames + fps; i++) {
    await page.evaluate((t) => window.seek(t), Math.min(duration, i / fps));
    const png = await page.screenshot({ type: "png" });
    if (!ff.stdin.write(png)) await new Promise((r) => ff.stdin.once("drain", r));
  }
  ff.stdin.end();
  await done;
  if (layout === "wide") await page.screenshot({ path: path.join(site, "assets", "explainer-poster.jpg"), type: "jpeg", quality: 88 });
  await page.close();
  console.log(`wrote ${out} (${frames + fps + 1} frames at ${fps} fps)`);
}
await browser.close();
