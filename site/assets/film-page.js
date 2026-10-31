// Frame-exact rendering for the video export. ?t=12.5 shows one frame; ?layout=tall uses the phone composition.
(function () {
  var params = new URLSearchParams(location.search);
  var layout = params.get("layout") || "wide";
  var L = window.CTBFilm.LAYOUTS[layout];
  var stage = document.getElementById("stage");
  stage.setAttribute("viewBox", "0 0 " + L.W + " " + L.H);
  window.seek = function (t) { stage.innerHTML = window.CTBFilm.render(t, layout); };
  var q = params.get("t");
  if (q !== null) { window.seek(parseFloat(q)); return; }
  var start = performance.now();
  (function loop(now) { window.seek(((now - start) / 1000) % (window.CTBFilm.DUR + 2)); requestAnimationFrame(loop); })(start);
})();
