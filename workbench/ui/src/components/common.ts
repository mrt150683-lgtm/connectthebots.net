/**
 * Small reusable presentation components. They only render what they are
 * given; no component decides permission, matching or disclosure.
 */
import type { NoticeCard, Option } from "../contracts.js";
import { formatDate, h } from "../dom.js";

export type Placement = "scored" | "notice" | "private";

const PLACEMENT_TEXT: Record<Placement, string> = {
  scored: "Used for matching",
  notice: "Notice only, not scored",
  private: "Private, never shared",
};

/** Says where a field's value can go. Text plus shape, never colour alone. */
export function placementBadge(placement: Placement): HTMLElement {
  return h("span", { className: `placement placement-${placement}` }, PLACEMENT_TEXT[placement]);
}

export function chips(options: Option[] | string[], label?: string): HTMLElement {
  const items = options.map((item) => (typeof item === "string" ? item : item.label));
  if (items.length === 0) return h("span", { className: "muted" }, "None stated");
  return h("ul", { className: "chips", ...(label ? { "aria-label": label } : {}) },
    items.map((text) => h("li", { className: "chip" }, text)));
}

export function emptyState(title: string, body: string, action?: HTMLElement): HTMLElement {
  return h("div", { className: "empty", role: "status" }, h("p", { className: "empty-title" }, title), h("p", {}, body), action ?? null);
}

export function sectionHeading(eyebrow: string, title: string, intro?: string, id?: string): HTMLElement {
  return h("header", { className: "section-heading" },
    h("p", { className: "eyebrow" }, eyebrow),
    h("h2", { tabindex: -1, ...(id ? { id } : {}) }, title),
    intro ? h("p", { className: "lead" }, intro) : null);
}

export function statusPill(text: string, tone: "positive" | "caution" | "neutral" | "negative"): HTMLElement {
  const marks = { positive: "✓", caution: "?", neutral: "–", negative: "×" };
  return h("span", { className: `pill pill-${tone}` }, h("span", { "aria-hidden": "true" }, marks[tone]), " ", text);
}

function dl(rows: [string, Node | string | null][]): HTMLElement {
  return h("dl", { className: "facts" }, rows.filter(([, value]) => value !== null).flatMap(([term, value]) => [
    h("dt", {}, term), h("dd", {}, value),
  ]));
}

/** An approved notice as another member sees it. Free text is shown as plain, untrusted text. */
export function noticeCard(card: NoticeCard, options: { headingLevel?: "h3" | "h4"; compact?: boolean; actions?: HTMLElement | null } = {}): HTMLElement {
  const heading = h(options.headingLevel ?? "h3", { className: "notice-title" }, card.title);
  const work = card.working_arrangement;
  const rows: [string, Node | string | null][] = [
    ["Offers", h("span", {}, chips(card.offers), card.offers_complete ? null : h("span", { className: "muted small" }, " (list not marked complete)"))],
    ["Primary need", card.primary_need.label],
    ["Also useful", card.also_seeks.length ? chips(card.also_seeks) : null],
    ["Focus", chips(card.focus_areas)],
    ["Availability", card.availability.label + (card.availability_required ? " · requires availability" : "")],
    ["Engagement", `${card.basis.label}${card.basis_required ? " · requirement: " + card.accepted_bases.map((b) => b.label).join(", ") : ""}`],
  ];
  if (!options.compact) {
    rows.push(
      ["Working", work ? [work.venue?.label ?? "Not stated", work.rhythm?.label ?? null].filter(Boolean).join(" · ") : "Not stated in this notice"],
      ["Feedback", card.feedback ? card.feedback.style?.label ?? "Not stated" : "Not stated in this notice"],
      ["Time", card.schedule ? `${card.schedule.hours_per_week ? card.schedule.hours_per_week + " h/week · " : ""}${card.schedule.start_window.label}` : "Not stated in this notice"],
      ["Suggested first step", card.first_step.label],
    );
  }
  return h("article", { className: `notice-card${options.compact ? " compact" : ""}`, "aria-label": `${card.title}, by ${card.publisher_alias}` },
    h("div", { className: "notice-meta" },
      h("span", { className: "notice-type" }, card.mode.id === "project" ? "Project or opportunity" : "Contribution"),
      h("span", {}, card.board.name)),
    heading,
    h("p", { className: "notice-outcome" }, card.desired_outcome),
    dl(rows),
    !options.compact && card.commercial_note
      ? h("div", { className: "untrusted" },
          h("p", { className: "untrusted-label" }, "Commercial note, written by the owner"),
          h("p", {}, card.commercial_note),
          h("p", { className: "muted small" }, card.untrusted_text_note))
      : null,
    h("footer", { className: "notice-person" },
      h("span", { className: "avatar", "aria-hidden": "true" }, card.publisher_alias.slice(0, 1)),
      h("span", {}, h("strong", {}, card.publisher_alias),
        h("small", {}, `${card.stage.label} · until ${formatDate(card.expires_at)} · ${card.provenance}`))),
    options.actions ?? null);
}
