/**
 * Brief editor: both entry paths ("I have a project or opportunity" and
 * "I want to contribute") use the same structured form.
 *
 * Inputs update the local draft without re-rendering, so typing never loses
 * focus. Only "Save private draft" sends anything, and saving is local to the
 * server's memory. Each field shows where its value can go: used for matching,
 * notice only, or private. Server field errors are rendered next to their
 * control and linked with aria-describedby.
 */
import type { Actions } from "../actions.js";
import type { BriefContent, FieldError, Option } from "../contracts.js";
import { formatTime, h, nextId } from "../dom.js";
import type { AppState } from "../state.js";
import { emptyState, placementBadge, sectionHeading, type Placement } from "./common.js";

type Key = keyof BriefContent;

function errorFor(errors: FieldError[], field: Key): FieldError | undefined {
  return errors.find((item) => item.field === field);
}

function fieldShell(id: string, label: string, placement: Placement, control: HTMLElement, errors: FieldError[], field: Key,
                    hint?: string): HTMLElement {
  const error = errorFor(errors, field);
  const hintId = hint ? `${id}-hint` : null;
  const errorId = error ? `${id}-error` : null;
  const described = [hintId, errorId].filter(Boolean).join(" ");
  if (described) control.setAttribute("aria-describedby", described);
  if (error) control.setAttribute("aria-invalid", "true");
  return h("div", { className: `field${error ? " has-error" : ""}`, "data-field": field },
    h("div", { className: "field-label-row" }, h("label", { for: id }, label), placementBadge(placement)),
    hint ? h("p", { className: "hint", id: hintId ?? undefined }, hint) : null,
    control,
    error ? h("p", { className: "field-error", id: errorId ?? undefined }, error.message) : null);
}

function groupShell(legend: string, placement: Placement, inner: HTMLElement, errors: FieldError[], field: Key, hint?: string): HTMLElement {
  const error = errorFor(errors, field);
  const id = nextId(`group-${field}`);
  return h("fieldset", { className: `field group${error ? " has-error" : ""}`, "data-field": field,
                         "aria-describedby": [hint ? `${id}-hint` : null, error ? `${id}-error` : null].filter(Boolean).join(" ") || undefined },
    h("legend", {}, h("span", {}, legend), placementBadge(placement)),
    hint ? h("p", { className: "hint", id: `${id}-hint` }, hint) : null,
    inner,
    error ? h("p", { className: "field-error", id: `${id}-error` }, error.message) : null);
}

function select(id: string, options: Option[], value: string | null, onChange: (value: string | null) => void,
                emptyLabel?: string): HTMLSelectElement {
  const control = h("select", { id, name: id },
    emptyLabel !== undefined ? h("option", { value: "" }, emptyLabel) : null,
    options.map((item) => h("option", { value: item.id, selected: item.id === value }, item.label)));
  control.addEventListener("change", () => onChange(control.value === "" ? null : control.value));
  return control;
}

function checkboxes(name: string, options: Option[], selected: string[], onChange: (value: string[]) => void): HTMLElement {
  const list = h("div", { className: "checks" });
  for (const item of options) {
    const id = nextId(name);
    const box = h("input", { type: "checkbox", id, name, value: item.id, checked: selected.includes(item.id) });
    box.addEventListener("change", () => {
      const chosen = Array.from(list.querySelectorAll<HTMLInputElement>("input:checked")).map((input) => input.value);
      onChange(chosen);
    });
    list.append(h("label", { className: "check", for: id }, box, h("span", {}, item.label)));
  }
  return list;
}

function toggle(id: string, label: string, checked: boolean, onChange: (value: boolean) => void): HTMLElement {
  const box = h("input", { type: "checkbox", id, checked });
  box.addEventListener("change", () => onChange(box.checked));
  return h("label", { className: "check toggle", for: id }, box, h("span", {}, label));
}

