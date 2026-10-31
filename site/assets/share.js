/* Dashboard sharing: copy the link, open the QR code, use the phone's own share
   sheet, and save the QR for Instagram. Every control is a plain link or button
   that works without this script; nothing here sends data anywhere. */
(function () {
  "use strict";
  var panel = document.querySelector("[data-share]");
  if (!panel) return;
  var toast = panel.querySelector("[data-toast]");
  var timer;
  function say(text) {
    if (!toast) return;
    toast.textContent = text;
    toast.classList.add("show");
    clearTimeout(timer);
    timer = setTimeout(function () { toast.classList.remove("show"); }, 4200);
  }
  function copy(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var area = document.createElement("textarea");
      area.value = text; area.setAttribute("readonly", ""); area.className = "sr-only";
      document.body.appendChild(area); area.select();
      var ok = document.execCommand && document.execCommand("copy");
      document.body.removeChild(area);
      ok ? resolve() : reject(new Error("copy failed"));
    });
  }
  var link = panel.getAttribute("data-share-url");
  var text = panel.getAttribute("data-share-text");

  panel.querySelectorAll("[data-copy]").forEach(function (button) {
    var label = button.querySelector("[data-copy-label]");
    button.addEventListener("click", function () {
      copy(button.getAttribute("data-copy")).then(function () {
        button.classList.add("copied");
        if (label) label.textContent = "Copied";
        say("Link copied. Paste it anywhere.");
        setTimeout(function () { button.classList.remove("copied"); if (label) label.textContent = "Copy link"; }, 2200);
      }, function () { say("Couldn't copy. Press and hold the link to copy it."); });
    });
  });

  var dialog = panel.querySelector("[data-qr-dialog]");
  panel.querySelectorAll("[data-qr-open]").forEach(function (button) {
    button.addEventListener("click", function () {
      if (dialog && dialog.showModal) dialog.showModal();
      else window.open(panel.querySelector(".qr-tile img").src, "_blank", "noopener");
    });
  });
  if (dialog) dialog.addEventListener("click", function (event) { if (event.target === dialog) dialog.close(); });

  if (navigator.share) {
    panel.querySelectorAll(".native-only").forEach(function (item) { item.hidden = false; });
    panel.querySelectorAll("[data-native-share]").forEach(function (button) {
      button.addEventListener("click", function () {
        navigator.share({ title: "Connect the Bots", text: text, url: link }).catch(function () {});
      });
    });
  }

  // Instagram has no share link. On phones that can share files, hand the QR
  // image to the share sheet (pick Instagram there). Otherwise save the image
  // and copy the link for a story link sticker or the bio.
  panel.querySelectorAll("[data-instagram]").forEach(function (anchor) {
    anchor.addEventListener("click", function (event) {
      copy(link).catch(function () {});
      if (!(navigator.canShare && window.File && window.fetch)) {
        say("Link copied and QR saved. In Instagram, add a story with the link sticker, or put the link in your bio.");
        return;
      }
      event.preventDefault();
      fetch(anchor.href.replace("?download=1", ""), { credentials: "same-origin" })
        .then(function (response) { return response.blob(); })
        .then(function (blob) {
          var file = new File([blob], "connect-the-bots-qr.png", { type: "image/png" });
          if (!navigator.canShare({ files: [file] })) throw new Error("files unsupported");
          say("Link copied. Choose Instagram, then add the link sticker.");
          return navigator.share({ files: [file], text: text + " " + link });
        })
        .catch(function (error) {
          if (error && error.name === "AbortError") return;
          window.location.href = anchor.href;
          say("Link copied and QR saved. In Instagram, add a story with the link sticker, or put the link in your bio.");
        });
    });
  });
})();
