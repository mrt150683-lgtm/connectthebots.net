/* Home: the film starts by itself, with sound where the browser allows it, unless the visitor
   asked for reduced motion or Save-Data, and plays once, resting on its end card. Browsers refuse
   sound before the visitor has touched the page, so then it plays muted and the first tap, click
   or key anywhere turns the sound on. Phones get the 480p file. */
(function () {
  "use strict";
  var video = document.querySelector("[data-trailer]");
  if (!video) return;
  var playBtn = document.querySelector("[data-play]");
  var soundBtn = document.querySelector("[data-sound]");
  var soundLabel = document.querySelector("[data-sound-label]");
  var replayBtn = document.querySelector("[data-replay]");

  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var conn = navigator.connection || {};
  var saveData = !!conn.saveData;
  var small = saveData || window.matchMedia("(max-width: 900px)").matches;
  var src = video.getAttribute(small ? "data-src-small" : "data-src-wide");
  var loaded = false;

  function load() {
    if (loaded) return;
    loaded = true;
    video.src = src;
    video.preload = "auto";
  }
  function showPlay(show) { playBtn.hidden = !show; }
  function setSound(on) {
    video.muted = !on;
    soundBtn.setAttribute("data-on", on ? "true" : "false");
    soundLabel.textContent = on ? "Sound off" : "Sound on";
  }
  function play() {
    load();
    if (video.ended) video.currentTime = 0;
    replayBtn.hidden = true;
    var p = video.play();
    if (p && p.catch) p.catch(function () { showPlay(true); });
    showPlay(false);
  }

  playBtn.addEventListener("click", function () { setSound(true); play(); });
  replayBtn.addEventListener("click", function () { play(); });
  soundBtn.addEventListener("click", function () {
    var on = video.muted;
    setSound(on);
    if (on && (video.paused || video.ended)) play();
  });
  // A tap on the film itself is the most natural way to ask for sound.
  video.addEventListener("click", function () {
    if (video.paused || video.ended) { setSound(true); play(); }
    else setSound(video.muted);
  });
  video.addEventListener("ended", function () { replayBtn.hidden = false; });

  // Sound is the default. Try with sound; if the browser refuses, play muted and turn the
  // sound on at the visitor's first gesture, unless they have already chosen with the button.
  var chose = false;
  soundBtn.addEventListener("click", function () { chose = true; }, true);
  function firstGesture(e) {
    stopListening();
    // The sound button and a tap on the film handle sound themselves; links leave the page.
    if (e.target && e.target.closest && e.target.closest("[data-sound],[data-trailer],[data-signin],a")) return;
    if (!chose && video.muted && !video.paused) setSound(true);
  }
  function stopListening() {
    ["pointerdown", "keydown", "touchend"].forEach(function (ev) { document.removeEventListener(ev, firstGesture, true); });
  }
  function autoplay() {
    load();
    setSound(true);
    var p = video.play();
    showPlay(false);
    if (p && p.catch) p.catch(function () {
      setSound(false);
      var q = video.play();
      if (q && q.catch) q.catch(function () { showPlay(true); });
      ["pointerdown", "keydown", "touchend"].forEach(function (ev) { document.addEventListener(ev, firstGesture, true); });
    });
  }

  setSound(false);
  if (reduced || saveData) {
    showPlay(true);
  } else {
    autoplay();
  }

  window.__home = { state: function () { return { src: video.currentSrc, paused: video.paused, muted: video.muted, ended: video.ended }; } };
})();
