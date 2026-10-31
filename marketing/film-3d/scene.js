import * as THREE from './vendor/three.module.js';

// Deterministic: every frame is a pure function of t (seconds). Nothing reads the clock.
const W = 1920, H = 1080, DUR = 46;
const PAPER = new THREE.Color('#f5f4ef'), INK = new THREE.Color('#272c35'), COBALT = new THREE.Color('#2443e0'),
  GREY = new THREE.Color('#c9c7bd'), JOONINK = new THREE.Color('#14181f');

function rng(seed) { return () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
const R = rng(20261030);
const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lerp = (a, b, x) => a + (b - a) * x;
const ease = x => { x = clamp(x); return x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; };
const easeOut = x => 1 - Math.pow(1 - clamp(x), 3);
const span = (t, a, b) => clamp((t - a) / (b - a));
const win = (t, a, b, fi = .45, fo = .45) => Math.min(span(t, a, a + fi), 1 - span(t, b - fo, b));
const V = (x, y, z) => new THREE.Vector3(x, y, z);

const renderer = new THREE.WebGLRenderer({ canvas: document.getElementById('gl'), antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(1); renderer.setSize(W, H, false); renderer.setClearColor(PAPER);
const scene = new THREE.Scene();
scene.fog = new THREE.Fog(PAPER, 20, 200);
const camera = new THREE.PerspectiveCamera(42, W / H, 0.5, 3000);
scene.add(new THREE.HemisphereLight(0xffffff, 0xcfcabb, 2.2));
const sun = new THREE.DirectionalLight(0xffffff, 1.6); sun.position.set(-120, 260, 140); scene.add(sun);
const world = new THREE.Group(); scene.add(world);

// ---------- People: a wide, quiet field of nodes ----------
const N = 1500, pos = [], size = [];
const JOON = V(84, 12, -40);
while (pos.length < N) {
  const a = R() * Math.PI * 2, r = 270 * Math.sqrt(R());
  const y = (R() + R() + R() - 1.5) * 44 * (1 - r / 400);
  const p = V(Math.cos(a) * r, y, Math.sin(a) * r);
  if (p.length() < 16 || p.distanceTo(JOON) < 9) continue;
  pos.push(p); size.push(0.7 + Math.pow(R(), 2) * 1.4);
}
const JI = pos.length; pos.push(JOON.clone()); size.push(2.4);
const nodes = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 18, 14), new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: .5, metalness: 0 }), pos.length);
const m4 = new THREE.Matrix4();
pos.forEach((p, i) => { m4.makeScale(size[i], size[i], size[i]).setPosition(p); nodes.setMatrixAt(i, m4); nodes.setColorAt(i, INK); });
world.add(nodes);
const baseColor = i => INK;

// faint web between near neighbours
{
  const seg = [];
  for (let i = 0; i < pos.length; i++) {
    let best = [];
    for (let j = 0; j < pos.length; j++) if (j !== i) { const d = pos[i].distanceToSquared(pos[j]); if (d < 900) best.push([d, j]); }
    best.sort((a, b) => a[0] - b[0]);
    for (const [, j] of best.slice(0, 2)) if (j > i) seg.push(pos[i].x, pos[i].y, pos[i].z, pos[j].x, pos[j].y, pos[j].z);
  }
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(seg, 3));
  world.add(new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: 0xa9a79c, transparent: true, opacity: .55 })));
}

// ---------- You ----------
const you = new THREE.Mesh(new THREE.SphereGeometry(3.4, 40, 30), new THREE.MeshStandardMaterial({ color: COBALT, emissive: COBALT, emissiveIntensity: .35, roughness: .35 }));
world.add(you);
function glowTex(rgb) {
  const c = document.createElement('canvas'); c.width = c.height = 256; const x = c.getContext('2d');
  const g = x.createRadialGradient(128, 128, 0, 128, 128, 128);
  g.addColorStop(0, `rgba(${rgb},.42)`); g.addColorStop(.35, `rgba(${rgb},.16)`); g.addColorStop(1, `rgba(${rgb},0)`);
  x.fillStyle = g; x.fillRect(0, 0, 256, 256); const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}
