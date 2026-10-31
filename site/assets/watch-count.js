/* Film play counter. Posts an empty request to this site when a film starts,
   gets its sound turned on, or reaches the end, once each per page load.
   No cookies, identifiers or third parties; the server only counts these in its
   shortened access log. Skipped when the browser sends Global Privacy Control
   or Do Not Track. */
(function () {
  "use strict";
  if (navigator.globalPrivacyControl === true || navigator.doNotTrack === "1") return;
  function send(path) {
    try {
      if (navigator.sendBeacon) navigator.sendBeacon(path);
      else if (window.fetch) fetch(path, { method: "POST", keepalive: true, credentials: "omit" });
    } catch (e) { /* counting must never break the film */ }
  }
  var films = document.querySelectorAll("video[data-trailer]");
  Array.prototype.forEach.call(films, function (video) {
    var sent = {};
    function once(path) { if (!sent[path]) { sent[path] = true; send(path); } }
    video.addEventListener("playing", function () { once("/v/play"); });
    video.addEventListener("volumechange", function () { if (!video.muted && !video.paused) once("/v/sound"); });
    video.addEventListener("playing", function () { if (!video.muted) once("/v/sound"); });
    video.addEventListener("ended", function () { once("/v/done"); });
  });
})();
