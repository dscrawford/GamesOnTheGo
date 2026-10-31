import * as api from "../api.js";
import { h } from "../dom.js";
import * as model from "../model.js";

export function tile(g) {
  const art = h("div", { class: "art" }, g.title.slice(0, 1).toUpperCase());
  api.art(g.platform, g.id).then((url) => url && art.replaceChildren(h("img", { src: url, alt: "", loading: "lazy" })));
  return h("a", { class: "tile", href: `#/game/${encodeURIComponent(g.platform)}/${encodeURIComponent(g.id)}` },
    art, h("div", { class: "t" }, g.title, h("small", {}, `${g.platform} · ${model.regionOf(g)}`)));
}

export async function browse(root, ctx) {
  const games = (await api.catalog()).games;
  const state = { platform: ctx.q.get("platform") ?? "", region: ctx.q.get("region") ?? "", search: ctx.q.get("q") ?? "" };
  let pageNo = Number(ctx.q.get("p") ?? 0);

  const select = (name, label, values) => h("select", { name, "aria-label": label,
    onchange: (e) => { state[name] = e.target.value; pageNo = 0; draw(); } },
    h("option", { value: "" }, `All ${label}s`),
    values.map((v) => h("option", { value: v, selected: state[name] === v }, v)));
  const search = h("input", { type: "search", placeholder: "Search titles", value: state.search, "aria-label": "search",
    oninput: (e) => { state.search = e.target.value; pageNo = 0; draw(); } });
  const results = h("div");
  root.replaceChildren(h("h1", {}, "Games"),
    h("div", { class: "bar" }, select("platform", "platform", model.platforms(games)),
      select("region", "region", model.regions(games)), search), results);

  function draw() {
    const found = model.filter(games, state);
    const last = model.pageCount(found) - 1;
    pageNo = Math.min(pageNo, last);
    const go = (n) => () => { pageNo = n; draw(); };
    results.replaceChildren(
      h("p", { class: "dim", id: "count" }, `${found.length} game${found.length === 1 ? "" : "s"}`),
      h("div", { class: "grid" }, model.page(found, pageNo).map(tile)),
      // Native replaceChildren would print a bare `false`; h() skips it.
      ...(found.length > model.PER_PAGE ? [h("div", { class: "pager" },
        h("button", { disabled: pageNo === 0, onclick: go(pageNo - 1) }, "Previous"),
        `Page ${pageNo + 1} of ${last + 1}`,
        h("button", { disabled: pageNo >= last, onclick: go(pageNo + 1) }, "Next"))] : []));
  }
  draw();
}
