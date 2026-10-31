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
  // Filled in once the service says how this system plays; empty if it has no player.
  const play = h("div", { class: "play" });
  api.playPlan(platform, id).then((plan) => {
    if (plan && plan.mode === "browser") {
      play.replaceChildren(
        h("a", { class: "button primary", href: api.playUrl(platform, id), id: "play" }, "▶ Play in browser"),
        ...(plan.experimental ? [h("span", { class: "dim" }, " experimental: may stutter on slower devices")] : []));
    } else if (plan && plan.nix) {
      play.replaceChildren(
        h("p", { class: "dim", id: "no-play" }, plan.reason || "Not playable in a browser."),
        h("p", {}, "With the Nix launcher: ", h("code", {}, plan.nix)));
    }
  }).catch(() => { /* the page still works without a play button */ });
  // The flake's native ports and variants of this game, filled in when the service answers.
  const ports = h("div", { class: "ports" });
  api.ports(platform, id).then((list) => {
    if (list && list.length) ports.replaceChildren(...portsSection(game, list));
  }).catch(() => { /* the page still works without it */ });
  root.replaceChildren(
    h("p", {}, h("a", { href: "#/" }, "← All games")),
    h("div", { class: "detail" }, art, h("div", {},
      h("h1", {}, game.title),
      h("p", { class: "dim" }, `${game.platform} · ${regionOf(game)} · ${game.handler}`),
      play,
      h("p", {}, h("code", {}, `gotg play ${game.id}`)),
      ports,
      h("p", {}, h("a", { href: `#/saves/${encodeURIComponent(platform)}/${encodeURIComponent(id)}` }, "Saves for this game")),
      h("h2", {}, `Files (${bytes(total)})`),
      h("table", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Size")),
        game.files.map((f) => h("tr", {}, h("td", {}, f.name), h("td", {}, bytes(f.size_bytes))))))));
}

const mib = (n) => `${Math.max(1, Math.round(n / 1048576))} MiB`;

/** One row per output the flake builds for this game: a Download when a client
 * has pushed it (`gotg port push`), and otherwise how to push one. */
export function portsSection(game, list) {
  const push = (variant) => `gotg port push ${game.id}${variant ? ` ${variant}` : ""}`;
  return [
    h("h2", {}, "Native ports & variants"),
    h("p", { class: "dim" }, "Each downloads as one file that runs on Linux without Nix: ",
      h("code", {}, "./file login"), " once per machine, then ", h("code", {}, "./file"), "."),
    h("table", {}, h("tr", {}, h("th", {}, "Build"), h("th", {}, "What it is"), h("th", {}, "Download")),
      list.map((p) => h("tr", { class: "port", "data-output": p.output },
        h("td", {}, h("code", {}, p.variant || "default")),
        h("td", {}, p.description, h("br"), h("small", { class: "dim" }, h("code", {}, `nix run gotg#${p.output}`))),
        h("td", {}, p.build
          ? [h("a", { class: "button primary", href: api.portUrl(p.output), download: `gotg-${p.output}.run` }, `⬇ ${mib(p.build.size)}`),
             h("br"), h("small", { class: "dim" }, `${p.build.device || "unknown machine"} · ${new Date(p.build.written_at).toLocaleDateString()}`)]
          : [h("span", { class: "dim" }, "Not built yet. On a machine with Nix: "), h("code", {}, push(p.variant))])))),
  ];
}
