// Renders marketing/trailer-dark frame by frame. Serve the repository root first:
//   python3 -m http.server 8099 --bind 127.0.0.1
// then: node marketing/trailer-dark/render.cjs OUTDIR [FROM] [TO] [STEP]   (30 fps JPEG frames)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');
(async () => {
  const out = process.argv[2], fps = 30, from = +(process.argv[3] || 0), to = +(process.argv[4] || 1e9), step = +(process.argv[5] || 1);
  fs.mkdirSync(out, { recursive: true });
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1920, height: 1080 } });
  p.on('pageerror', e => console.log('ERR', e.message));
  await p.goto(process.env.FILM_URL || 'http://127.0.0.1:8099/marketing/trailer-dark/index.html');
  await p.waitForFunction(() => window.READY, null, { timeout: 60000 });
  const n = Math.round(await p.evaluate(() => window.DUR) * fps);
  for (let i = from; i < Math.min(n, to); i += step) {
    const data = await p.evaluate(t => { window.renderAt(t); return document.getElementById('c').toDataURL('image/jpeg', .95); }, i / fps);
    fs.writeFileSync(`${out}/${String(i).padStart(5, '0')}.jpg`, Buffer.from(data.split(',')[1], 'base64'));
    if (i % 150 === 0) console.log(i, '/', n, new Date().toISOString());
  }
  await b.close(); console.log('done');
})();
