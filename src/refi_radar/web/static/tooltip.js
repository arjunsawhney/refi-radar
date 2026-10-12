// One shared tooltip for the whole page.
// Elements opt in with data-tip (plain text) or data-tip-html (trusted, pre-escaped HTML).

const tip = document.createElement("div");
tip.className = "tooltip";
tip.setAttribute("role", "tooltip");
tip.hidden = true;
document.body.append(tip);

const OFFSET = 14;

function place(x, y) {
  const { width, height } = tip.getBoundingClientRect();
  const left = x + OFFSET + width > window.innerWidth ? x - width - OFFSET : x + OFFSET;
  const top = y + OFFSET + height > window.innerHeight ? y - height - OFFSET : y + OFFSET;
  tip.style.left = `${Math.max(4, left)}px`;
  tip.style.top = `${Math.max(4, top)}px`;
}

function show(target, x, y) {
  if (target.dataset.tipHtml !== undefined) tip.innerHTML = target.dataset.tipHtml;
  else tip.textContent = target.dataset.tip;
  tip.hidden = false;
  place(x, y);
}

function hide() {
  tip.hidden = true;
}

const findTarget = (node) => node instanceof Element && node.closest("[data-tip], [data-tip-html]");

document.addEventListener("mouseover", (e) => {
  const target = findTarget(e.target);
  if (target) show(target, e.clientX, e.clientY);
});
document.addEventListener("mousemove", (e) => {
  if (!tip.hidden && findTarget(e.target)) place(e.clientX, e.clientY);
});
document.addEventListener("mouseout", (e) => {
  const from = findTarget(e.target);
  if (from && !from.contains(e.relatedTarget)) hide();
});
document.addEventListener("focusin", (e) => {
  const target = findTarget(e.target);
  if (!target) return;
  const r = target.getBoundingClientRect();
  show(target, r.left, r.bottom);
});
document.addEventListener("focusout", hide);
document.addEventListener("scroll", hide, true);