const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex('36,67,224'), transparent: true, depthWrite: false }));
world.add(halo);
const rings = [0, 1, 2].map(() => { const m = new THREE.Mesh(new THREE.RingGeometry(.96, 1, 96), new THREE.MeshBasicMaterial({ color: COBALT, transparent: true, side: THREE.DoubleSide, depthWrite: false })); world.add(m); return m; });

// ---------- Probes: out to other people's agents ----------
function arc(a, b, lift, side) {
  const mid = a.clone().add(b).multiplyScalar(.5), len = a.distanceTo(b);
  const perp = V(-(b.z - a.z), 0, b.x - a.x).normalize().multiplyScalar(side * len);
  mid.y += lift * len; mid.add(perp);
  return new THREE.QuadraticBezierCurve3(a.clone(), mid, b.clone());
}
const SEG = 90, RAD = 8, PER = RAD * 6;
function tube(curve, r, color, opacity = 1) {
  const m = new THREE.Mesh(new THREE.TubeGeometry(curve, SEG, r, RAD, false), new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthWrite: false }));
  m.geometry.setDrawRange(0, 0); world.add(m); return m;
}
const grow = (m, a, b) => { // draw the tube from fraction a to b
  const i0 = Math.floor(clamp(a) * SEG), i1 = Math.ceil(clamp(b) * SEG);
  m.geometry.setDrawRange(i0 * PER, Math.max(0, i1 - i0) * PER); m.visible = i1 > i0;
};

const cand = pos.map((p, i) => [i, p]).filter(([i, p]) => i !== JI && p.length() > 45 && p.length() < 150);
const targets = [];
for (let k = 0; k < 47; k++) {
  const want = (k / 47) * Math.PI * 2 + 0.3;
  let best = null, bd = 1e9;
  for (const [i, p] of cand) { if (targets.includes(i)) continue; let da = Math.abs(Math.atan2(p.z, p.x) - want); da = Math.min(da, Math.PI * 2 - da); const s = da * 80 + Math.abs(p.length() - (60 + (k * 37 % 80))); if (s < bd) { bd = s; best = i; } }
  targets.push(best);
}
targets.splice(17, 0, JI);
const SURV = new Set([JI, targets[6], targets[33]]);
const ORIGIN = V(0, 0, 0);
const probes = targets.map((ti, k) => {
  const c = arc(ORIGIN, pos[ti], .22 + R() * .14, (R() - .5) * .18);
  const launch = 12.7 + k * (5.0 / targets.length) + (ti === JI ? 0 : 0);
  return { ti, k, c, launch, m: tube(c, ti === JI ? .42 : .32, COBALT), surv: SURV.has(ti), fade: 18.7 + (k * 7 % 48) / 48 * 2.4 };
});
const joonP = probes.find(p => p.ti === JI);
const joonThick = tube(joonP.c, .8, COBALT);
const pulses = [];
for (const p of probes) if (p.surv) for (let q = 0; q < 3; q++) { const s = new THREE.Mesh(new THREE.SphereGeometry(1.2, 16, 12), new THREE.MeshBasicMaterial({ color: COBALT, transparent: true })); world.add(s); pulses.push({ p, q, s }); }
const lastPulses = [0, 1, 2, 3, 4, 5].map(q => { const s = new THREE.Mesh(new THREE.SphereGeometry(1.5, 16, 12), new THREE.MeshBasicMaterial({ color: COBALT, transparent: true })); world.add(s); return { q, s }; });

// Joon's ring: an ink ring that fills cobalt when he says yes
const joonRing = new THREE.Mesh(new THREE.RingGeometry(4.6, 5.3, 96), new THREE.MeshBasicMaterial({ color: JOONINK, transparent: true, side: THREE.DoubleSide, depthWrite: false }));
world.add(joonRing); joonRing.position.copy(JOON);
const joonHalo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex('36,67,224'), transparent: true, depthWrite: false, opacity: 0 }));
joonHalo.position.copy(JOON); world.add(joonHalo);

