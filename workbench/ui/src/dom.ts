/**
 * Minimal DOM helpers. Text is always inserted with `textContent` or text
 * nodes, never as HTML, so notice text written by another (fictional) owner
 * cannot inject markup or script. The Content-Security-Policy also forbids
 * inline script as a second layer.
 */

type Child = Node | string | null | undefined | false;
type Attrs = Record<string, string | number | boolean | null | undefined | EventListener>;

export function h<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Attrs = {}, ...children: (Child | Child[])[]): HTMLElementTagNameMap[K] {
  const element = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (name.startsWith("on") && typeof value === "function") {
      element.addEventListener(name.slice(2).toLowerCase(), value);
    } else if (value === true) {
      element.setAttribute(name, "");
    } else if (name === "className") {
      element.className = String(value);
    } else {
      element.setAttribute(name, String(value));
    }
  }
  append(element, children);
  return element;
}

export function append(parent: Element, children: (Child | Child[])[]): void {
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    parent.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
}

/** Decorative two-mark connection motif from the design system; hidden from assistive technology. */
export function brandMark(): SVGSVGElement {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 32 32");
  svg.setAttribute("fill", "none");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", "brand-mark");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", "M12 6H7v20h5M20 6h5v20h-5M11 16h10");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "2.6");
  svg.append(path);
  return svg;
}

export function formatTime(iso: string): string {
  const date = new Date(iso);
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC",
  }).format(date) + " UTC";
}

export function formatDate(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(new Date(iso));
}

let uid = 0;
export function nextId(prefix: string): string {
  uid += 1;
  return `${prefix}-${uid}`;
}

/** Ask for confirmation with a native modal dialog. Escape cancels; focus returns to the opener. */
export function confirmAction(title: string, body: string, confirmLabel: string, tone: "danger" | "primary" = "danger"): Promise<boolean> {
  return new Promise((resolve) => {
    const opener = document.activeElement as HTMLElement | null;
    const titleId = nextId("confirm-title");
    const cancel = h("button", { type: "button", className: "button secondary" }, "Cancel");
    const confirm = h("button", { type: "button", className: `button ${tone}` }, confirmLabel);
    const dialog = h("dialog", { className: "confirm", "aria-labelledby": titleId },
      h("h2", { id: titleId }, title), h("p", {}, body), h("div", { className: "button-row" }, cancel, confirm));
    const finish = (result: boolean) => {
      dialog.close();
      dialog.remove();
      opener?.focus();
      resolve(result);
    };
    cancel.addEventListener("click", () => finish(false));
    confirm.addEventListener("click", () => finish(true));
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); finish(false); });
    document.body.append(dialog);
    dialog.showModal();
    cancel.focus();
  });
}
