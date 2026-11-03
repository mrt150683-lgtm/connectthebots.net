// Marks the current page in the signed-in navigation and keeps it in view on
// phones, where the row scrolls sideways. Navigation works without it.
(() => {
  const nav = document.querySelector(".report-nav");
  if (!nav) return;
  const here = location.pathname.replace(/\/+$/, "") || "/";
  let best = null;
  for (const link of nav.querySelectorAll("a[href]")) {
    const path = new URL(link.href, location.href).pathname.replace(/\/+$/, "") || "/";
    const match = here === path || (path !== "/app" && here.startsWith(path + "/"));
    if (match && (!best || path.length > best.path.length)) best = { link, path };
  }
  if (best) {
    best.link.setAttribute("aria-current", "page");
    const left = best.link.offsetLeft - 24;
    if (left > 0) nav.scrollLeft = left;
  }
  const edge = () => nav.classList.toggle("at-end", nav.scrollLeft + nav.clientWidth >= nav.scrollWidth - 4);
  nav.addEventListener("scroll", edge, { passive: true });
  edge();
})();
