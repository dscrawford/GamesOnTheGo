import * as api from "../api.js";
import { bytes, h, iso } from "../dom.js";
import { saveAttrs } from "../model.js";

const ATTR = /^env-[a-z0-9][a-z0-9_-]*$/;

function download(blob, name) {
  const a = h("a", { href: URL.createObjectURL(blob), download: name });
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

export async function saves(root, ctx) {
  const [first, id] = ctx.segs;
  const [games, list] = await Promise.all([api.catalog().then((c) => c.games), api.saveList()]);
  const game = id && games.find((g) => g.platform === first && g.id === id);
  const picker = h("input", { list: "games", placeholder: "Pick a game to see its saves", "aria-label": "game" });
  const options = h("datalist", { id: "games" }, games.slice(0, 2000).map((g) => h("option", { value: `${g.platform}/${g.id}` }, g.title)));
  const body = h("div");
  picker.addEventListener("change", () => {
    location.hash = "#/saves/" + picker.value.trim().split("/").map(encodeURIComponent).join("/");
  });
  const title = game ? `Saves: ${game.title}` : "Saves";
  root.replaceChildren(h("h1", {}, title), h("div", { class: "bar" }, picker, options), body);
  if (first && !id && ATTR.test(first)) return showAttr(body, first, "this environment");
  if (!game) { body.replaceChildren(overview(list)); return; }
  // The server says which environments exist; the game's own is offered for a first upload.
  const have = new Set(list.map((e) => e.attr));
  const wanted = saveAttrs(game).filter((c, i) => i === 0 || have.has(c.attr));
  const shown = wanted.filter((c) => have.has(c.attr));
  for (const { attr, scope } of shown.length ? shown : wanted.slice(0, 1)) {
    const section = h("div");
    body.append(section);
    await showAttr(section, attr, scope);
  }
}

function overview(list) {
  if (!list.length) return h("p", { class: "dim" }, "Nothing has been pushed yet.");
  return h("table", {}, h("tr", {}, ["Environment", "Head", "Devices", "Written", "Size", ""].map((t) => h("th", {}, t))),
    list.map((e) => h("tr", { "data-attr": e.attr },
      h("td", {}, h("a", { href: `#/saves/${encodeURIComponent(e.attr)}` }, h("code", {}, e.attr))),
      h("td", {}, `gen ${e.head.generation} of ${e.generations}`),
      h("td", {}, (e.devices || []).join(", ") || "—"),
      h("td", {}, iso(e.head.written_at)), h("td", {}, bytes(e.head.size)),
      h("td", {}, e.has_conflict && h("span", { class: "bad" }, "conflict")))));
}

async function showAttr(into, attr, scope) {
  const meta = await api.saveMeta(attr);
  const card = h("div", { class: "card", "data-attr": attr }, h("h2", {}, h("code", {}, attr)), h("p", { class: "dim" }, `Covers ${scope}.`));
  into.replaceChildren(card);
  const note = h("div");
  const refresh = () => showAttr(into, attr, scope);
  const upload = h("input", { type: "file", accept: ".zst,.tar.zst,application/zstd", "aria-label": "bundle" });
  const parentOf = () => (meta ? meta.hash : "");
  const push = async (force) => {
    const file = upload.files[0];
    if (!file) return;
    const result = await api.savePut(attr, file, { parent: upload.dataset.parent ?? parentOf(), force });
    if (result.ok) { note.replaceChildren(h("p", { class: "ok" }, "Uploaded.")); setTimeout(refresh, 400); }
    else conflict(note, result.head, () => push(true), refresh);
  };
  const controls = h("div", { class: "bar" }, upload, h("button", { onclick: () => push(false) }, "Upload as new head"));
  if (!meta) {
    card.append(h("p", {}, "Nothing has been pushed yet."), controls, note);
    return;
  }
  const history = await api.saveHistory(attr);
  card.append(
    h("p", {}, `Head: generation ${meta.generation} from ${meta.device || "unknown device"}, ${iso(meta.written_at)}, ${bytes(meta.size)}`),
    h("table", {}, h("tr", {}, ["Gen", "Device", "Written", "Size", ""].map((t) => h("th", {}, t))),
      history.map((g) => h("tr", { "data-gen": g.generation },
        h("td", {}, g.generation, g.current && h("span", { class: "ok" }, " (head)")), h("td", {}, g.device || "—"),
        h("td", {}, iso(g.written_at)), h("td", {}, bytes(g.size)),
        h("td", {}, h("button", { onclick: async () => download(await api.saveBytes(attr, g.generation), `${attr}-${g.generation}.tar.zst`) }, "Download"),
          !g.current && h("button", { onclick: () => restore(attr, g, meta, note, refresh) }, "Make head"))))),
    h("h3", {}, "Upload a bundle"), controls, note);
}

// Promoting an old generation is a PUT of its bytes on top of the head: a head that moved meanwhile is a 409.
async function restore(attr, gen, head, note, refresh) {
  const result = await api.savePut(attr, await api.saveBytes(attr, gen.generation), { parent: head.hash });
  if (result.ok) { note.replaceChildren(h("p", { class: "ok" }, `Generation ${gen.generation} is the head again.`)); setTimeout(refresh, 400); }
  else conflict(note, result.head, async () => {
    const forced = await api.savePut(attr, await api.saveBytes(attr, gen.generation), { parent: result.head.hash, force: true });
    if (forced.ok) refresh();
  }, refresh);
}

function conflict(note, head, overwrite, refresh) {
  note.replaceChildren(h("div", { class: "banner", role: "alert" },
    h("strong", {}, "Conflict. "),
    `The head moved to generation ${head.generation} (${head.device || "unknown device"}, ${iso(head.written_at)}) since this save was based on it.`,
    h("div", { class: "bar" },
      h("button", { class: "primary", onclick: refresh }, "Keep the head (discard mine)"),
      h("button", { class: "danger", onclick: overwrite }, "Overwrite with mine (the old head stays in history)"))));
}
