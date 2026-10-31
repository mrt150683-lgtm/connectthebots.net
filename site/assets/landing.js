/* Landing page: the opening film (plays once, then rests on its final frame) and the prompt panel. */
(function () {
  "use strict";
  var F = window.CTBFilm;
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- Film ---------- */
  var film = document.querySelector("[data-film]");
  if (film && F) {
    var svg = film.querySelector("svg");
    var title = svg.querySelector("title");
    var playBtn = film.querySelector("[data-play]");
    var replayBtn = film.querySelector("[data-replay]");
    var bar = film.querySelector("[data-progress]");
    var chapterEl = film.querySelector("[data-chapter]");
    var narrow = window.matchMedia("(max-width: 820px)");
    var layout = "wide", t = 0, playing = false, last = 0, started = false;

    function setLayout() {
      layout = narrow.matches ? "tall" : "wide";
      var L = F.LAYOUTS[layout];
      svg.setAttribute("viewBox", "0 0 " + L.W + " " + L.H);
      draw();
    }
    function draw() {
      svg.innerHTML = "";
      svg.appendChild(title);
      svg.insertAdjacentHTML("beforeend", F.render(t, layout));
      bar.style.width = (t / F.DUR * 100) + "%";
      var label = F.CHAPTERS[0].label;
      F.CHAPTERS.forEach(function (c) { if (t >= c.t) label = c.label; });
      chapterEl.textContent = label;
    }
    function setButton(isPlaying) {
      playBtn.querySelector("span").textContent = isPlaying ? "Pause" : "Play";
      playBtn.setAttribute("aria-label", isPlaying ? "Pause film" : "Play film");
      playBtn.querySelector("svg").innerHTML = isPlaying
        ? '<rect x="3" y="2" width="3.5" height="12" fill="currentColor"/><rect x="9.5" y="2" width="3.5" height="12" fill="currentColor"/>'
        : '<path d="M4 2.5v11l9-5.5z" fill="currentColor"/>';
    }
    function frame(now) {
      if (!playing) return;
      t += Math.min(0.05, (now - last) / 1000); last = now;
      if (t >= F.DUR) { t = F.DUR; draw(); stop(); return; }
      draw(); requestAnimationFrame(frame);
    }
    function play() {
      if (t >= F.DUR) t = 0;
      playing = true; started = true; setButton(true);
      last = performance.now(); requestAnimationFrame(frame);
    }
    function stop() { playing = false; setButton(false); }

    playBtn.addEventListener("click", function () { playing ? stop() : play(); });
    replayBtn.addEventListener("click", function () { t = 0; draw(); play(); });
    document.addEventListener("visibilitychange", function () { if (document.hidden && playing) stop(); });
    if (narrow.addEventListener) narrow.addEventListener("change", setLayout);

    if (reduced) {
      // Still image: the final composition, with a play button for people who want the motion.
      t = F.DUR; setLayout(); setButton(false);
    } else {
      setLayout(); setButton(false);
      if ("IntersectionObserver" in window) {
        var io = new IntersectionObserver(function (entries) {
          entries.forEach(function (e) { if (e.isIntersecting && !started) { play(); io.disconnect(); } });
        }, { threshold: 0.35 });
        io.observe(svg);
      } else play();
    }
    window.__film = { seek: function (v) { stop(); t = v; draw(); }, state: function () { return { t: t, playing: playing, layout: layout }; } };
  }

  /* ---------- Prompt panel ---------- */
  var pre = document.querySelector("[data-prompt-text]");
  var text = window.CTB_PROMPT || "";
  if (pre && text) {
    pre.textContent = text;
    var lines = document.querySelector("[data-prompt-lines]");
    if (lines) lines.textContent = text.split("\n").length + " lines";
  }
  function remember() { try { localStorage.setItem("ctb-copied", "1"); } catch (e) { /* storage blocked */ } }
  var dl = document.querySelector("[data-download]");
  if (dl) dl.addEventListener("click", remember);
  var copyBtn = document.querySelector("[data-copy]");
  var status = document.querySelector("[data-copied]");
  if (copyBtn) {
    copyBtn.addEventListener("click", function () {
      function selected() {
        var r = document.createRange(); r.selectNodeContents(pre);
        var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
        status.textContent = "Selected. Press Ctrl+C or ⌘C to copy.";
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () {
          status.textContent = "Copied. Paste it into your assistant.";
          remember();
        }, selected);
      } else selected();
    });
  }
})();