// ---------- The ending: everyone else connecting too ----------
const others = [];
{
  const r2 = rng(77);
  let guard = 0;
  while (others.length < 80 && guard++ < 20000) {
    const a = Math.floor(r2() * (pos.length - 1)); if (a === JI || pos[a].length() < 30) continue;
    let b = -1, bd = 1e9;
    for (let j = 0; j < pos.length - 1; j++) { const d = pos[a].distanceTo(pos[j]); if (d > 22 && d < 60 && Math.abs(d - 40) < bd && r2() < .5) { bd = Math.abs(d - 40); b = j; } }
    if (b < 0) continue;
    const c = arc(pos[a], pos[b], .32, 0);
    others.push({ a, b, m: tube(c, .55, COBALT), start: 38.9 + r2() * 4.6 });
  }
}

// ---------- Camera ----------
const orbit = (a, r, h) => V(Math.sin(a) * r, h, Math.cos(a) * r);
const mixS = (A, B, w) => ({ p: A.p.clone().lerp(B.p, w), l: A.l.clone().lerp(B.l, w) });
function flyState(t) {
  const u = lerp(.04, .6, ease(span(t, 23.4, 29.2)));
  const c = joonP.c, p = c.getPoint(u), ahead = c.getPoint(Math.min(1, u + .3));
  const dir = ahead.clone().sub(p).normalize(), side = V(-dir.z, 0, dir.x).normalize();
  return { p: p.clone().add(side.multiplyScalar(-26)).add(V(0, 15, 0)), l: ahead.lerp(JOON, .4) };
}
function sideState(t) {
  const mid = joonP.c.getPoint(.5), dir = JOON.clone().normalize(), side = V(-dir.z, 0, dir.x);
  const drift = (t - 28) * .9;
  return { p: mid.clone().add(side.multiplyScalar(132 + drift)).add(V(0, 34 + drift * .2, 0)).add(dir.clone().multiplyScalar(-6)), l: joonP.c.getPoint(.42).add(V(0, -6, 0)) };
}
function camState(t) {
  const K0 = { p: V(-26, 5, 172), l: V(-4, 0, 60) }, K1 = { p: V(-6, 2, 108), l: V(6, 0, 0) };
  const K2 = { p: V(0, 160, 380), l: V(0, -10, 0) };
  const orb = tt => { const w = span(tt, 12.6, 23.4); return { p: orbit(lerp(.42, 1.02, w), lerp(178, 150, ease(w)), lerp(64, 52, w)), l: V(22, 0, -12) }; };
  const END = { p: V(10, 360, 470), l: V(0, -20, 0) };
  if (t < 5.5) return mixS(K0, K1, ease(t / 5.5) * .85 + t / 5.5 * .15);
  if (t < 9.2) return mixS(K1, K2, ease(span(t, 5.5, 9.2)));
  if (t < 12.6) return mixS(K2, orb(12.6), ease(span(t, 9.2, 12.6)));
  if (t < 23.4) return orb(t);
  if (t < 27.2) return mixS(orb(23.4), flyState(t), ease(span(t, 23.4, 25.2)));
  if (t < 38.3) return mixS(flyState(t), sideState(t), ease(span(t, 27.2, 30)));
  const e = mixS(sideState(38.3), END, ease(span(t, 38.3, 43.6)));
  e.p.y += Math.max(0, t - 43.6) * 3; return e;
}

// ---------- Overlays ----------
const $ = id => document.getElementById(id);
const show = (id, o, dy = 0) => { const e = $(id); e.style.opacity = o.toFixed(3); if (dy) e.style.transform = `translateY(${dy.toFixed(1)}px)`; };
const CAPS = [['c0', .7, 5.2], ['c1', 5.5, 9.0], ['c2', 9.3, 12.4], ['c3', 12.6, 18.3], ['c4', 18.6, 23.0], ['c5', 23.3, 27.9], ['c6', 31.6, 37.8]];
const tmp = new THREE.Vector3();
function screen(v) { tmp.copy(v); world.localToWorld(tmp); tmp.project(camera); return [(tmp.x + 1) / 2 * W, (1 - tmp.y) / 2 * H, tmp.z < 1]; }
let viewShift = 0;
function place(id, v, dy, o) { const [x, y, ok] = screen(v); const e = $(id); e.style.left = x + 'px'; e.style.top = (y + dy) + 'px'; e.style.opacity = ok ? o.toFixed(3) : 0; }

