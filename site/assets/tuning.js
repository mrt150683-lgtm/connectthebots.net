// Match-tuning sliders: show the level name as the owner drags, and fill the
// track up to the thumb. Saving is a plain form post, so the page works
// without this script.
for (const input of document.querySelectorAll(".tuning-row input[type=range]")) {
  const names = [...input.parentElement.querySelectorAll(".tuning-scale span")];
  const show = () => {
    const level = names[Number(input.value)]?.textContent || "";
    input.dataset.level = level;
    input.setAttribute("aria-valuetext", level);
    names.forEach((span, index) => span.classList.toggle("on", index === Number(input.value)));
    const span = Number(input.max) - Number(input.min) || 1;
    input.style.setProperty("--fillw", ((Number(input.value) - Number(input.min)) / span) * 100 + "%");
  };
  input.addEventListener("input", show);
  show();
}
