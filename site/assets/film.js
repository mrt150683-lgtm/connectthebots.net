/* Connect the Bots opening film, 15 seconds, drawn as SVG.
   Fragments of one person's work are organised by their agent; a different person's
   post appears elsewhere; one line with one reason forms between them; a quiet
   notification asks whether to talk. render(t, layout) is deterministic, so the page,
   the reduced-motion still and the exported video all come from the same code.
   Layouts: "wide" (1600x700, desktop) and "tall" (800x1000, phones). */
(function () {
  "use strict";
  var DUR = 15;
  var SANS = "'Schibsted Grotesk','Helvetica Neue',Arial,sans-serif";
  var SERIF = "Newsreader,Georgia,serif";
  var MONO = "'IBM Plex Mono',ui-monospace,Consolas,monospace";
  var PAPER = "#f5f4ef", INK = "#14181f", INK2 = "#3b414b", MUTED = "#5f6670", FAINT = "#8a9098", RULE = "#d8d6cd", COBALT = "#2443e0", SHEET = "#fbfbf8";

  var CHAPTERS = [
    { t: 0, label: "Fragments" },
    { t: 3, label: "Organised" },
    { t: 6, label: "Elsewhere" },
    { t: 9.6, label: "A reason" },
    { t: 12.4, label: "Your call" }
  ];

  function clamp(x) { return x < 0 ? 0 : x > 1 ? 1 : x; }
  function prog(t, a, b) { return clamp((t - a) / (b - a)); }
  function ease(x) { return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }
  function eout(x) { return 1 - Math.pow(1 - x, 3); }
  function lerp(a, b, k) { return a + (b - a) * k; }
  function n(v) { return Math.round(v * 10) / 10; }
  function o(v) { return clamp(v).toFixed(3); }

  function text(x, y, s, f) {
    f = f || {};
    var fam = f.mono ? MONO : f.serif ? SERIF : SANS;
    return '<text x="' + n(x) + '" y="' + n(y) + '" font-family="' + fam + '" font-size="' + (f.size || 20) + '" font-weight="' + (f.weight || 400) +
      '"' + (f.serif ? ' font-style="italic"' : "") + ' fill="' + (f.fill || INK) + '" opacity="' + o(f.op === undefined ? 1 : f.op) + '"' +
      (f.ls ? ' letter-spacing="' + f.ls + '"' : "") + (f.anchor ? ' text-anchor="' + f.anchor + '"' : "") +
      (f.rot ? ' transform="rotate(' + f.rot + " " + n(x) + " " + n(y) + ')"' : "") + ">" + s + "</text>";
  }

  /* Ama's fragments: scattered start (sx, sy, rot) and organised place (key into layout rows). */
  var FRAGS = [
    { s: "readings drift in clay soil", row: "c2", sx: 0.62, sy: 0.18, rot: -3 },
    { s: "someone who has shipped low-power firmware", row: "need", sx: 0.08, sy: 0.42, rot: 2 },
    { s: "Tuesdays only", row: "p1", sx: 0.55, sy: 0.80, rot: 4 },
    { s: "three working farms for field tests", row: "offer", sx: 0.30, sy: 0.66, rot: -2 },
    { s: "the v3 board drains its battery in 9 days", row: "need2", sx: 0.18, sy: 0.12, rot: 3 },
    { s: "grant deadline 12 Dec", row: "p2", sx: 0.05, sy: 0.88, rot: -4 },
    { s: "soil sensor, open hardware", row: "c1", sx: 0.48, sy: 0.50, rot: 1 }
  ];
  var JOON = [
    { s: "8 years of ultra-low-power firmware", match: true },
    { s: "wants real field data for a paper", match: true },
    { s: "about 5 hours a week, remote", match: false }
  ];

  var LAYOUTS = {
    wide: {
      W: 1600, H: 700, scatter: { x: 90, y: 80, w: 980, h: 540 },
      x0: 110, label: 110, rule: { x: 88, y1: 150, y2: 452 },
      rows: { need: [195, 22, INK], need2: [229, 18, MUTED], offer: [318, 22, INK], c1: [404, 18, MUTED], c2: [436, 18, MUTED], p1: [548, 15, FAINT], p2: [574, 15, FAINT] },
      heads: [["Needs", 166], ["Offers", 289], ["Context", 375], ["Kept private", 522]],
      you: [655, 262], them: [1030, 262], name: "below",
      jx: 1070, jlabel: 150, jrows: [200, 250, 296], jsize: [22, 22, 18],
      probes: [{ x: 880, y: 96, s: "needs a full-time hire" }, { x: 860, y: 590, s: "drone mapping, no firmware" }],
      why: { x: 842, y: 236, size: 36, anchor: "middle" },
      connectors: true,
      note: { x: 1176, y: 432, w: 380, h: 196 }
    },
    tall: {
      W: 800, H: 1000, scatter: { x: 30, y: 80, w: 470, h: 820 },
      x0: 48, label: 64, rule: { x: 30, y1: 98, y2: 372 },
      rows: { need: [140, 20, INK], need2: [168, 16, MUTED], offer: [234, 20, INK], c1: [300, 16, MUTED], c2: [326, 16, MUTED], p1: [398, 14, FAINT], p2: [420, 14, FAINT] },
      heads: [["Needs", 114], ["Offers", 208], ["Context", 274], ["Kept private", 374]],
      you: [400, 500], them: [400, 690], name: "side",
      jx: 48, jlabel: 760, jrows: [806, 846, 882], jsize: [20, 20, 16],
      probes: [{ x: 640, y: 560, s: "full-time only" }, { x: 150, y: 620, s: "no firmware" }],
      why: { x: 426, y: 606, size: 30, anchor: "start" },
      connectors: false,
      note: { x: 40, y: 24, w: 720, h: 150 }
    }
  };

  function fragments(t, L) {
    var s = "";
    var dimEnd = 1 - 0.55 * prog(t, 11.6, 12.4);
    for (var i = 0; i < FRAGS.length; i++) {
      var f = FRAGS[i], row = L.rows[f.row];
      var appear = eout(prog(t, 0.25 + i * 0.32, 0.85 + i * 0.32));
      var m = ease(prog(t, 3.0 + i * 0.14, 4.4 + i * 0.14));
      var sx = L.scatter.x + f.sx * L.scatter.w, sy = L.scatter.y + f.sy * L.scatter.h;
      var x = lerp(sx, L.x0, m), y = lerp(sy, row[0], m);
      var size = lerp(row[1] * (f.row === "need" || f.row === "offer" ? 1.45 : 1.3), row[1], m);
      var priv = f.row === "p1" || f.row === "p2";
      var fill = m > 0.5 ? row[2] : INK2;
      var matched = (f.row === "need" || f.row === "offer") && t > 9.7;
      if (matched) fill = COBALT;
      var op = appear * (priv ? lerp(1, 0.9, m) : 1) * (matched ? 1 : dimEnd);
      s += text(x, y + (1 - appear) * 10, f.s, { size: n(size), weight: f.row === "need" || f.row === "offer" ? 500 : 400, fill: fill, op: op, rot: n(f.rot * (1 - m)) });
    }
    // headings, agent's rule and the "kept private" lock appear as the agent organises
    var hO = prog(t, 4.4, 5.2) * dimEnd;
    for (var h = 0; h < L.heads.length; h++) s += text(L.x0, L.heads[h][1], L.heads[h][0].toUpperCase(), { mono: true, size: 12, fill: h === 3 ? FAINT : MUTED, ls: 1.2, op: hO });
    s += text(L.x0, L.label, "AMA · HER AGENT’S NOTES", { mono: true, size: 13, fill: INK, ls: 1.4, op: prog(t, 4.8, 5.4) * dimEnd, weight: 500 });
    var r = eout(prog(t, 3.0, 4.2)), rOff = prog(t, 5.6, 6.4);
    if (r > 0) s += '<line x1="' + L.rule.x + '" y1="' + L.rule.y1 + '" x2="' + L.rule.x + '" y2="' + n(lerp(L.rule.y1, L.rule.y2, r)) + '" stroke="' + COBALT + '" stroke-width="2" opacity="' + o(1 - 0.75 * rOff) + '"/>';
    return s;
  }

  function people(t, L) {
    var s = "";
    var y = eout(prog(t, 5.2, 5.9));
    var jo = eout(prog(t, 8.9, 9.6));
    var you = L.you, them = L.them;
    if (y > 0) {
      s += '<circle cx="' + you[0] + '" cy="' + you[1] + '" r="' + n(11 * y) + '" fill="' + COBALT + '"/>';
      s += L.name === "below"
        ? text(you[0], you[1] + 34, "Ama", { size: 16, anchor: "middle", fill: INK, weight: 600, op: y })
        : text(you[0] + 20, you[1] + 6, "Ama", { size: 16, fill: INK, weight: 600, op: y });
    }
    // Joon's public post, found elsewhere
    var po = prog(t, 6.0, 6.6);
    if (po > 0) {
      s += text(L.jx, L.jlabel, "JOON · PUBLIC POST, OPEN HARDWARE FORUM", { mono: true, size: 13, fill: INK, ls: 1.4, op: po, weight: 500 });
      for (var i = 0; i < JOON.length; i++) {
        var ro = eout(prog(t, 6.3 + i * 0.25, 6.9 + i * 0.25));
        var matched = JOON[i].match && t > 9.7;
        s += text(L.jx, L.jrows[i] + (1 - ro) * 8, JOON[i].s, { size: L.jsize[i], weight: matched ? 600 : JOON[i].match ? 500 : 400, fill: JOON[i].match ? INK : MUTED, op: ro * (matched ? 1 : 1 - 0.5 * prog(t, 11.6, 12.4)) });
      }
    }
    if (jo > 0 || t > 8.9) {
      s += '<circle cx="' + them[0] + '" cy="' + them[1] + '" r="' + n(9.5 * jo + 0.01) + '" fill="' + PAPER + '" stroke="' + INK + '" stroke-width="2.6"/>';
      s += L.name === "below"
        ? text(them[0], them[1] + 34, "Joon", { size: 16, anchor: "middle", fill: INK, weight: 600, op: jo })
        : text(them[0] + 20, them[1] + 6, "Joon", { size: 16, fill: INK, weight: 600, op: jo });
    }
    return s;
  }

  function search(t, L) {
    var s = "", you = L.you;
    var probes = [[6.4, 7.0, 7.3, 7.7], [7.6, 8.15, 8.35, 8.75]];
    for (var i = 0; i < 2; i++) {
      var p = L.probes[i], w = probes[i];
      var vis = prog(t, w[0] - 0.4, w[0]) * (1 - prog(t, w[3], w[3] + 0.6));
      if (vis > 0) {
        s += '<circle cx="' + p.x + '" cy="' + p.y + '" r="5" fill="' + PAPER + '" stroke="' + FAINT + '" stroke-width="1.5" opacity="' + o(vis) + '"/>';
        s += text(p.x + 14, p.y + 5, p.s, { size: 15, fill: FAINT, op: vis });
      }
      var out = ease(prog(t, w[0], w[1])), back = ease(prog(t, w[2], w[3]));
      if (out > 0 && back < 1) {
        var x1 = lerp(you[0], p.x, back), y1 = lerp(you[1], p.y, back);
        var x2 = lerp(you[0], p.x, out), y2 = lerp(you[1], p.y, out);
        s += '<line x1="' + n(x1) + '" y1="' + n(y1) + '" x2="' + n(x2) + '" y2="' + n(y2) + '" stroke="' + FAINT + '" stroke-width="1.2"/>';
      }
    }
    // the line that finds Joon
    var g = ease(prog(t, 8.6, 9.5));
    if (g > 0) {
      var thick = lerp(1.2, 2.6, prog(t, 10.8, 11.6));
      var tx = lerp(you[0], L.them[0], g), ty = lerp(you[1], L.them[1], g);
      s += '<line x1="' + you[0] + '" y1="' + you[1] + '" x2="' + n(tx) + '" y2="' + n(ty) + '" stroke="' + INK + '" stroke-width="' + n(thick) + '"/>';
    }
    // matched rows joined (wide layout only), then folded into the one line
    if (L.connectors) {
      var c = ease(prog(t, 9.7, 10.5)) * (1 - prog(t, 10.9, 11.6));
      if (c > 0) {
        var pairs = [[L.rows.need[0] - 7, L.jrows[0] - 7], [L.rows.offer[0] - 7, L.jrows[1] - 7]];
        var xa = 612, xb = L.jx - 14;
        for (var k = 0; k < 2; k++) {
          s += '<line x1="' + xa + '" y1="' + pairs[k][0] + '" x2="' + n(lerp(xa, xb, c)) + '" y2="' + n(lerp(pairs[k][0], pairs[k][1], c)) + '" stroke="' + COBALT + '" stroke-width="1.2" stroke-dasharray="3 5" opacity="' + o(c) + '"/>';
        }
      }
    }
    // the reason, set on the line
    var w = prog(t, 10.9, 11.9);
    if (w > 0) s += text(L.why.x, L.why.y + (1 - eout(w)) * 6, "her farms, his firmware", { serif: true, size: L.why.size, anchor: L.why.anchor, fill: INK, op: w });
    return s;
  }

  function notice(t, L) {
    var k = eout(prog(t, 12.4, 13.0));
    if (k <= 0) return "";
    var b = L.note, y = b.y + (1 - k) * -10;
    var s = '<g opacity="' + o(k) + '">';
    s += '<rect x="' + b.x + '" y="' + n(y) + '" width="' + b.w + '" height="' + b.h + '" fill="' + SHEET + '" stroke="' + INK + '" stroke-width="1.5"/>';
    s += '<rect x="' + b.x + '" y="' + n(y) + '" width="' + b.w + '" height="3" fill="' + COBALT + '"/>';
    var px = b.x + 22;
    s += text(px, y + 34, "CONNECT THE BOTS · JUST NOW", { mono: true, size: 11.5, fill: MUTED, ls: 1.2 });
    s += text(px, y + 66, "One person worth meeting", { size: 21, weight: 600 });
    s += text(px, y + 94, "Joon · ultra-low-power firmware", { size: 16, fill: INK2 });
    if (L.name === "below") {
      s += '<rect x="' + px + '" y="' + n(y + 122) + '" width="118" height="42" rx="3" fill="' + COBALT + '"/>' + text(px + 59, y + 149, "See why", { size: 16, weight: 600, fill: "#ffffff", anchor: "middle" });
      s += '<rect x="' + (px + 130) + '" y="' + n(y + 122) + '" width="96" height="42" rx="3" fill="none" stroke="' + INK + '" stroke-width="1.5"/>' + text(px + 178, y + 149, "Later", { size: 16, weight: 600, anchor: "middle" });
    } else {
      s += '<rect x="' + (b.x + b.w - 250) + '" y="' + n(y + 88) + '" width="118" height="42" rx="3" fill="' + COBALT + '"/>' + text(b.x + b.w - 191, y + 115, "See why", { size: 16, weight: 600, fill: "#ffffff", anchor: "middle" });
      s += '<rect x="' + (b.x + b.w - 120) + '" y="' + n(y + 88) + '" width="96" height="42" rx="3" fill="none" stroke="' + INK + '" stroke-width="1.5"/>' + text(b.x + b.w - 72, y + 115, "Later", { size: 16, weight: 600, anchor: "middle" });
    }
    return s + "</g>";
  }

  function render(t, layout) {
    var L = LAYOUTS[layout] || LAYOUTS.wide;
    t = Math.max(0, Math.min(DUR, t));
    return '<rect width="' + L.W + '" height="' + L.H + '" fill="' + PAPER + '"/>' + fragments(t, L) + search(t, L) + people(t, L) + notice(t, L);
  }

  window.CTBFilm = { render: render, DUR: DUR, CHAPTERS: CHAPTERS, LAYOUTS: LAYOUTS };
})();