function update(t) {
  world.rotation.y = t * .012;
  // camera and framing; the card scene shifts the picture left
  viewShift = 320 * ease(span(t, 31.0, 32.6)) * (1 - ease(span(t, 37.8, 39.4)));
  camera.setViewOffset(W, H, viewShift, 0, W, H);
  const s = camState(t); camera.position.copy(s.p); camera.lookAt(s.l); camera.updateMatrixWorld();
  // fog: close and misty inside the crowd, wide when we pull back
  const f = t < 5.5 ? [12, 190] : t < 9.2 ? [lerp(12, 220, ease(span(t, 5.5, 9.2))), lerp(190, 980, ease(span(t, 5.5, 9.2)))]
    : t < 38.3 ? [lerp(220, 90, ease(span(t, 9.2, 13))), lerp(980, 520, ease(span(t, 9.2, 13)))]
    : [lerp(90, 330, ease(span(t, 38.3, 43.6))), lerp(520, 1100, ease(span(t, 38.3, 43.6)))];
  scene.fog.near = f[0]; scene.fog.far = f[1];

  // you
  const ig = easeOut(span(t, 9.3, 10.4)), bounce = 1 + Math.sin(span(t, 9.3, 10.6) * Math.PI) * .25;
  you.scale.setScalar(Math.max(.001, ig * bounce)); halo.scale.setScalar(46 * ig * (1 + .06 * Math.sin(t * 2.2)) * (1 + beat * .14)); halo.material.opacity = ig;
  rings.forEach((rm, i) => {
    const per = 2.4, ph = ((t - 9.4 - i * .8) % per + per) % per / per, on = t > 9.4 + i * .8 && t < 23.2 ? 1 : 0;
    rm.scale.setScalar(4 + ph * 26); rm.material.opacity = on * (1 - ph) * .5; rm.quaternion.copy(camera.quaternion);
  });

  // probes
  let spoken = 0;
  for (const p of probes) {
    const g = easeOut(span(t, p.launch, p.launch + 1.0));
    if (t >= p.launch + 1.0) spoken++;
    const fadeT = p.surv ? (p.ti === JI ? 99 : 24.0) : p.fade;
    const fd = span(t, fadeT, fadeT + .7), rt = ease(span(t, fadeT + .5, fadeT + 1.6));
    p.m.material.color.copy(COBALT).lerp(GREY, fd);
    p.m.material.opacity = p.ti === JI ? 1 : lerp(.9, .5, fd) * (1 - rt);
    grow(p.m, rt, g);
    const hit = span(t, p.launch + .9, p.launch + 1.3);
    const col = p.surv && t > 19 ? COBALT.clone().lerp(INK, span(t, fadeT, fadeT + .6)) : INK.clone().lerp(COBALT, Math.sin(hit * Math.PI) * .9).lerp(INK, 0);
    if (p.ti !== JI) nodes.setColorAt(p.ti, t > p.launch + 1 && t < fadeT && p.surv ? COBALT : col);
  }
  grow(joonThick, 0, ease(span(t, 23.5, 25.0)));
  // Joon: ink node, then cobalt when he says yes
  const yes = span(t, 35.0, 35.6);
  nodes.setColorAt(JI, INK.clone().lerp(COBALT, Math.max(t > joonP.launch + 1 && t < 27 ? .0 : 0, yes)));
  nodes.instanceColor.needsUpdate = true;
  for (const u of pulses) {
    const st = 19.2 + u.q * .55 + (u.p.k % 5) * .12, x = span(t, st, st + 1.3);
    u.s.visible = x > 0 && x < 1 && t < 24.5; u.s.position.copy(u.p.c.getPoint(1 - easeOut(x)));
  }
  for (const u of lastPulses) {
    const per = 1.6, st = 36.2 + u.q * .4, ph = ((t - st) % per + per) % per / per, dir = u.q % 2;
    u.s.visible = t > st && t < 39.4; u.s.position.copy(joonP.c.getPoint(dir ? 1 - ph : ph)); u.s.material.opacity = 1 - span(t, 38.6, 39.4);
  }
  joonRing.quaternion.copy(camera.quaternion);
  joonRing.scale.setScalar(Math.max(.001, easeOut(span(t, 24.4, 25.4)) * (1 + .08 * Math.sin(span(t, 35, 36) * Math.PI))));
  joonRing.material.color.copy(JOONINK).lerp(COBALT, yes);
  joonRing.material.opacity = 1 - span(t, 38.4, 39.4);
  joonHalo.scale.setScalar(36 * yes); joonHalo.material.opacity = yes * (1 - span(t, 38.4, 39.4));

  // everyone else
  for (const o of others) {
    const g = easeOut(span(t, o.start, o.start + 1.3)); grow(o.m, 0, g); o.m.material.opacity = .85;
  }
  for (const o of others) if (t > o.start + 1.1) { nodes.setColorAt(o.a, COBALT); nodes.setColorAt(o.b, COBALT); }

  // overlays
  show('brand', win(t, .4, 38.4, .8, .6));
  show('hud', win(t, 12.7, 23.9, .6, .6));
  $('nSpoken').textContent = spoken;
  $('nBoth').textContent = t < 19.2 ? '–' : Math.min(3, Math.floor(span(t, 19.2, 21.6) * 3 + .001) + (t >= 21.6 ? 0 : 0));
  for (const [id, a, b] of CAPS) { const o = win(t, a, b, .55, .45); show(id, o, (1 - easeOut(span(t, a, a + .7))) * 18); }
  show('fict', win(t, 23.6, 38.2, .6, .6));
  place('tYou', ORIGIN, 34, win(t, 27.6, 38.0, .5, .5));
  place('tThem', JOON, 34, win(t, 27.8, 38.0, .5, .5));
  place('why', joonP.c.getPoint(.5), 0, win(t, 28.4, 38.0, .6, .6));
  const ck = win(t, 31.8, 38.0, .6, .6); show('card', ck, (1 - easeOut(span(t, 31.8, 32.6))) * 26);
  $('askBtn').classList.toggle('on', t > 33.6);
  $('s1').classList.toggle('on', t > 33.7); $('s2').classList.toggle('on', t > 35.0); $('s3').classList.toggle('on', t > 36.2);
  show('end', easeOut(span(t, 38.4, 39.6)));
  show('word', easeOut(span(t, 38.8, 39.6)), (1 - easeOut(span(t, 38.8, 39.8))) * 22);
  show('tl', easeOut(span(t, 40.4, 41.3)), (1 - easeOut(span(t, 40.4, 41.4))) * 16);
  show('url', easeOut(span(t, 42.0, 42.8)));
  show('next', easeOut(span(t, 42.7, 43.5)));
  show('credit', CUT ? easeOut(span(t, 42.7, 43.5)) : 0);
}

