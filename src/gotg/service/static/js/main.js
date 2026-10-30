import * as api from "./api.js";
import { h } from "./dom.js";
import { browse } from "./views/browse.js";
import { detail } from "./views/detail.js";
import { saves } from "./views/saves.js";
import { adminView } from "./views/admin.js";

const view = document.getElementById("view");
const ctx = { who: null, admin: false };

// "#/game/n64/usa.foo?x=1" -> {segs:["game","n64","usa.foo"], q:URLSearchParams}
export function parse(hash) {
  const [path, query = ""] = hash.replace(/^#\/?/, "").split("?");
  return { segs: path.split("/").filter(Boolean).map(decodeURIComponent), q: new URLSearchParams(query) };
}

const routes = {
  "": browse, game: detail, saves, admin: adminView,
};

function notSignedIn() {
  view.replaceChildren(
    h("div", { class: "card" },
      h("h2", {}, "Sign in"),
      h("p", {}, "You need to sign in to see the library."),
      h("a", { href: api.loginUrl(), id: "login" }, "Sign in"),
      h("details", {}, h("summary", { class: "dim" }, "Use a token instead"),
        h("form", { onsubmit: (e) => { e.preventDefault(); api.setBearer(e.target.t.value.trim()); location.reload(); } },
          h("input", { name: "t", type: "password", placeholder: "bearer token" }), h("button", {}, "Use")))));
}

async function render() {
  const { segs, q } = parse(location.hash);
  const name = segs[0] ?? "";
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === (name === "game" ? "" : name) || (name === "" && a.dataset.nav === "browse")));
  view.replaceChildren(h("p", { class: "dim" }, "Loading…"));
  try {
    await (routes[name] ?? browse)(view, { ...ctx, segs: segs.slice(1), q });
  } catch (e) {
    if (e.login) return notSignedIn();
    view.replaceChildren(h("div", { class: "banner" }, `Something went wrong: ${e.message}`));
  }
}

async function start() {
  try {
    ctx.who = await api.me();
    ctx.admin = await api.isAdmin(ctx.who);
    document.getElementById("who").textContent = ctx.who.user || ctx.who.name;
    document.querySelector('[data-nav="admin"]').hidden = !ctx.admin;
  } catch (e) {
    if (e.login) return notSignedIn();
    view.replaceChildren(h("div", { class: "banner" }, `Cannot reach the service: ${e.message}`));
    return;
  }
  addEventListener("hashchange", render);
  render();
}

start();
