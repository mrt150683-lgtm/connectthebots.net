/* Dashboard prototype. Everything here is a labelled, fictional example for one owner (Ama):
   nothing is searched, scheduled, sent or delivered. Text is rendered as text nodes only.
   Views are chosen by the URL hash: a state (#setup, #working, #quiet, #question,
   #connection, #paused, #failed), a connection (#joon) or the conversation (#talk). */
(function () {
  "use strict";

  /* ---------- tiny DOM helpers ---------- */
  function h(tag, attrs) {
    var el = document.createElement(tag);
    var kids = Array.prototype.slice.call(arguments, 2);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v == null || v === false) return;
      if (k === "class") el.className = v;
      else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    });
    kids.forEach(function add(c) {
      if (c == null || c === false) return;
      if (Array.isArray(c)) return c.forEach(add);
      el.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return el;
  }
  function $(s) { return document.querySelector(s); }
  function block(title, meta) {
    var kids = Array.prototype.slice.call(arguments, 2);
    return h("section", { class: "block", "aria-label": title },
      h("div", { class: "block-head" }, h("h2", {}, title), meta ? h("span", { class: "meta" }, meta) : null), kids);
  }
  function tie(why, big) {
    return h("div", { class: "tie" + (big ? " lg" : ""), role: "img", "aria-label": "You and Joon, connected because of " + why },
      h("span", { class: "you" }), h("span", { class: "rule" }), h("span", { class: "why" }, why), h("span", { class: "rule" }), h("span", { class: "them" }));
  }

  /* ---------- fictional data for one owner ---------- */
  var DATA = {
    owner: "Ama",
    objectives: [
      "Someone who has shipped low-power firmware, a few hours a week",
      "An agronomist who could sanity-check soil-moisture calibration in clay"
    ],
    scope: ["Open Hardware Forum · #collaborators", "Embedded Systems Discord · #projects", "AgTech Builders · project board"],
    posting: "Building a low-cost, open-hardware soil sensor with three working farms. Looking for someone who has shipped low-power firmware to help the v3 board last a growing season on one battery, a few hours a week. I can offer field test sites, real soil data and co-authorship on the open design.",
    postingMeta: "On 3 boards · wording you approved on Sun 25 Oct",
    cadence: "Weekly, Thursdays 08:00",
    last: "Thu 29 Oct, 08:00",
    next: "Thu 5 Nov, 08:00",
    summary: [
      ["Thu 29 Oct", "Read 31 new posts on 3 boards. One worth your attention: Joon."],
      ["Sun 25 Oct", "Updated your posting with the wording you approved (added co-authorship)."],
      ["Thu 22 Oct", "Read 44 posts. Two firmware engineers, both full-time only. Passed over."],
      ["Thu 15 Oct", "First check-in. Read 27 posts. Nothing that worked both ways."]
    ],
    log: [
      ["29 Oct 08:04", "Check-in", "3 boards", "31 posts read · 1 connection suggested"],
      ["29 Oct 08:03", "Compared", "Open Hardware Forum", "Joon: fits both ways"],
      ["29 Oct 08:02", "Compared", "Embedded Systems Discord", "Priya: needs a full-time hire · passed"],
      ["25 Oct 19:12", "Posting updated", "3 boards", "Your approved wording v2"],
      ["22 Oct 08:03", "Check-in", "3 boards", "44 posts read · nothing suggested"],
      ["15 Oct 08:05", "Check-in", "3 boards", "27 posts read · nothing suggested"],
      ["15 Oct 08:01", "Posted", "3 boards", "Your approved wording v1"]
    ],
    shared: [
      ["Your posting", "Public on the 3 boards since 15 Oct"],
      ["Introduction request", "Not sent to anyone"],
      ["Anything else", "Nothing has been shared"]
    ],
    joon: {
      name: "Joon", where: "Open Hardware Forum", when: "Found Thu 29 Oct, 08:03",
      why: "your farms, his firmware",
      headline: "Joon could help the v3 board last a season, and he needs your soil data.",
      summary: "He posted on Tuesday asking for real field data for a paper due in February. You need low-power firmware help. It works in both directions.",
      now: "Joon posted on Tue 27 Oct that he needs field data from real soil for a paper due in February. Your farms are ready for tests this month.",
      evidence: ["Joon's post, Open Hardware Forum, 27 Oct: “8 years of ultra-low-power firmware, looking for real field data”", "Your brief: “the v3 board drains its battery in 9 days”", "Your posting offers field test sites and soil data"],
      youBring: "Three working farms, clay-soil data, a board that's ready for testing.",
      youGain: "Firmware that lasts a growing season on one battery.",
      themBring: "Eight years of ultra-low-power firmware, about 5 hours a week.",
      themGain: "Field data from real clay soil for his February paper.",
      unknown: ["Whether his 5 hours a week can start before December", "Whether co-authorship fits how his paper is credited"],
      step: "A 20-minute call to look at the v3 board's power trace together."
    }
  };

  var STATES = [
    ["setup", "First visit"], ["working", "Working"], ["quiet", "Nothing yet"], ["question", "Question"],
    ["connection", "Connection"], ["paused", "Paused"], ["failed", "Check-in failed"]
  ];
  var app = { state: "connection", intro: "none", toastShown: false, sent: [] };

  /* ---------- shared pieces ---------- */
  function looking(state) {
    var quiet = state === "quiet";
    return block("What your agent is looking for", "Agreed during setup",
      h("div", { class: "looking" },
        h("ul", {}, DATA.objectives.map(function (o) { return h("li", { class: "objective" }, h("i", {}), h("span", {}, o)); })),
        h("div", {}, h("p", { class: "sublabel" }, "Where it looks"), h("div", { class: "scope" }, DATA.scope.map(function (s) { return h("span", {}, s); }))),
        h("div", { class: "posting" }, h("p", { class: "meta" }, quiet ? "On 3 boards · posted Thu 15 Oct, approved by you" : DATA.postingMeta), h("blockquote", {}, DATA.posting))
      ));
  }
  function workDone(state) {
    var rows = state === "quiet" ? [
      ["Thu 29 Oct", "Read 31 new posts on 3 boards. Nothing that works both ways yet."],
      ["Thu 22 Oct", "Read 44 posts. Two firmware engineers, both full-time only. Passed over."],
      ["Thu 15 Oct", "First check-in. Posted your approved wording and read 27 posts."]
    ] : (state === "paused" || state === "failed") ? DATA.summary.slice(1) : DATA.summary;
    return block("Since you last looked", rows.length + " entries",
      h("ul", { class: "summary-list" }, rows.map(function (r) { return h("li", {}, h("time", {}, r[0]), h("span", {}, r[1])); })),
      h("details", { class: "log" }, h("summary", {}, "Full activity record"),
        h("div", { class: "log-scroll" }, h("table", { class: "log-table" },
          h("thead", {}, h("tr", {}, ["When", "What", "Where", "Result"].map(function (x) { return h("th", { scope: "col" }, x); }))),
          h("tbody", {}, DATA.log.map(function (r) { return h("tr", {}, r.map(function (c) { return h("td", {}, c); })); }))))),
      h("details", { class: "log" }, h("summary", {}, "What has been shared"),
        h("ul", { class: "summary-list" }, DATA.shared.map(function (r) { return h("li", {}, h("time", {}, r[0]), h("span", {}, r[1])); }))));
  }
  function side(state) {
    var paused = state === "paused", failed = state === "failed";
    return h("aside", { class: "side", "aria-label": "Check-ins and controls" },
      block("Check-ins", null,
        h("dl", { class: "checkin" },
          h("div", {}, h("dt", {}, "Cadence"), h("dd", {}, DATA.cadence)),
          h("div", {}, h("dt", {}, "Last"), h("dd", {}, failed || paused ? "Thu 22 Oct, 08:00" : DATA.last)),
          h("div", {}, h("dt", {}, failed ? "Missed" : "Next"), h("dd", {}, paused ? "None while paused" : failed ? "Thu 29 Oct, 08:00" : DATA.next))),
        h("p", { class: "side-note" }, "Scheduled in your assistant, not on this site. Reports reach this page once that's built."), h("span", { class: "needs-int", style: "margin:8px 0 0" }, "Needs integration")),
      h("div", { id: "permissions" }, block("Permissions", "Change any time",
        h("ul", { class: "perm" },
          h("li", {}, h("b", {}, "Without asking: "), "search 3 boards, read public posts, keep your approved posting current."),
          h("li", {}, h("b", {}, "Only with your yes: "), "introduction requests, new wording, new boards, sharing more."),
          h("li", {}, h("b", {}, "Never: "), "reply as you, accept for you, agree money or time.")),
        h("div", { class: "controls", style: "margin-top:16px" },
          h("button", { class: "btn btn-line btn-sm", type: "button", onclick: togglePause }, paused ? "Resume check-ins" : "Pause check-ins"),
          h("button", { class: "btn btn-quiet btn-sm", type: "button", onclick: function () { flash("Editing permissions happens in your assistant for now."); } }, "Edit boundaries")))));
  }

  /* ---------- attention area by state ---------- */
  function attention(state) {
    if (state === "working") return block("Worth your attention", "Nothing waiting",
      h("div", { class: "quiet" }, h("p", { class: "attention-title" }, "Nothing needs you right now."),
        h("p", { class: "attention-sub" }, "Your agent read 31 new posts this morning. None worked in both directions, so it stayed quiet. It updated your posting on Sunday with the wording you approved.")));
    if (state === "quiet") return block("Worth your attention", "Nothing waiting",
      h("div", { class: "quiet" }, h("p", { class: "attention-title" }, "Nothing worth your time yet."),
        h("p", { class: "attention-sub" }, "Three check-ins, 102 posts read, no one who fits both ways. That's common in the first weeks: the right person may simply not have posted yet. Your agent keeps checking; you don't need to do anything."),
        h("p", { class: "attention-sub" }, h("a", { href: "#permissions" }, "Add a board"), " if you know a community where your kind of collaborator spends time.")));
    if (state === "question") return block("Worth your attention", "1 question",
      h("div", { class: "question" },
        h("h3", {}, "Would you pay for a short, scoped piece of firmware work?"),
        h("p", { class: "because" }, "Because: of the last 9 firmware posts your agent read, 6 offer paid work only. If you'd consider a small paid piece, it can include them. If not, it keeps looking for revenue-share people only."),
        h("div", { class: "answers", role: "group", "aria-label": "Answer" },
          ["Yes, up to a small fixed fee", "No, revenue share only", "Ask me again in a month"].map(function (a) {
            return h("button", { class: "btn btn-line btn-sm", type: "button", onclick: function (e) { answered(e.currentTarget, a); } }, a);
          })),
        h("form", { class: "answer-other", onsubmit: function (e) { e.preventDefault(); var v = e.currentTarget.querySelector("input").value.trim(); if (v) answered(e.currentTarget, v); } },
          h("label", { class: "sr-only", for: "answer-own" }, "Answer in your own words"),
          h("input", { id: "answer-own", type: "text", placeholder: "Or answer in your own words" }),
          h("button", { class: "btn btn-primary btn-sm", type: "submit" }, "Send to my agent"))));
    if (state === "connection") return block("Worth your attention", "1 connection", connCard());
    if (state === "paused") return block("Worth your attention", null,
      h("div", { class: "alert warn", role: "status" }, h("span", { class: "sym" }, "II"),
        h("div", {}, h("h3", {}, "Check-ins are paused"),
          h("p", {}, "You paused them on Tue 27 Oct. Nothing is searched while paused. Your approved posting stays up on 3 boards."),
          h("div", { class: "actions" }, h("button", { class: "btn btn-primary btn-sm", type: "button", onclick: togglePause }, "Resume check-ins"),
            h("button", { class: "btn btn-line btn-sm", type: "button", onclick: function () { flash("Example: your agent would remove the posting from all 3 boards and confirm here."); } }, "Also take my posting down")))));
    if (state === "failed") return block("Worth your attention", null,
      h("div", { class: "alert bad", role: "status" }, h("span", { class: "sym" }, "!"),
        h("div", {}, h("h3", {}, "This morning's check-in didn't report back"),
          h("p", {}, "It was due Thu 29 Oct at 08:00. The last one that worked was Thu 22 Oct. Nothing is wrong with your posting; your agent just hasn't run."),
          h("ol", {}, h("li", {}, "Open your assistant's scheduled tasks and check “Connect the Bots check-in” is still there."),
            h("li", {}, "Or run it now: paste the line below into your assistant.")),
          h("div", { class: "code-line" }, h("code", {}, "Run my Connect the Bots check-in"),
            h("button", { class: "btn btn-line btn-sm", type: "button", onclick: function (e) { copy("Run my Connect the Bots check-in", e.currentTarget); } }, "Copy")))));
    return null;
  }
  function answered(el, a) {
    var box = el.closest(".question");
    box.textContent = "";
    box.appendChild(h("h3", {}, "Thanks. Your agent will use that from the next check-in."));
    box.appendChild(h("p", { class: "because" }, "Your answer: “" + a + "”. You can change it under Permissions. (Example: not sent anywhere.)"));
  }

  function connCard() {
    var j = DATA.joon;
    return h("article", { class: "conn-card" },
      h("div", { class: "top" }, h("span", { class: "status-line" }, h("i", {}), "Worth a look"), h("span", { class: "found" }, j.when + " · " + j.where)),
      tie(j.why, false),
      h("div", { class: "tie-names" }, h("span", {}, h("b", {}, "You")), h("span", {}, h("b", {}, "Joon"), " · ultra-low-power firmware")),
      h("p", { class: "summary" }, j.summary),
      h("p", { class: "summary", style: "margin-top:8px;font-size:15.5px;color:var(--warn)" }, "Still unknown: " + j.unknown[0] + "."),
      h("div", { class: "actions" }, h("a", { class: "btn btn-primary", href: "#joon" }, "See why"), h("a", { class: "btn btn-quiet", href: "#joon" }, "Decide later")));
  }

  /* ---------- setup ---------- */
  function setupView() {
    var copied = false;
    try { copied = localStorage.getItem("ctb-copied") === "1"; } catch (e) { /* storage blocked */ }
    function step(n, title, body, stateText, ok, extra) {
      return h("li", { class: ok ? "done" : "todo" }, h("span", { class: "n" }, String(n)),
        h("div", {}, h("h3", {}, title), h("p", {}, body), extra || null),
        h("span", { class: "state " + (ok ? "ok" : "no") }, stateText));
    }
    return h("div", {},
      h("section", { class: "block" },
        h("div", { class: "block-head" }, h("h2", {}, "Set up your agent"), h("span", { class: "meta" }, "About ten minutes")),
        h("p", { class: "attention-title" }, "Your agent isn't set up yet."),
        h("p", { class: "attention-sub" }, "Setup happens in the assistant you already use. This page can't see it yet, so these steps show what to do, not what has happened.")),
      h("ol", { class: "setup-steps" },
        step(1, "Get the interview prompt", "Copy or download it. This gives you text only; nothing is connected.", copied ? "Copied on this device" : "Not yet", copied,
          h("div", { class: "code-line" }, h("a", { class: "btn btn-primary btn-sm", href: "about.html#start" }, "Get the interview prompt"))),
        step(2, "Run it in your assistant", "It asks which of your sources it may read, then a few questions about what's missing. You approve your brief and the exact posting.", "Happens in your assistant", false),
        step(3, "Choose daily or weekly check-ins", "If your assistant can schedule tasks, it creates one and shows you. If it can't, add this reminder to your calendar and paste it when it fires:", "Your assistant confirms", false,
          h("div", { class: "code-line" }, h("code", {}, "Run my Connect the Bots check-in"), h("button", { class: "btn btn-line btn-sm", type: "button", onclick: function (e) { copy("Run my Connect the Bots check-in", e.currentTarget); } }, "Copy"))),
        step(4, "See its work here", "Your agent's reports, questions and connections will appear on this page.", "Not available yet", false, h("span", { class: "needs-int", style: "margin-left:0;margin-top:8px" }, "Needs integration"))));
  }

  /* ---------- main dashboard ---------- */
  function dashboard(state) {
    if (state === "setup") return h("div", { class: "app-grid" }, setupView(),
      h("aside", { class: "side" }, block("What this page will show", null,
        h("ul", { class: "perm" }, h("li", {}, "People worth meeting, with the reasons"), h("li", {}, "Questions only you can answer"), h("li", {}, "What your agent read and posted"), h("li", {}, "When it checks next")))));
    return h("div", { class: "app-grid" },
      h("div", {}, attention(state), looking(state), workDone(state)),
      side(state));
  }

  /* ---------- connection detail ---------- */
  function stages() {
    var order = ["none", "sent", "mutual", "talking"];
    var at = order.indexOf(app.intro);
    var items = [["Found", 0], ["Your choice", 0], ["Invitation sent", 1], ["Both agreed", 2], ["Talking", 3]];
    return h("ol", { class: "stages", "aria-label": "Introduction progress" }, items.map(function (it, i) {
      var cls = i === 0 ? "done" : i === 1 ? (at === 0 ? "now" : "done") : (at > it[1] ? "done" : at === it[1] ? "now" : "");
      return h("li", { class: cls, "aria-current": cls === "now" ? "step" : null }, h("i", {}), it[0]);
    }));
  }
  function detail() {
    var j = DATA.joon;
    var choice;
    if (app.intro === "none") choice = h("div", { class: "choice" },
      h("button", { class: "btn btn-primary", type: "button", onclick: function () { app.intro = "sent"; render(); } }, "Ask for an introduction"),
      h("button", { class: "btn btn-line", type: "button", onclick: function () { flash("Saved. It stays on your dashboard; nothing is sent."); } }, "Save for later"),
      h("button", { class: "btn btn-quiet", type: "button", onclick: function () { flash("Passed. Your agent won't suggest Joon again for this brief."); location.hash = "#working"; } }, "Pass"),
      h("p", { class: "fine" }, "Asking sends Joon these reasons through the Open Hardware Forum. Nothing else about you is shared unless he says yes."));
    else if (app.intro === "sent") choice = h("div", { class: "choice" },
      h("span", { class: "status-line waiting" }, h("i", {}), "Invitation sent Thu 29 Oct. Waiting for Joon to decide."),
      h("p", { class: "fine" }, "He sees the same reasons you do. If he passes, you'll be told, without a reason."),
      h("button", { class: "btn btn-line btn-sm", type: "button", onclick: function () { app.intro = "mutual"; render(); } }, "Show what happens if Joon says yes (example)"));
    else choice = h("div", { class: "choice" },
      h("span", { class: "status-line mutual" }, h("i", {}), "You both said yes. You can talk directly now."),
      h("a", { class: "btn btn-primary", href: "#talk" }, "Start the conversation"));
    return h("article", { "aria-labelledby": "detail-h" },
      h("a", { class: "detail-back", href: "#" + (app.state || "connection") }, "← Dashboard"),
      h("div", { class: "detail-hero" },
        h("span", { class: "example-tag" }, "Example · fictional people"),
        h("h1", { id: "detail-h" }, j.headline),
        tie(j.why, true),
        h("div", { class: "tie-names" }, h("span", {}, h("b", {}, "You"), " · soil sensor, three farms"), h("span", {}, h("b", {}, "Joon"), " · " + j.where)),
        stages()),
      h("div", { class: "facts" },
        h("div", { class: "fact" }, h("h4", {}, "Why now"), h("p", {}, j.now)),
        h("div", { class: "fact" }, h("h4", {}, "Evidence"), h("ul", {}, j.evidence.map(function (e) { return h("li", {}, e); }))),
        h("div", { class: "fact" }, h("h4", {}, h("span", { class: "who" }, h("i", {}), "You bring · could gain")), h("p", {}, j.youBring), h("p", { style: "margin-top:8px;color:var(--ink-2)" }, j.youGain)),
        h("div", { class: "fact" }, h("h4", {}, h("span", { class: "who" }, h("s", {}), "Joon brings · could gain")), h("p", {}, j.themBring), h("p", { style: "margin-top:8px;color:var(--ink-2)" }, j.themGain)),
        h("div", { class: "fact unknown" }, h("h4", {}, "Still unknown"), h("ul", {}, j.unknown.map(function (u) { return h("li", {}, u); }))),
        h("div", { class: "fact" }, h("h4", {}, "A small first step"), h("p", {}, j.step))),
      choice);
  }

  /* ---------- conversation ---------- */
  function conversation() {
    if (app.intro !== "mutual" && app.intro !== "talking") return h("div", {},
      h("a", { class: "detail-back", href: "#joon" }, "← Connection"),
      h("p", { class: "attention-title", style: "margin-top:24px" }, "This conversation isn't open yet."),
      h("p", { class: "attention-sub" }, "A conversation opens only after you and Joon both say yes."));
    var ta = h("textarea", { id: "msg", placeholder: "Write to Joon in your own words" });
    var help = h("div", { class: "draft-help", hidden: true },
      h("p", {}, "From your notes, things Joon might want to know first:"),
      h("ul", {}, h("li", {}, "The v3 board's battery lasts 9 days; you want a season."),
        h("li", {}, "Three farms are ready for tests this month."),
        h("li", {}, "His suggested first step: a 20-minute call on the power trace.")),
      h("div", { style: "display:flex;gap:8px;margin-top:12px;flex-wrap:wrap" },
        h("button", { class: "btn btn-line btn-sm", type: "button", onclick: function () {
          ta.value = "Hi Joon, I saw your post about needing field data. I'm building an open soil sensor with three farms, and the v3 board only lasts 9 days on a battery. Would a 20-minute call next week to look at the power trace together be useful? Happy to share what soil data we have.";
          ta.focus();
        } }, "Put a draft in the box to edit"),
        h("span", { class: "side-note", style: "margin:0;align-self:center" }, "Your words go out only when you press Send.")));
    var thread = h("div", { class: "thread" },
      h("p", { class: "sys-msg" }, "You and Joon both said yes on 30 Oct. This is a direct conversation between the two of you. Your agents don't read or write here."),
      app.sent.map(function (m) { return h("div", { class: "msg mine" }, h("p", { class: "by" }, "You · not delivered (prototype)"), h("p", {}, m)); }));
    return h("div", {},
      h("a", { class: "detail-back", href: "#joon" }, "← Connection"),
      h("div", { class: "detail-hero", style: "padding-bottom:24px" },
        h("span", { class: "example-tag" }, "Prototype · messages are not sent"),
        h("h1", { id: "talk-h", style: "margin-bottom:16px" }, "You and Joon"),
        tie(DATA.joon.why, false)),
      h("div", { class: "convo" },
        h("div", {}, thread,
          h("form", { class: "composer", onsubmit: function (e) { e.preventDefault(); var v = ta.value.trim(); if (!v) { ta.focus(); return; } app.sent.push(v); app.intro = "talking"; render(); } },
            h("label", { for: "msg" }, "Message"), ta,
            h("div", { class: "row" },
              h("button", { class: "btn btn-quiet btn-sm", type: "button", onclick: function () { help.hidden = !help.hidden; } }, "Help me start"),
              h("button", { class: "btn btn-primary btn-sm", type: "submit" }, "Send")))),
        h("aside", {}, block("Help, if you want it", null, help, h("p", { class: "side-note" }, "Your agent can suggest what to mention. It never writes as you or sends anything.")),
          block("What Joon knows about you", null, h("ul", { class: "perm" }, h("li", {}, "Your approved posting"), h("li", {}, "The reasons on this connection"), h("li", {}, "Nothing from your private notes"))))));
  }

  /* ---------- notification ---------- */
  function toast() {
    var slot = $("[data-toast-slot]");
    slot.textContent = "";
    if (app.state !== "connection" || app.toastShown || location.hash === "#joon") return;
    app.toastShown = true;
    var el = h("div", { class: "notify", role: "status", "aria-live": "polite" },
      h("p", { class: "mono" }, "Connect the Bots · 08:04"),
      h("b", {}, "One person worth meeting"),
      h("p", {}, "Joon, ultra-low-power firmware. Your farms, his firmware."),
      h("div", { class: "actions" },
        h("a", { class: "btn btn-primary btn-sm", href: "#joon", onclick: function () { el.remove(); } }, "See why"),
        h("button", { class: "btn btn-line btn-sm", type: "button", onclick: function () { el.remove(); } }, "Later")),
      h("p", { class: "side-note", style: "margin-top:10px" }, "Example notification. Email and phone delivery need integration."));
    slot.appendChild(el);
  }
  function flash(msg) {
    var slot = $("[data-toast-slot]");
    slot.textContent = "";
    var el = h("div", { class: "notify", role: "status" }, h("p", {}, msg), h("div", { class: "actions" }, h("button", { class: "btn btn-line btn-sm", type: "button", onclick: function () { el.remove(); } }, "OK")));
    slot.appendChild(el);
    setTimeout(function () { el.remove(); }, 6000);
  }
  function copy(text, btn) {
    function done(msg) { btn.textContent = msg; setTimeout(function () { btn.textContent = "Copy"; }, 2000); }
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(function () { done("Copied"); }, function () { done("Select and copy"); });
    else done("Select and copy");
  }

  /* ---------- header + routing ---------- */
  function togglePause() {
    location.hash = app.state === "paused" ? "#working" : "#paused";
  }
  function header() {
    var s = app.state;
    var pill = $("[data-agent-state]");
    var map = { setup: ["none", "Not set up"], paused: ["paused", "Paused"], failed: ["failed", "Check-in missed"] };
    var v = map[s] || ["", "Agent working"];
    pill.className = "agent-state " + v[0];
    pill.lastChild.textContent = v[1];
    var pb = $("[data-pause]");
    pb.hidden = s === "setup";
    pb.textContent = s === "paused" ? "Resume" : "Pause";
    $("[data-perm-link]").hidden = s === "setup";
    var bar = $("[data-states]");
    bar.textContent = "";
    STATES.forEach(function (st) {
      bar.appendChild(h("button", { type: "button", "aria-pressed": String(st[0] === s), onclick: function () { location.hash = "#" + st[0]; } }, st[1]));
    });
  }
  var ROUTES = STATES.map(function (s) { return s[0]; }).concat(["joon", "talk"]);
  function render() {
    var hash = (location.hash || "#connection").slice(1);
    if (ROUTES.indexOf(hash) < 0) hash = app.state;
    var view = $("[data-view]");
    view.textContent = "";
    if (hash === "joon") { view.appendChild(detail()); document.title = "Joon · Connect the Bots"; }
    else if (hash === "talk") { view.appendChild(conversation()); document.title = "You and Joon · Connect the Bots"; }
    else {
      app.state = STATES.some(function (s) { return s[0] === hash; }) ? hash : "connection";
      view.appendChild(dashboard(app.state));
      document.title = "Your agent · Connect the Bots";
    }
    header();
    toast();
  }
  $("[data-pause]").addEventListener("click", togglePause);
  window.addEventListener("hashchange", function () {
    var hash = location.hash.slice(1);
    if (ROUTES.indexOf(hash) < 0) return; // an in-page anchor such as #permissions
    render();
    if (hash === "joon" || hash === "talk") { window.scrollTo(0, 0); $("[data-view]").focus({ preventScroll: true }); }
  });
  render();
})();