// Cuts re-time the same film to a soundtrack: [cut time, film time] pairs, linear between them.
// "music" fits "Cry" by Sascha Ende (CC BY 4.0) from 86.4 s: breakdown, build, a 2.4 s hush, then the drop at 36 s.
const CUTS = {
  music: { dur: 66, bpm: 100, beatsFrom: 14.4, beatsTo: 52.8, hush: [33.6, 36],
    map: [[0, 0], [6, 5.5], [13, 9.2], [14.4, 9.3], [19.2, 12.6], [33, 18.6], [35.98, 22.6], [36, 23.4], [45.6, 31.6], [52.8, 38.3], [66, 46]] },
};
const CUT = CUTS[new URLSearchParams(location.search).get('cut')] || null;
let beat = 0;
function filmTime(T) {
  if (!CUT) return T;
  const m = CUT.map;
  for (let i = 1; i < m.length; i++) if (T <= m[i][0]) return lerp(m[i - 1][1], m[i][1], (T - m[i - 1][0]) / (m[i][0] - m[i - 1][0]));
  return m[m.length - 1][1];
}
function beatAt(T) {
  if (!CUT || T < CUT.beatsFrom || T > CUT.beatsTo || (T > CUT.hush[0] && T < CUT.hush[1])) return 0;
  const per = 60 / CUT.bpm, ph = ((T % per) + per) % per;
  return Math.exp(-ph * 9);
}

window.DUR = CUT ? CUT.dur : DUR;
window.renderAt = T => { beat = beatAt(T); update(filmTime(T)); renderer.render(scene, camera); return true; };
document.fonts.ready.then(() => { window.renderAt(0); window.READY = true; });
