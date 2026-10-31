import * as api from "../api.js";
import { h, when } from "../dom.js";
import { users } from "../model.js";

export async function adminView(root, ctx) {
  if (!ctx.admin) { root.replaceChildren(h("div", { class: "banner" }, "Administration needs an admin.")); return; }
  const [tokens, invites, info] = await Promise.all([api.admin.tokens(), api.admin.invites(), api.admin.info().catch(() => ({}))]);
  const minted = h("div");
  const refresh = () => adminView(root, ctx);

  const form = h("form", { class: "bar", onsubmit: async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      const r = await api.admin.mint(f.name.value.trim(), f.user.value.trim(), Number(f.days.value));
      const link = info.public_url ? `${info.public_url}/claim/${r.code}` : "";
      minted.replaceChildren(h("div", { class: "card" }, h("p", {}, `Invite for ${r.name} (shown once):`), h("code", { id: "code" }, r.code),
        link && h("p", { class: "dim" }, "Claim: POST it to ", h("code", {}, `${info.public_url}/claim`))));
    } catch (err) { minted.replaceChildren(h("div", { class: "banner" }, err.message)); }
  } },
  h("input", { name: "name", placeholder: "name", required: true }), h("input", { name: "user", placeholder: "user (optional)" }),
  h("input", { name: "days", type: "number", value: 7, min: 1, max: 3650, "aria-label": "days valid" }),
  h("button", { class: "primary" }, "Mint invite"));

  root.replaceChildren(h("h1", {}, "Admin"),
    h("h2", {}, "Users"),
    h("table", { id: "users" }, h("tr", {}, ["User", "Live tokens", "Tokens", "Open invites"].map((t) => h("th", {}, t))),
      users(tokens, invites).map((u) => h("tr", {}, h("td", {}, u.user), h("td", {}, u.live), h("td", {}, u.tokens), h("td", {}, u.invites)))),
    h("h2", {}, "Invites"), form, minted,
    h("table", { id: "invites" }, h("tr", {}, ["Name", "User", "Created", "Expires"].map((t) => h("th", {}, t))),
      invites.map((i) => h("tr", {}, h("td", {}, i.name), h("td", {}, i.user || "—"), h("td", {}, when(i.created_at)), h("td", {}, when(i.expires_at))))),
    h("h2", {}, "Tokens"),
    h("table", { id: "tokens" }, h("tr", {}, ["Name", "User", "Created", "Last used", "Status", ""].map((t) => h("th", {}, t))),
      tokens.map((t) => h("tr", {}, h("td", {}, t.name), h("td", {}, t.user || "—"), h("td", {}, when(t.created_at)), h("td", {}, when(t.last_used_at)),
        h("td", { class: t.revoked_at ? "bad" : "ok" }, t.revoked_at ? "revoked" : "live"),
        h("td", {}, !t.revoked_at && h("button", { class: "danger", onclick: async () => {
          if (confirm(`Revoke ${t.name}?`)) { await api.admin.revoke(t.name); refresh(); }
        } }, "Revoke"))))));
}
