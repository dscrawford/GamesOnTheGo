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
  const [platform, id] = ctx.segs;
  const games = (await api.catalog()).games;
  const game = platform && games.find((g) => g.platform === platform && g.id === id);
  const picker = h("input", { list: "games", placeholder: "Pick a game or type an env-… name", "aria-label": "game" });
  const options = h("datalist", { id: "games" }, games.slice(0, 2000).map((g) => h("option", { value: `${g.platform}/${g.id}` }, g.title)));
  const body = h("div");
  picker.addEventListener("change", () => {
    const v = picker.value.trim();
    if (ATTR.test(v)) return showAttr(body, v, "this environment");
    location.hash = "#/saves/" + v.split("/").map(encodeURIComponent).join("/");
  });
  root.replaceChildren(h("h1", {}, game ? `Saves: ${game.title}` : "Saves"), h("div", { class: "bar" }, picker, options), body);
  if (!game) { body.replaceChildren(h("p", { class: "dim" }, "Choose a game to see its saves.")); return; }
  body.replaceChildren();
  for (const { attr, scope } of saveAttrs(game)) {
    const section = h("div");
    body.append(section);
    await showAttr(section, attr, scope);
  }
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
