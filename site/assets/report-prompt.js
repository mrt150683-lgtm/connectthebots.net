// Optional copy help. The readonly prompt and normal download form work without JS.
(function () {
  "use strict";
  var prompt = document.getElementById("personalised-prompt");
  var copy = document.querySelector("[data-copy-prompt]");
  var status = document.querySelector("[data-copy-prompt-status]");
  if (!prompt || !copy) return;
  copy.hidden = false;
  copy.addEventListener("click", async function () {
    try {
      if (!navigator.clipboard) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(prompt.value);
      if (status) status.textContent = "Copied. Keep this prompt private.";
    } catch (_) {
      prompt.focus();
      prompt.select();
      if (status) status.textContent = "Selected. Copy this prompt and keep it private.";
    }
  });
})();
