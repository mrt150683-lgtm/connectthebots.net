/**
 * Page frame: identity, prototype labelling, the demo perspective switch, the
 * simulated clock, the journey tabs and the activity list.
 *
 * The perspective switch is labelled as a demo control everywhere it appears.
 * It changes which fictional owner the next action is taken for; it is not a
 * sign-in and proves nothing about who is at the keyboard.
 */
import type { Actions } from "../actions.js";
import { brandMark, formatTime, h } from "../dom.js";
import type { AppState, Tab } from "../state.js";

const TABS: { id: Tab; label: string; step: string }[] = [
  { id: "brief", label: "Brief", step: "1" },
  { id: "publish", label: "Publish", step: "2" },
  { id: "discover", label: "Discover", step: "3" },
  { id: "introductions", label: "Introductions", step: "4" },
];

export function header(state: AppState, actions: Actions): HTMLElement {
  const session = state.session;
  const perspective = h("select", { id: "perspective", "aria-describedby": "perspective-note", disabled: state.busy !== null },
    (session?.owners ?? []).map((owner) => h("option", { value: owner.owner_id, selected: owner.owner_id === state.owner },
      `${owner.alias}${owner.mode ? " · " + (owner.mode.id === "project" ? "project" : "contributor") : ""}`)));
  perspective.addEventListener("change", () => actions.selectOwner(perspective.value));
  return h("header", { className: "topbar" },
    h("div", { className: "shell topbar-inner" },
      h("a", { className: "identity", href: "#main" }, brandMark(), h("span", {}, "agentmatch"),
        h("span", { className: "prototype-label" }, "Fictional prototype")),
      h("div", { className: "controls" },
        h("div", { className: "perspective" },
          h("label", { for: "perspective" }, "Viewing as"), perspective,
          h("p", { id: "perspective-note", className: "control-note" }, "Demo perspective, not sign-in")),
        h("div", { className: "clock", role: "group", "aria-label": "Simulated clock" },
          h("p", { className: "control-label" }, "Simulated time"),
          h("p", { className: "clock-time" }, session ? formatTime(session.now) : "…"),
          h("div", { className: "clock-buttons" },
            h("button", { type: "button", className: "button ghost small", onclick: () => actions.advanceClock(1) }, "+1 day"),
            h("button", { type: "button", className: "button ghost small", onclick: () => actions.advanceClock(7) }, "+7 days"),
            h("button", { type: "button", className: "button ghost small", onclick: () => actions.resetDemo() }, "Reset demo"))))));
}

export function hero(state: AppState): HTMLElement {
  const notices = state.session?.notices;
  return h("section", { className: "hero shell", "aria-labelledby": "hero-title" },
    h("div", {},
      h("p", { className: "eyebrow" }, "Local collaboration workbench"),
      h("h1", { id: "hero-title" }, h("span", {}, "Connecting"), h("span", {}, "human intelligence")),
      h("p", { className: "hero-copy" }, h("strong", {}, "An idea. A capability. The right person between them. "),
        "Project and business collaboration first; friendship later, dating last.")),
    h("div", { className: "prototype-notice", role: "note", "aria-label": "About this prototype" },
      h("p", {}, h("strong", {}, notices?.prototype ?? "Fictional prototype.")),
      h("p", {}, notices?.perspective ?? ""),
      h("p", {}, notices?.scope ?? "")));
}

export function tabs(state: AppState, actions: Actions): HTMLElement {
  const ws = state.workspace;
  const badge = (tab: Tab): string | null => {
    if (!ws) return null;
    if (tab === "brief") return ws.brief ? `v${ws.brief.version}${state.briefDirty ? " · unsaved" : ""}` : "empty";
    if (tab === "publish") {
      const active = ws.publications.filter((p) => p.status === "active").length;
      const paused = ws.publications.filter((p) => p.status === "suspended_brief_changed").length;
      return paused ? `${paused} paused` : active ? `${active} live` : "none";
    }
    if (tab === "introductions") {
      const waiting = ws.introductions.filter((i) => i.status === "pending" && i.actions.includes("accept")).length;
      const open = ws.introductions.filter((i) => i.status === "open").length;
      return waiting ? `${waiting} to review` : open ? `${open} open` : null;
    }
    if (tab === "discover") {
      const incoming = ws.information_requests.filter((request) => request.direction === "incoming" && request.status === "pending").length;
      const answered = ws.information_requests.filter((request) => request.direction === "outgoing" && request.status === "answered").length;
      return incoming ? `${incoming} request${incoming === 1 ? "" : "s"}` : answered ? `${answered} updated` : null;
    }
    return null;
  };
  const list = h("div", { className: "tabs shell", role: "tablist", "aria-label": "Journey" },
    TABS.map((tab) => {
      const selected = state.tab === tab.id;
      const text = badge(tab.id);
      return h("button", { type: "button", role: "tab", id: `tab-${tab.id}`, "aria-selected": selected ? "true" : "false",
                           "aria-controls": "panel", tabindex: selected ? 0 : -1, className: "tab",
                           onclick: () => actions.selectTab(tab.id) },
        h("span", { className: "tab-step", "aria-hidden": "true" }, tab.step), h("span", {}, tab.label),
        text ? h("span", { className: "tab-badge" }, text) : null);
    }));
  // Arrow keys move between tabs, as in the ARIA tabs pattern.
  list.addEventListener("keydown", (event) => {
    const index = TABS.findIndex((tab) => tab.id === state.tab);
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % TABS.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + TABS.length) % TABS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = TABS.length - 1;
    else return;
    event.preventDefault();
    const target = TABS[next];
    if (target) {
      actions.selectTab(target.id);
      document.getElementById(`tab-${target.id}`)?.focus();
    }
  });
  return list;
}

export function activity(state: AppState): HTMLElement | null {
  const ws = state.workspace;
  if (!ws) return null;
  return h("aside", { className: "activity shell", "aria-labelledby": "activity-title" },
    h("h2", { id: "activity-title" }, `What happened in ${ws.owner.alias}'s perspective`),
    h("p", { className: "muted small" }, "Only actions taken in this local session. Nothing here was done automatically or by an agent."),
    ws.activity.length === 0 ? h("p", { className: "muted" }, "Nothing yet.") :
      h("ol", { className: "activity-list" }, ws.activity.map((item) => h("li", {}, h("time", { datetime: item.at }, formatTime(item.at)), h("span", {}, item.text)))));
}

export function footer(): HTMLElement {
  return h("footer", { className: "page-footer shell" },
    h("p", {}, "AgentMatch local collaboration workbench. Fictional data only. No accounts, no encryption, no network transport. " +
      "This prototype does not complete the R1 secure-owner or R2 networking release gates and is not ready for real people."));
}
