import * as api from "../api.js";
import { bytes, h } from "../dom.js";
import { regionOf } from "../model.js";

export async function detail(root, ctx) {
  const [platform, id] = ctx.segs;
  const game = (await api.catalog()).games.find((g) => g.platform === platform && g.id === id);
  if (!game) {
    root.replaceChildren(h("div", { class: "banner" }, `No game ${platform}/${id}.`), h("a", { href: "#/" }, "Back"));
    return;
  }
  const art = h("div", { class: "art" });
  api.art(platform, id).then((url) => art.replaceChildren(url ? h("img", { src: url, alt: game.title }) : h("p", { class: "dim" }, "No art")));
  const total = game.files.reduce((n, f) => n + f.size_bytes, 0);
  root.replaceChildren(
    h("p", {}, h("a", { href: "#/" }, "← All games")),
    h("div", { class: "detail" }, art, h("div", {},
      h("h1", {}, game.title),
      h("p", { class: "dim" }, `${game.platform} · ${regionOf(game)} · ${game.handler}`),
      h("p", {}, h("code", {}, `gotg play ${game.id}`)),
      h("p", {}, h("a", { href: `#/saves/${encodeURIComponent(platform)}/${encodeURIComponent(id)}` }, "Saves for this game")),
      h("h2", {}, `Files (${bytes(total)})`),
      h("table", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Size")),
        game.files.map((f) => h("tr", {}, h("td", {}, f.name), h("td", {}, bytes(f.size_bytes))))))));
}