export function briefEditor(state: AppState, actions: Actions): HTMLElement {
  const session = state.session;
  const workspace = state.workspace;
  const draft = state.briefDraft;
  if (!session || !workspace) return h("div", { className: "loading", "aria-busy": "true" }, "Loading brief…");
  const voc = session.vocabularies;
  const errors = state.briefErrors;
  const alias = workspace.owner.alias;
  const heading = sectionHeading("Step 1 · Private brief", `What are you working towards, ${alias}?`,
    "Describe a current project or the contribution you want to make. Structured choices are used for matching; " +
    "your words are shown on a notice only if you later approve it. Nothing leaves this draft until you approve a publication.",
    "panel-heading-brief");
  if (!draft) {
    return h("section", { className: "panel" }, heading,
      emptyState("No brief yet", "Start from a fictional example and edit it, or fill in the form from scratch.",
        h("div", { className: "button-row" },
          h("button", { type: "button", className: "button secondary", onclick: () => actions.loadExample("project") }, "Start from a project example"),
          h("button", { type: "button", className: "button secondary", onclick: () => actions.loadExample("contribute") }, "Start from a contributor example"))));
  }
  const set = (patch: Partial<BriefContent>) => actions.updateDraft(patch);
  const id = (field: Key) => `brief-${field}`;

  const modeCards = h("div", { className: "mode-cards", role: "radiogroup", "aria-label": "Which describes you?" },
    voc.modes.map((mode) => {
      const inputId = nextId("mode");
      const input = h("input", { type: "radio", name: "brief-mode", id: inputId, value: mode.id, checked: draft.mode === mode.id });
      input.addEventListener("change", () => set({ mode: mode.id as BriefContent["mode"] }));
      return h("label", { className: "mode-card", for: inputId }, input,
        h("span", { className: "mode-title" }, mode.label),
        h("span", { className: "mode-help" }, mode.id === "project"
          ? "You have a goal, a problem or an opportunity and need someone's contribution."
          : "You have a capability to offer and want a worthwhile project to apply it to."));
    }));

  const textInput = (field: "title" | "commercial_note", max: number) => {
    const input = h("input", { type: "text", id: id(field), maxlength: max, value: draft[field], autocomplete: "off" });
    input.addEventListener("input", () => set({ [field]: input.value } as Partial<BriefContent>));
    return input;
  };
  const textArea = (field: "desired_outcome" | "private_notes", max: number, rows: number) => {
    const area = h("textarea", { id: id(field), maxlength: max, rows });
    area.value = draft[field];
    area.addEventListener("input", () => set({ [field]: area.value } as Partial<BriefContent>));
    return area;
  };
  const hours = h("input", { type: "number", id: id("hours_per_week"), min: 1, max: 60, step: 1, inputmode: "numeric",
                              value: draft.hours_per_week ?? "" });
  hours.addEventListener("input", () => {
    const parsed = hours.value === "" ? null : Number(hours.value);
    set({ hours_per_week: parsed === null || Number.isNaN(parsed) ? null : parsed });
  });
  const capacity = select(id("capacity"), [1, 2, 3, 4, 5].map((n) => ({ id: String(n), label: `${n} at a time` })),
    String(draft.capacity), (value) => set({ capacity: Number(value ?? 1) }));

  const saved = workspace.brief;
  const saveBar = h("div", { className: "save-bar", role: "group", "aria-label": "Save brief" },
    h("p", { className: "save-status", "aria-live": "polite" },
      state.briefDirty ? h("strong", {}, "Unsaved changes. ") : null,
      saved ? `Saved version ${saved.version}, ${formatTime(saved.updated_at)}. Held in this local session's memory only.`
            : "Not saved yet."),
    h("div", { className: "button-row" },
      state.briefDirty && saved ? h("button", { type: "button", className: "button secondary", onclick: () => actions.discardDraft() }, "Discard changes") : null,
      h("button", { type: "submit", id: "brief-save", className: "button primary", disabled: state.busy !== null }, state.busy === "save" ? "Saving…" : "Save private draft")));

  const form = h("form", { className: "brief-form", novalidate: true, "aria-describedby": "brief-form-note" },
    h("p", { id: "brief-form-note", className: "hint" },
      "Saving creates a new version. Saving after you have published pauses your notice until you approve the new version."),
    errors.length ? h("div", { className: "error-summary", role: "alert", tabindex: -1, id: "brief-error-summary" },
      h("p", {}, h("strong", {}, `${errors.length} field${errors.length === 1 ? "" : "s"} need attention.`)),
      h("ul", {}, errors.map((e) => h("li", {}, h("a", { href: `#${id(e.field as Key)}` }, `${e.field.replace(/_/g, " ")}: ${e.message}`))))) : null,
    h("div", { className: "example-row" },
      h("span", { className: "muted" }, "Fill with a fictional example:"),
      h("button", { type: "button", className: "button ghost", onclick: () => actions.loadExample("project") }, "Project example"),
      h("button", { type: "button", className: "button ghost", onclick: () => actions.loadExample("contribute") }, "Contributor example")),
    groupShell("Which describes you?", "notice", modeCards, errors, "mode"),

    h("div", { className: "form-section" }, h("h3", {}, "The outcome"),
      fieldShell(id("title"), "Short title", "notice", textInput("title", 90), errors, "title", "4 to 90 characters."),
      fieldShell(id("desired_outcome"), "What should become possible?", "notice", textArea("desired_outcome", 400, 3), errors, "desired_outcome",
        "Include the next concrete milestone. Not used for matching; nothing is inferred from this text."),
      fieldShell(id("stage"), "Stage", "notice", select(id("stage"), voc.stages, draft.stage, (value) => set({ stage: value ?? "" })), errors, "stage"),
      groupShell("Focus areas (up to 3)", "scored", checkboxes("focus", voc.focus_areas, draft.focus_areas, (value) => set({ focus_areas: value })), errors, "focus_areas")),

    h("div", { className: "form-section" }, h("h3", {}, "Contributions"),
      groupShell("What you can contribute now (up to 6)", "scored", checkboxes("offers", voc.capabilities, draft.offers, (value) => set({ offers: value })), errors, "offers"),
      toggle(id("offers_complete"), "This is my complete list. Others may treat a missing item as a known no rather than unknown.",
        draft.offers_complete, (value) => set({ offers_complete: value })),
      fieldShell(id("primary_need"), "Primary need from the other person", "scored",
        select(id("primary_need"), voc.capabilities, draft.primary_need, (value) => set({ primary_need: value ?? "" }), "Choose one"),
        errors, "primary_need", "A requirement: people who do not offer this are not suggested, however much else matches."),
      groupShell("Also useful (up to 3)", "scored", checkboxes("also", voc.capabilities, draft.also_seeks, (value) => set({ also_seeks: value })), errors, "also_seeks")),

    h("div", { className: "form-section" }, h("h3", {}, "Practical fit"),
      h("div", { className: "two-col" },
        fieldShell(id("availability"), "Your availability", "scored", select(id("availability"), voc.availability, draft.availability, (value) => set({ availability: value ?? "" })), errors, "availability"),
        fieldShell(id("start_window"), "Start window", "notice", select(id("start_window"), voc.start_windows, draft.start_window, (value) => set({ start_window: value ?? "" })), errors, "start_window")),
      toggle(id("availability_required"), "Only suggest people who say they are available now or have limited capacity.", draft.availability_required, (value) => set({ availability_required: value })),
      fieldShell(id("hours_per_week"), "Hours per week (optional)", "notice", hours, errors, "hours_per_week"),
      h("div", { className: "two-col" },
        fieldShell(id("venue"), "How you work", "scored", select(id("venue"), voc.venues, draft.venue, (value) => set({ venue: value }), "Prefer not to say"), errors, "venue",
          "Left unsaid, it stays unknown to others rather than counting against you."),
        groupShell("Works with", "scored", checkboxes("venues", voc.venues, draft.accepted_venues, (value) => set({ accepted_venues: value })), errors, "accepted_venues")),
      h("div", { className: "two-col" },
        fieldShell(id("rhythm"), "Working rhythm", "scored", select(id("rhythm"), voc.rhythms, draft.rhythm, (value) => set({ rhythm: value }), "Prefer not to say"), errors, "rhythm"),
        groupShell("Rhythms you accept", "scored", checkboxes("rhythms", voc.rhythms, draft.accepted_rhythms, (value) => set({ accepted_rhythms: value })), errors, "accepted_rhythms")),
      h("div", { className: "two-col" },
        fieldShell(id("feedback_style"), "Feedback you give", "scored", select(id("feedback_style"), voc.feedback_styles, draft.feedback_style, (value) => set({ feedback_style: value }), "Prefer not to say"), errors, "feedback_style"),
        groupShell("Feedback you work well with", "scored", checkboxes("feedback", voc.feedback_styles, draft.accepted_feedback, (value) => set({ accepted_feedback: value })), errors, "accepted_feedback"))),

    h("div", { className: "form-section" }, h("h3", {}, "Commercial expectations"),
      fieldShell(id("basis"), "Your engagement basis", "scored", select(id("basis"), voc.bases, draft.basis, (value) => set({ basis: value ?? "" })), errors, "basis",
        "“Undecided” is never treated as agreement; it stays unknown until you decide."),
      groupShell("Bases you would accept from the other person", "scored",
        checkboxes("bases", voc.bases.filter((b) => b.id !== "undecided"), draft.accepted_bases, (value) => set({ accepted_bases: value })), errors, "accepted_bases"),
      toggle(id("basis_required"), "Treat the engagement basis as a requirement. Unknown or other bases will not be suggested.", draft.basis_required, (value) => set({ basis_required: value })),
      fieldShell(id("commercial_note"), "Commercial note (optional)", "notice", textInput("commercial_note", 200), errors, "commercial_note",
        "Shown only if you include it when publishing. Records expectations; it is not an agreement.")),

    h("div", { className: "form-section" }, h("h3", {}, "First step and settings"),
      fieldShell(id("first_step"), "A useful first step to suggest", "notice", select(id("first_step"), voc.first_steps, draft.first_step, (value) => set({ first_step: value ?? "" })), errors, "first_step"),
      h("div", { className: "two-col" },
        fieldShell(id("capacity"), "Introductions at a time", "private", capacity, errors, "capacity"),
        fieldShell(id("selectivity"), "Selectivity", "private", select(id("selectivity"), voc.selectivity, draft.selectivity, (value) => set({ selectivity: value ?? "selective" })), errors, "selectivity",
          "How much of your stated preference must be met. Not published.")),
      fieldShell(id("private_notes"), "Private notes", "private", textArea("private_notes", 500, 3), errors, "private_notes",
        "Never published, never scored and never shown to anyone else.")),
    saveBar);
  form.addEventListener("submit", (event) => { event.preventDefault(); actions.saveBrief(); });
  return h("section", { className: "panel" }, heading, form);
}
