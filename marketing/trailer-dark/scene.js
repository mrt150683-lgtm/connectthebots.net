// Connect the Bots dark opener trailer. Deterministic: every frame is a pure
// function of t (seconds); nothing reads the clock. Timed to the 66 s excerpt of
// "Cry" by Sascha Ende (CC BY 4.0) used by the home trailer: 100 BPM, breakdown,
// build from 14.4 s, hush 33.6-36.0 s, drop at 36.0 s. Every word on screen is
// fictional and written for this film. window.renderAt(t) draws one frame.
'use strict';
const W = 1920, H = 1080, DUR = 68, BEAT = 0.6, F = 1000;
const C = { bg: '#08090c', ink: '#eef0f4', body: '#c3c8d1', muted: '#8d94a1', faint: '#5f6672', edge: '#2a2e38',
  cob: '#8ea0ff', fill: '#3b5bff', cobInk: '#b4c0ff', good: '#5fd394' };
const SANS = '"Schibsted Grotesk", Arial, sans-serif', SERIF = '"Newsreader", Georgia, serif', MONO = '"IBM Plex Mono", monospace';
const cv = document.getElementById('c'), x = cv.getContext('2d');

const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const lerp = (a, b, k) => a + (b - a) * k;
const ease = k => { k = clamp(k); return k < .5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2; };
const easeOut = k => 1 - Math.pow(1 - clamp(k), 3);
const span = (t, a, b) => clamp((t - a) / (b - a));
const win = (t, a, b, fi = .5, fo = .5) => Math.min(span(t, a, a + fi), 1 - span(t, b - fo, b));
function rng(s) { return () => { s |= 0; s = s + 0x6D2B79F5 | 0; let q = Math.imul(s ^ s >>> 15, 1 | s); q = q + Math.imul(q ^ q >>> 7, 61 | q) ^ q; return ((q ^ q >>> 14) >>> 0) / 4294967296; }; }
const R = rng(20261031);
const rgba = (hex, a) => { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${n >> 8 & 255},${n & 255},${clamp(a)})`; };
const beatPulse = t => { const p = ((t % BEAT) + BEAT) % BEAT; return Math.exp(-p / .14); };

// ---------- the people in the opening ----------
const YOU = [0, 0, 0], PART = [760, 160, 0], MID = [380, 80, 0];
const HERO = 'drones that still find their way when GPS is jammed';
const FRAG = [ // ring position, text, time it resolves (every two beats from the build)
  { p: [-620, -260, 0], s: 'ex-combat medic writing a field-trauma course', t: 14.4 },
  { p: PART, s: 'I built star trackers for satellites', t: 15.6 },
  { p: [-700, 200, 0], s: 'a mesh radio that keeps talking when the network dies', t: 16.8 },
  { p: [120, -300, 0], s: 'armour-grade ceramic at half the weight', t: 18.0 },
  { p: [560, -230, 0], s: 'a night-vision optics engineer between contracts', t: 19.2 },
  { p: [-260, 300, 0], s: 'a rescue team that needs thermal maps', t: 20.4 },
  { p: [400, 310, 0], s: 'bomb-disposal veteran who wants to build training sims', t: 21.6 },
  { p: [-800, -40, 0], s: 'batteries that work at minus forty', t: 22.8 },
];
const DRIFT = ['a satellite link that fits in a backpack', 'an ex-navy diver mapping wrecks', 'I write code for flight controllers',
  'someone who has run disaster logistics', 'a precision-optics lens grinder', 'a ranger who knows the mountain passes']
  .map((s, i) => ({ s, x: [300, 1250, 520, 1180, 240, 980][i], y: [250, 210, 820, 690, 560, 900][i], a: .25 + i * .62, dx: (R() - .5) * 30, dy: (R() - .5) * 16 }));
const REASONS = ['his trauma course, her sim engine', 'your mesh radio, their rescue team', 'her ceramic, his body armour', 'your batteries, their arctic drones',
  'his thermal maps, her rescue dogs', 'your optics, their night drones', 'her flight code, his airframe'];

// Probes in the build: dashed lines that reach out and come back without connecting.
const PROBE_FROM = [YOU, ...FRAG.map(f => f.p)];
const PROBES = [];
for (let k = 0; k < 26; k++) {
  const start = k < 16 ? 24 + k * .6 : 31.2 + (k - 16) * .24;
  let a = Math.floor(R() * PROBE_FROM.length), b = Math.floor(R() * PROBE_FROM.length);
  if (a === b || (PROBE_FROM[a] === YOU && PROBE_FROM[b] === PART) || (PROBE_FROM[a] === PART && PROBE_FROM[b] === YOU)) b = (b + 3) % PROBE_FROM.length;
  if (a === b) b = (b + 1) % PROBE_FROM.length;
  PROBES.push({ a: PROBE_FROM[a], b: PROBE_FROM[b], start, reach: .3 + R() * .38 });
}

// ---------- the field revealed on the drop: pairs, each one a line with a reason ----------
const PAIRS = [];
for (let i = 0; i < 7200; i++) {
  const u = R(), r = 650 * Math.exp(u * Math.log(150)), ang = R() * Math.PI * 2;
  const c = [Math.cos(ang) * r, Math.sin(ang) * r * .56, (R() - .5) * r * .5];
  const L = r * (.05 + R() * .06), d = R() * Math.PI * 2, e = (R() - .5) * .6;
  const A = [c[0] - Math.cos(d) * L, c[1] - Math.sin(d) * L, c[2] - e * L], B = [c[0] + Math.cos(d) * L, c[1] + Math.sin(d) * L, c[2] + e * L];
  // the cascade radiates out on the beat: 36 s plus whole beats, further out = later
  const at = 36 + .3 + Math.round(u * 15 + R() * 1.2) * BEAT;
  PAIRS.push({ A, B, r, at, s: r * .005 + 2, why: r < 12000 ? REASONS[i % REASONS.length] : null });
}
// a few pairs close in, kept clear of the one connection, so the centre is never an empty hole
for (let i = 0; i < 160; i++) {
  const r = 140 + R() * 520, ang = R() * Math.PI * 2, c = [MID[0] + Math.cos(ang) * r * 1.4, MID[1] + Math.sin(ang) * r * .8, (R() - .5) * 200];
  const along = clamp(((c[0]) * PART[0] + (c[1]) * PART[1]) / (PART[0] ** 2 + PART[1] ** 2));
  if (Math.hypot(c[0] - along * PART[0], c[1] - along * PART[1]) < 150) continue;
  const L = 18 + R() * 26, d = R() * Math.PI * 2;
  PAIRS.push({ A: [c[0] - Math.cos(d) * L, c[1] - Math.sin(d) * L, c[2]], B: [c[0] + Math.cos(d) * L, c[1] + Math.sin(d) * L, c[2]], r, at: 36.3 + Math.round(R() * 3) * BEAT, s: 2.6, why: null });
}

// ---------- camera: centre (cx, cy) and distance d ----------
const KEYS = [ // [time, cx, cy, d]
  [0, 240, 0, 430], [4.8, 240, 0, 430], [14.4, 220, 0, 360], [24, 120, 0, 1180], [33.6, 130, 10, 1260],
  [35.6, MID[0], MID[1], 860], [36, MID[0], MID[1], 860]];
function camAt(t) {
  if (t >= 36 && t < 45.6) { // pull back hard on the drop and keep going
    const k = easeOut(span(t, 36, 45.6) * 1.05) * .55 + span(t, 36, 45.6) * .45;
    return [MID[0], MID[1], 860 * Math.pow(140, k)];
  }
  if (t >= 45.6) { // dive back to the one connection, then a slow push
    const far = 860 * Math.pow(140, 1), near = 660;
    if (t < 49.2) { const k = ease(span(t, 45.6, 49.2)); return [MID[0], MID[1], far * Math.pow(near / far, k)]; }
    return [MID[0], MID[1], lerp(near, 600, span(t, 49.2, 57.6))];
  }
  for (let i = 1; i < KEYS.length; i++) if (t <= KEYS[i][0]) {
    const [t0, ...a] = KEYS[i - 1], [t1, ...b] = KEYS[i], k = ease(span(t, t0, t1));
    return [lerp(a[0], b[0], k), lerp(a[1], b[1], k), a[2] * Math.pow(b[2] / a[2], k)];
  }
  return KEYS[KEYS.length - 1].slice(1);
}
let CAM = [0, 0, 1];
function proj(p) { const z = p[2] + CAM[2]; if (z < 40) return null; const s = F / z; return [W / 2 + (p[0] - CAM[0]) * s, H / 2 + (p[1] - CAM[1]) * s, s]; }

// ---------- drawing helpers ----------
function text(s, px, py, size, font, color, a, align = 'left', base = 'alphabetic', track = 0) {
  if (a <= 0.003 || size < 2.5) return;
  const q = font.indexOf('"'); x.save(); x.globalAlpha = clamp(a); x.font = `${font.slice(0, q)} ${size}px ${font.slice(q)}`; x.fillStyle = color; x.textAlign = align; x.textBaseline = base;
  if (track) x.letterSpacing = `${track}em`;
  x.fillText(s, px, py); x.restore();
}
function serif(w = 400) { return `italic ${w} `; }
function disc(p, r, color, a) { if (a <= 0) return; x.globalAlpha = a; x.fillStyle = color; x.beginPath(); x.arc(p[0], p[1], Math.max(r, .6), 0, 7); x.fill(); x.globalAlpha = 1; }
function ring(p, r, color, a, lw = 1.5) {
  if (a <= 0) return; x.globalAlpha = a;
  if (r < 1.6) { x.fillStyle = color; x.fillRect(p[0] - .7, p[1] - .7, 1.4, 1.4); }
  else { x.strokeStyle = color; x.lineWidth = Math.min(lw, r * .5); x.beginPath(); x.arc(p[0], p[1], r, 0, 7); x.stroke(); }
  x.globalAlpha = 1;
}
function line(p, q, color, a, lw = 1, dash = null, k = 1) {
  if (a <= 0 || k <= 0) return; x.globalAlpha = a; x.strokeStyle = color; x.lineWidth = lw; x.setLineDash(dash || []);
  x.beginPath(); x.moveTo(p[0], p[1]); x.lineTo(lerp(p[0], q[0], k), lerp(p[1], q[1], k)); x.stroke(); x.setLineDash([]); x.globalAlpha = 1;
}
function onLine(s, p, q, size, color, a, gap = 10) { // a reason set on its line, upright
  if (a <= 0 || size < 5) return;
  let ang = Math.atan2(q[1] - p[1], q[0] - p[0]); if (ang > Math.PI / 2) ang -= Math.PI; if (ang < -Math.PI / 2) ang += Math.PI;
  x.save(); x.translate((p[0] + q[0]) / 2, (p[1] + q[1]) / 2); x.rotate(ang); x.globalAlpha = clamp(a);
  x.font = `italic 400 ${size}px ${SERIF}`; x.textAlign = 'center'; x.textBaseline = 'alphabetic'; x.fillStyle = color;
  x.fillText(s, 0, -gap); x.restore();
}
function glow(cx, cy, r, a) {
  if (a <= 0) return; const g = x.createRadialGradient(cx, cy, 0, cx, cy, r);
  g.addColorStop(0, `rgba(59,91,255,${.2 * a})`); g.addColorStop(.45, `rgba(59,91,255,${.08 * a})`); g.addColorStop(1, 'rgba(59,91,255,0)');
  x.fillStyle = g; x.fillRect(0, 0, W, H);
}
function shade(cx, cy, rx, ry, a) { // a soft dark backing so a statement reads over the field (not a glow)
  if (a <= 0) return; x.save(); x.translate(cx, cy); x.scale(1, ry / rx);
  const g = x.createRadialGradient(0, 0, 0, 0, 0, rx); g.addColorStop(0, `rgba(8,9,12,${.62 * a})`); g.addColorStop(.55, `rgba(8,9,12,${.42 * a})`); g.addColorStop(1, 'rgba(8,9,12,0)');
  x.fillStyle = g; x.fillRect(-rx, -rx, rx * 2, rx * 2); x.restore();
}
function tick(px, py, s, color, a) {
  if (a <= 0) return; x.globalAlpha = a; x.strokeStyle = color; x.lineWidth = s * .16; x.lineCap = 'round'; x.lineJoin = 'round';
  x.beginPath(); x.moveTo(px, py - s * .05); x.lineTo(px + s * .32, py + s * .28); x.lineTo(px + s * .9, py - s * .38); x.stroke(); x.lineCap = 'butt'; x.globalAlpha = 1;
}

// ---------- the frame ----------
// ---------- tactical layer: grain, scanlines, HUD, chapters, radar, shockwaves ----------
const GRAIN = (() => { const c = document.createElement('canvas'); c.width = c.height = 256; const g = c.getContext('2d'), im = g.createImageData(256, 256), r = rng(7);
  for (let i = 0; i < im.data.length; i += 4) { const v = r() * 255; im.data[i] = im.data[i + 1] = im.data[i + 2] = v; im.data[i + 3] = 255; }
  g.putImageData(im, 0, 0); return c; })();
const SCAN = (() => { const c = document.createElement('canvas'); c.width = 4; c.height = 4; const g = c.getContext('2d'); g.fillStyle = 'rgba(0,0,0,.22)'; g.fillRect(0, 0, 4, 1); g.fillRect(0, 2, 4, 1); return c; })();
const CHAPTERS = [ // [start, code, name] on the music's sections
  [0.4, '00', 'DARK'], [4.8, '01', 'THE ASSET'], [14.4, '02', 'ALONE'], [24.0, '03', 'RECON'],
  [33.6, '04', 'SILENCE'], [36.0, '05', 'CONTACT'], [45.6, '06', 'CONSENT']];
function typed(s, t, t0, cps = 38) { return s.slice(0, Math.max(0, Math.floor((t - t0) * cps))); }
function fmtT(t) { const m = Math.floor(t / 60), sec = t - m * 60; return `T+${String(m).padStart(2, '0')}:${sec.toFixed(1).padStart(4, '0')}`; }
function grainAndScan(t, a) {
  const f = Math.round(t * 30); x.save();
  x.globalAlpha = .06 * a; x.globalCompositeOperation = 'overlay';
  x.fillStyle = x.createPattern(GRAIN, 'repeat'); x.translate(-(f * 73) % 256, -(f * 151) % 256); x.fillRect(0, 0, W + 256, H + 256);
  x.setTransform(1, 0, 0, 1, 0, 0); x.globalCompositeOperation = 'source-over'; x.globalAlpha = .55 * a;
  x.fillStyle = x.createPattern(SCAN, 'repeat'); x.fillRect(0, 0, W, H);
  const v = x.createRadialGradient(W / 2, H / 2, H * .35, W / 2, H / 2, W * .72); v.addColorStop(0, 'rgba(0,0,0,0)'); v.addColorStop(1, `rgba(0,0,0,${.55 * a})`);
  x.globalAlpha = 1; x.fillStyle = v; x.fillRect(0, 0, W, H); x.restore();
}
function hud(t, a) {
  if (a <= 0) return; x.save(); x.globalAlpha = a; x.strokeStyle = 'rgba(238,240,244,.35)'; x.lineWidth = 1.5;
  const m = 40, L = 34;
  for (const [cx, cy, sx, sy] of [[m, m, 1, 1], [W - m, m, -1, 1], [W - m, H - m, -1, -1], [m, H - m, 1, -1]]) {
    x.beginPath(); x.moveTo(cx + sx * L, cy); x.lineTo(cx, cy); x.lineTo(cx, cy + sy * L); x.stroke();
  }
  x.strokeStyle = 'rgba(238,240,244,.16)'; x.lineWidth = 1;
  for (let i = -6; i <= 6; i++) { const k = i % 3 === 0 ? 12 : 6; x.beginPath(); x.moveTo(W / 2 + i * 60, m); x.lineTo(W / 2 + i * 60, m + k); x.moveTo(W / 2 + i * 60, H - m); x.lineTo(W / 2 + i * 60, H - m - k); x.stroke(); }
  for (let i = -3; i <= 3; i++) { const k = i % 3 === 0 ? 12 : 6; x.beginPath(); x.moveTo(m, H / 2 + i * 60); x.lineTo(m + k, H / 2 + i * 60); x.moveTo(W - m, H / 2 + i * 60); x.lineTo(W - m - k, H / 2 + i * 60); x.stroke(); }
  x.restore();
  text('CONNECT THE BOTS // OWNER-CONTROLLED', 64, 80, 14, `500 ${MONO}`, C.muted, a, 'left', 'alphabetic', .14);
  text(fmtT(t), W - 64, 80, 14, `500 ${MONO}`, C.muted, a, 'right', 'alphabetic', .14);
  const blink = Math.floor(t * 2) % 2 ? 1 : .35;
  disc([W - 64 - 150, 75], 4, t >= 36 ? C.cob : '#d0574b', a * blink);
  text(t >= 36 ? 'LINK' : 'REC', W - 64 - 136, 80, 14, `500 ${MONO}`, C.muted, a, 'left', 'alphabetic', .14);
  let ch = null; for (const c of CHAPTERS) if (t >= c[0]) ch = c;
  if (ch) {
    text(`${ch[1]} // ${typed(ch[2], t, ch[0], 22)}`, 64, 108, 14, `500 ${MONO}`, C.cobInk, a, 'left', 'alphabetic', .2);
    const big = win(t, ch[0], ch[0] + 1.8, .05, .5) * a * (ch[0] < 1 ? 0 : 1);
    if (big > 0) text(`${ch[1]}`, 64, 168, 40, `500 ${MONO}`, C.cob, big * .9, 'left', 'alphabetic', .05);
  }
  text('ILLUSTRATION · FICTIONAL IDEAS', W - 64, H - 64, 13, `500 ${MONO}`, C.faint, a, 'right', 'alphabetic', .14);
}
function tacticalGrid(a) {
  if (a <= 0) return; x.save(); x.strokeStyle = 'rgba(142,160,255,.07)'; x.lineWidth = 1; x.globalAlpha = a; x.beginPath();
  for (let g = -2400; g <= 2400; g += 160) {
    const p1 = proj([g, -1400, 0]), p2 = proj([g, 1400, 0]), q1 = proj([-2400, g * .6, 0]), q2 = proj([2400, g * .6, 0]);
    if (p1 && p2) { x.moveTo(p1[0], p1[1]); x.lineTo(p2[0], p2[1]); }
    if (q1 && q2) { x.moveTo(q1[0], q1[1]); x.lineTo(q2[0], q2[1]); }
  }
  x.stroke(); x.restore();
}
function radar(t, pY, a) {
  if (a <= 0 || !pY) return;
  const R0 = 720 * pY[2], ang = (t - 24) / 2.4 * Math.PI * 2 - Math.PI / 2;
  x.save(); x.globalAlpha = a;
  x.strokeStyle = 'rgba(142,160,255,.16)'; x.lineWidth = 1; x.setLineDash([3, 7]);
  for (const k of [.33, .66, 1]) { x.beginPath(); x.arc(pY[0], pY[1], R0 * k, 0, 7); x.stroke(); }
  x.setLineDash([]);
  for (let i = 0; i < 24; i++) {
    const a0 = ang - i * .045; x.globalAlpha = a * .1 * (1 - i / 24);
    x.fillStyle = C.cob; x.beginPath(); x.moveTo(pY[0], pY[1]); x.arc(pY[0], pY[1], R0, a0 - .045, a0); x.closePath(); x.fill();
  }
  x.globalAlpha = a * .8; x.strokeStyle = C.cobInk; x.lineWidth = 1.5; x.beginPath(); x.moveTo(pY[0], pY[1]); x.lineTo(pY[0] + Math.cos(ang) * R0, pY[1] + Math.sin(ang) * R0); x.stroke();
  x.restore();
  return ang;
}
function shock(cx, cy, t, t0, maxR, a, lw = 2) {
  const k = span(t, t0, t0 + 1.3); if (k <= 0 || k >= 1) return;
  x.save(); x.globalAlpha = a * (1 - k) * .7; x.strokeStyle = C.cobInk; x.lineWidth = lw * (1 - k * .6);
  x.beginPath(); x.arc(cx, cy, easeOut(k) * maxR, 0, 7); x.stroke(); x.restore();
}

