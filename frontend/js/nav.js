/* ---------- 布局交互：分隔条 / icon 导航 ---------- */
const splitter = $("splitter"), panel = $("panel");
splitter.addEventListener("mousedown", e => {
  e.preventDefault();
  splitter.classList.add("drag");
  const move = ev => {
    const target = ev.clientX - panel.offsetLeft;
    panel.style.width = Math.min(560, Math.max(260, target)) + "px";
  };
  const up = () => {
    splitter.classList.remove("drag");
    window.removeEventListener("mousemove", move);
    window.removeEventListener("mouseup", up);
  };
  window.addEventListener("mousemove", move);
  window.addEventListener("mouseup", up);
});

const deck = document.querySelector(".deck");
const marker = document.querySelector(".rail-marker");
const deckCards = [...deck.children];
let curIdx = 0;

function cardH() { return deckCards[curIdx] ? deckCards[curIdx].offsetHeight : 0; }
function goTo(icon) {
  const idx = deckCards.findIndex(c => c.id === icon.dataset.card);
  if (idx < 0) return;
  curIdx = idx;
  deck.style.transform = `translateY(${-idx * cardH()}px)`;
  marker.style.transform = `translateY(${icon.offsetTop + 10}px)`;
  document.querySelectorAll(".rail-ico").forEach(j =>
    j.classList.toggle("active", j === icon));
}
document.querySelectorAll(".rail-ico").forEach(i =>
  i.addEventListener("click", () => goTo(i)));
// 卡片底部翻页按钮：按 data-nav 偏移切卡
document.querySelectorAll(".card-foot .btn").forEach(b =>
  b.addEventListener("click", () => {
    const idx = curIdx + (+b.dataset.nav);
    if (idx < 0 || idx >= deckCards.length) return;
    goTo(document.querySelector(
      `.rail-ico[data-card="${deckCards[idx].id}"]`));
  }));
window.addEventListener("resize", () =>
  deck.style.transform = `translateY(${-curIdx * cardH()}px)`);
goTo(document.querySelector(".rail-ico.active"));