function renderAt(t) {
  CAM = camAt(t);
  { const sh = (t >= 36 ? Math.exp(-(t - 36) * 3.2) : 0) * CAM[2] * .012; CAM = [CAM[0] + Math.sin(t * 61) * sh, CAM[1] + Math.cos(t * 47) * sh, CAM[2]]; }
  x.setTransform(1, 0, 0, 1, 0, 0); x.globalAlpha = 1; x.fillStyle = C.bg; x.fillRect(0, 0, W, H);
  const out = 1 - span(t, 57.6, 59.2);                       // the story clears for the end card
  const hush = span(t, 33.6, 34.1) * (1 - span(t, 35.9, 36));  // everything but the two of you steps back
  const field = span(t, 36, 36.25);                            // the reveal arrives over at least 6 frames
  const fieldDim = t < 45.6 ? 1 : lerp(1, .28, ease(span(t, 45.6, 48.6)));

  // the one glow: blooms on the drop, stays behind the reveal and the end card
  glow(W / 2, H / 2, 1150, easeOut(span(t, 36, 37.4)) * (t < 57.6 ? 1 : lerp(1, .7, span(t, 57.6, 59.2))));

  tacticalGrid(span(t, 4.8, 8) * (1 - span(t, 36, 37)) * (1 - hush * .7));
  const pY0 = proj(YOU), sweep = radar(t, pY0, win(t, 24, 33.7, .8, .3));

  // Act 1: faint ideas at the edge of perception
  if (t < 9) for (const d of DRIFT) {
    const a = win(t, d.a, d.a + 3.6, 1.2, 1.4) * .55 * (1 - span(t, 4.8, 6));
    if (a <= 0) continue; x.save(); x.filter = 'blur(2.5px)';
    text(d.s, d.x + d.dx * t / 5, d.y + d.dy * t / 5, 30, serif(300) + SERIF, C.faint, a); x.restore();
  }
  text(typed('SOMEWHERE, SOMEONE HAS THE OTHER HALF OF YOUR IDEA.', t, 1.0, 26), W / 2, H / 2 + 8, 24, `400 ${MONO}`, C.muted, win(t, 1.0, 4.6, .9, .6), 'center', 'middle', .02);

  // the field (drawn under everything else)
  if (field > 0 && out > 0) {
    const fa = field * fieldDim * out;
    let reasons = 0;
    for (const P of PAIRS) {
      const a = proj(P.A), b = proj(P.B); if (!a || !b) continue;
      if ((a[0] < -60 && b[0] < -60) || (a[0] > W + 60 && b[0] > W + 60) || (a[1] < -60 && b[1] < -60) || (a[1] > H + 60 && b[1] > H + 60)) continue;
      const k = easeOut(span(t, P.at, P.at + .45)), len = Math.hypot(b[0] - a[0], b[1] - a[1]);
      const nodeA = .5 + .5 * k;
      ring(a, P.s * a[2], k > 0 ? C.body : C.faint, fa * nodeA); ring(b, P.s * b[2], k > 0 ? C.body : C.faint, fa * nodeA);
      if (k > 0 && len > 1) line(a, b, C.muted, fa * .62, len > 40 ? 1 : .7, null, k);
      if (P.why && k >= 1 && len > 120 && len < 1500 && reasons < 60) {
        reasons++; onLine(P.why, a, b, clamp(len * .07, 9, 24), C.body, fa * span(t, P.at + .3, P.at + .9) * .9, clamp(len * .02, 5, 10));
      }
    }
  }

  // Act 3-4: the other ideas, alone; then probes that reach and come back
  const fragsOut = (1 - hush * .82) * (1 - span(t, 36, 36.4));
  for (const f of FRAG) {
    const p = proj(f.p); if (!p) continue;
    const isPart = f.p === PART;
    const a = span(t, f.t, f.t + .5);
    const keep = isPart ? 1 : fragsOut;
    ring(p, 8 * p[2], C.ink, a * keep * out, 1.6);
    const fi = FRAG.indexOf(f) + 2, tag = `[${String(fi).padStart(2, '0')}] `;
    let ping = 0;
    if (sweep !== undefined && t > 24 && t < 33.7) { const fa = Math.atan2(f.p[1] - YOU[1], f.p[0] - YOU[0]); const d = ((sweep - fa) % (Math.PI * 2) + Math.PI * 2) % (Math.PI * 2); ping = Math.exp(-d / .5); }
    if (ping > .02) { x.save(); x.globalAlpha = ping * fragsOut; x.strokeStyle = C.cobInk; x.lineWidth = 1.5; x.beginPath(); x.arc(p[0], p[1], (10 + 14 * (1 - ping)) * p[2], 0, 7); x.stroke(); x.restore(); }
    text(tag + typed(f.s.toUpperCase(), t, f.t, 60), p[0] + 16 * p[2], p[1] + 6 * p[2], 15 * p[2], `500 ${MONO}`, ping > .3 ? C.cobInk : (isPart && t > 33.6 ? C.faint : C.body), a * fragsOut * out, 'left', 'alphabetic', .04);
  }
  if (t > 24 && t < 34.2) for (const pr of PROBES) {
    const g = span(t, pr.start, pr.start + 1.2), back = span(t, pr.start + 1.5, pr.start + 2.1);
    const k = easeOut(g) * (1 - ease(back)) * pr.reach; if (k <= 0) continue;
    const a = proj(pr.a), b = proj(pr.b); if (!a || !b) continue;
    line(a, b, C.faint, .9 * (1 - hush), 1.2, [5, 6], k);
  }

  // you, the hero idea and the one connection
  const pY = proj(YOU), pP = proj(PART);
  if (pY) {
    const youA = span(t, 4.8, 5.3) * out;
    const pulse = (t > 4.8 && t < 57.6 && !(t > 33.6 && t < 36)) ? beatPulse(t) : 0;
    const hr = 7 * pY[2] * (1 + .04 * pulse);
    const hg = x.createRadialGradient(pY[0], pY[1], 0, pY[0], pY[1], hr * 5);
    hg.addColorStop(0, `rgba(142,160,255,${.35 * youA * (1 + .5 * pulse)})`); hg.addColorStop(1, 'rgba(142,160,255,0)');
    x.fillStyle = hg; x.fillRect(pY[0] - hr * 5, pY[1] - hr * 5, hr * 10, hr * 10);
    disc(pY, hr, C.cob, youA);
    const heroA = span(t, 4.8, 5.4) * (1 - hush * .7) * (1 - span(t, 36, 36.4)) * out;
    text(typed(HERO, t, 4.8, 30), pY[0] + 18 * pY[2], pY[1] + 8 * pY[2], 25 * pY[2], serif(400) + SERIF, C.ink, heroA);
    text('[01] YOUR IDEA \u00b7 KEPT BY YOUR AGENT', pY[0] + 18 * pY[2], pY[1] + 30 * pY[2], 7.5 * pY[2], `500 ${MONO}`, C.muted, win(t, 6.0, 14.4, .6, .8), 'left', 'alphabetic', .08);
  }
  if (pY && pP) {
    // hush: one dashed line draws, finishing a beat before the drop
    const draw = ease(span(t, 34.0, 35.4));
    if (t < 36) line(pY, pP, C.muted, .9, 1.4, [6, 6], draw);
    if (t >= 36 && out > 0) {
      const open = easeOut(span(t, 51.0, 51.8));
      const lw = lerp(2, 5, open) * clamp(pY[2], .35, 1.2);
      line(pY, pP, open > 0 ? C.fill : C.cob, out, lw);
      if (open > 0) line(pY, pP, C.cob, out * (1 - open), lw);
      const len = Math.hypot(pP[0] - pY[0], pP[1] - pY[1]);
      onLine('your drones, their star tracker', pY, pP, clamp(len * .058, 0, 44), C.cobInk, out * span(t, 36, 36.3), clamp(len * .022, 4, 18));
    }
    ring(pP, 9 * pP[2], C.ink, out * (t > 33.6 ? 1 : 0), 2);
    // lock-on: brackets close in on the other person through the hush and snap shut on the drop
    const lockA = span(t, 34.0, 34.4) * (1 - span(t, 38.6, 39.4)) * out;
    if (lockA > 0) {
      const k = ease(span(t, 34.0, 35.9)), snap = t >= 36 ? 1 : 0;
      const half = lerp(96, 30, k) * pP[2] * (snap ? 1 : 1 + .06 * Math.sin(t * 18) * (1 - k)), arm = half * .42;
      x.globalAlpha = lockA; x.strokeStyle = snap ? C.cobInk : C.muted; x.lineWidth = snap ? 2.2 : 1.5;
      for (const [sx, sy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
        const cx = pP[0] + sx * half, cy = pP[1] + sy * half;
        x.beginPath(); x.moveTo(cx - sx * arm, cy); x.lineTo(cx, cy); x.lineTo(cx, cy - sy * arm); x.stroke();
      }
      x.globalAlpha = 1;
      text(snap ? 'FOUND \u00b7 BOTH SIDES DECIDE' : 'SEARCHING', pP[0], pP[1] + half + 22 * pP[2] * .9, 10 * pP[2], `500 ${MONO}`, snap ? C.cobInk : C.muted, lockA * (snap ? 1 : .5 + .5 * Math.round((t * 5) % 1)), 'center', 'alphabetic', .12);
    }
    // Act 7: two yeses, then the conversation opens
    const s = pY[2];
    const y1 = span(t, 49.2, 49.6) * out, y2 = span(t, 50.4, 50.8) * out;
    tick(pY[0] - 20 * s, pY[1] + 36 * s, 16 * s, C.good, y1); text('yes', pY[0] - 1 * s, pY[1] + 42 * s, 13 * s, `500 ${MONO}`, C.good, y1);
    tick(pP[0] - 20 * s, pP[1] + 38 * s, 16 * s, C.good, y2); text('yes', pP[0] - 1 * s, pP[1] + 44 * s, 13 * s, `500 ${MONO}`, C.good, y2);
    text('CONVERSATION OPEN', (pY[0] + pP[0]) / 2 + 34 * s, (pY[1] + pP[1]) / 2 + 26 * s, 9 * s, `500 ${MONO}`, C.cobInk, span(t, 51.6, 52.2) * out, 'center', 'alphabetic', .1);
    text('you', pY[0], pY[1] - 18 * s, 9 * s, `500 ${MONO}`, C.cob, span(t, 48.0, 48.6) * out, 'center');
  }

  if (pY && pP && t >= 36 && t < 46) {
    const cx0 = (pY[0] + pP[0]) / 2, cy0 = (pY[1] + pP[1]) / 2;
    shock(cx0, cy0, t, 36, 1500, 1, 3);
    for (let b = 1; b < 4; b++) shock(cx0, cy0, t, 36 + b * 2.4, 1100, .5, 1.5);
  }

  // statements and whispers (screen space)
  const s3 = win(t, 19.2, 24.0, .5, .5);
  shade(W / 2, 960, 760, 120, s3);
  text('EVERY IDEA IS HALF OF SOMETHING.', W / 2, 984, 60, `800 ${SANS}`, C.ink, s3, 'center', 'alphabetic', -.035);
  text('AGENTS READ ONLY WHAT EACH PERSON CHOSE TO SHARE', W / 2, 990, 22, `400 ${MONO}`, C.muted, win(t, 25.2, 33.2, .6, .5), 'center', 'alphabetic', .03);
  const s6 = win(t, 38.4, 45.4, .5, .7), s6b = win(t, 40.8, 45.4, .6, .7);
  shade(W / 2, 520, 900, 220, Math.max(s6, s6b));
  text('LIMITLESS CONNECTIONS.', W / 2, 525, 120, `800 ${SANS}`, C.ink, s6, 'center', 'alphabetic', -.035);
  text('found in what people chose to share.', W / 2, 600, 44, serif(400) + SERIF, C.body, s6b, 'center');
  const s7 = win(t, 52.2, 57.4, .6, .6);
  shade(W / 2, 930, 900, 140, s7);
  text('NOTHING OPENS WITHOUT BOTH PEOPLE SAYING YES.', W / 2, 950, 54, `800 ${SANS}`, C.ink, s7, 'center', 'alphabetic', -.035);
  text('YOUR AGENT FINDS. YOU CHOOSE.', W / 2, 1010, 22, `400 ${MONO}`, C.muted, win(t, 53.4, 57.4, .6, .6), 'center', 'alphabetic', .03);
  hud(t, span(t, .2, 1.0) * (1 - span(t, 57.6, 58.6)));

  // Act 8: the end card (also the poster and the reduced-motion still)
  if (t >= 58.4) {
    const mk = span(t, 58.6, 59.2), cy = 420, cx = W / 2;
    disc([cx - 70, cy], 20, C.fill, span(t, 58.4, 58.7));
    line([cx - 44, cy], [cx + 44, cy], C.ink, 1, 3, null, ease(mk));
    ring([cx + 70, cy], 18.5, C.ink, span(t, 59.0, 59.3), 3.5);
    text('Connect the Bots', cx, 560, 104, `800 ${SANS}`, C.ink, span(t, 59.4, 60.0), 'center', 'alphabetic', -.045);
    text('Connecting human intelligence', cx, 636, 44, serif(400) + SERIF, C.body, span(t, 60.2, 60.8), 'center');
    text('connectthebots.net', cx, 712, 26, `500 ${MONO}`, C.cob, span(t, 60.8, 61.4), 'center', 'alphabetic', .04);
    text('Music: \u201cCry\u201d by Sascha Ende \u00b7 ende.app \u00b7 CC BY 4.0', cx, 1010, 18, `400 ${MONO}`, C.muted, span(t, 61.2, 61.8), 'center', 'alphabetic', .02);
    text('ILLUSTRATION \u00b7 FICTIONAL IDEAS', cx, 1044, 13, `500 ${MONO}`, C.faint, span(t, 61.2, 61.8), 'center', 'alphabetic', .14);
  }
  grainAndScan(t, 1);
}

window.DUR = DUR;
window.renderAt = renderAt;
(async () => {
  await Promise.all([`800 20px ${SANS}`, `700 20px ${SANS}`, `italic 400 20px ${SERIF}`, `italic 300 20px ${SERIF}`, `400 20px ${MONO}`, `500 20px ${MONO}`].map(f => document.fonts.load(f)));
  renderAt(+(new URLSearchParams(location.search).get('t') || 67)); window.READY = true;
})();
