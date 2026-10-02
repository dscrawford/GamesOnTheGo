"""The admin page: invite somebody, see who holds a token, revoke one.

Served by the admin listener only (the tailnet's), to anybody who can reach
it -- it holds nothing. The admin token is typed into it, kept for the tab
(sessionStorage, gone when the tab is), and sent as the same bearer the CLI
sends; every action is the admin API the CLI already uses.

Three strings rather than files beside this one: the service is stdlib-only
and ships as a wheel, and a module needs no package-data rule to arrive.
The policy allows this page's own script and style and nothing else -- no
inline script, no framing, no third party -- and the script writes what it
is given (names and users came from invites) as text, never as markup.
"""

from __future__ import annotations

POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>GOTG admin</title>
<link rel="stylesheet" href="/admin/app.css">
<script src="/admin/app.js" defer></script>
</head>
<body>
<header>
  <h1>GOTG <span class="dim">admin</span></h1>
  <button id="signout" class="quiet" hidden>Sign out</button>
</header>

<main>
  <section id="login">
    <h2>Sign in</h2>
    <p class="dim">The admin token:
    <code>kubectl get secret gotg-api -o jsonpath='{.data.admin-token}' | base64 -d</code>.
    Kept for this tab only.</p>
    <form id="login-form">
      <input id="token" type="password" autocomplete="off" spellcheck="false" placeholder="admin token" required>
      <button type="submit">Sign in</button>
    </form>
    <p id="login-error" class="error" hidden></p>
  </section>

  <div id="app" hidden>
    <section>
      <h2>Invite someone</h2>
      <form id="invite-form" class="row">
        <label>Name <input id="name" required maxlength="32" pattern="[a-z0-9][a-z0-9_\\-]{0,31}"
          placeholder="alice-deck" autocomplete="off" spellcheck="false"></label>
        <label>User <input id="user" maxlength="32" pattern="[a-z0-9][a-z0-9_\\-]{0,31}"
          placeholder="alice" autocomplete="off" spellcheck="false"></label>
        <label>Days <input id="days" type="number" min="1" max="3650" value="7" required></label>
        <button type="submit">Make invite</button>
      </form>
      <p class="dim">One name per person and device. The user (default: the name up to its first hyphen) is
      whose saves the token reads and writes.</p>
      <p id="invite-error" class="error" hidden></p>
      <div id="minted" class="card" hidden>
        <p><strong id="minted-name"></strong> &mdash; shown once; it works once, until
        <span id="minted-expires"></span>.</p>
        <div class="copy"><input id="minted-link" readonly><button data-copy="minted-link">Copy link</button></div>
        <p class="dim">For them to run, with Nix:</p>
        <div class="copy"><input id="minted-play" readonly><button data-copy="minted-play">Copy</button></div>
        <p class="dim">Or the whole install (a Deck, or Linux without Nix):</p>
        <div class="copy"><input id="minted-install" readonly><button data-copy="minted-install">Copy</button></div>
      </div>
    </section>

    <section>
      <h2>Open invites</h2>
      <table>
        <thead><tr><th>Name</th><th>User</th><th>Made</th><th>Expires</th><th></th></tr></thead>
        <tbody id="invites"></tbody>
      </table>
      <p id="no-invites" class="dim" hidden>None open.</p>
    </section>

    <section>
      <h2>Tokens</h2>
      <label class="dim"><input id="show-retired" type="checkbox"> show revoked and expired</label>
      <table>
        <thead><tr><th>Name</th><th>User</th><th>Token</th><th>Made</th><th>Last used</th>
          <th>State</th><th></th></tr></thead>
        <tbody id="tokens"></tbody>
      </table>
      <p id="no-tokens" class="dim" hidden>No tokens.</p>
    </section>
  </div>
</main>
</body>
</html>
"""

STYLE = """
:root { color-scheme: dark; --bg: #14161a; --panel: #1d2026; --line: #2c3038; --text: #e6e8ec;
  --dim: #9097a3; --accent: #6ab0ff; --bad: #ff7a7a; --good: #7ad49a; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
header { display: flex; align-items: center; justify-content: space-between;
  padding: 14px 20px; border-bottom: 1px solid var(--line); }
h1 { font-size: 18px; margin: 0; }
h2 { font-size: 15px; margin: 0 0 10px; text-transform: uppercase; letter-spacing: .06em; color: var(--dim); }
main { max-width: 980px; margin: 0 auto; padding: 20px; }
section { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 16px 18px; margin-bottom: 18px; overflow-x: auto; }
.dim { color: var(--dim); }
.error { color: var(--bad); }
.good { color: var(--good); }
code { background: #0f1114; padding: 1px 5px; border-radius: 4px; font-size: 13px; word-break: break-all; }
form.row, #login-form { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--dim); }
label:has(input[type=checkbox]) { flex-direction: row; align-items: center; margin-bottom: 8px; }
input { background: #0f1114; color: var(--text); border: 1px solid var(--line); border-radius: 6px;
  padding: 8px 10px; font: inherit; min-width: 0; }
input:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
input:invalid:not(:placeholder-shown) { border-color: var(--bad); }
#token { width: 340px; max-width: 100%; }
#days { width: 80px; }
button { background: var(--accent); color: #08121f; border: 0; border-radius: 6px; padding: 8px 14px;
  font: inherit; font-weight: 600; cursor: pointer; }
button:hover { filter: brightness(1.1); }
button.quiet { background: transparent; color: var(--dim); border: 1px solid var(--line); font-weight: 400; }
button.danger { background: transparent; color: var(--bad); border: 1px solid var(--bad); font-weight: 400;
  padding: 4px 10px; }
.card { border: 1px solid var(--accent); border-radius: 8px; padding: 12px 14px; margin-top: 12px; }
.copy { display: flex; gap: 8px; margin: 6px 0; }
.copy input { flex: 1; font-family: ui-monospace, monospace; font-size: 13px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th { text-align: left; color: var(--dim); font-weight: 500; padding: 6px 8px; border-bottom: 1px solid var(--line); }
td { padding: 7px 8px; border-bottom: 1px solid var(--line); white-space: nowrap; }
td.mono { font-family: ui-monospace, monospace; font-size: 13px; }
tr.retired td { color: var(--dim); }
[hidden] { display: none !important; }
"""

SCRIPT = r"""
"use strict";
(function () {
  const KEY = "gotg-admin-token";
  const $ = (id) => document.getElementById(id);
  let publicUrl = "";

  function token() { return sessionStorage.getItem(KEY) || ""; }

  async function api(method, path, body) {
    const init = { method, headers: { Authorization: "Bearer " + token() }, cache: "no-store" };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const reply = await fetch("/admin/" + path, init);
    let data = {};
    try { data = await reply.json(); } catch (e) { /* an empty or non-JSON body */ }
    if (!reply.ok) {
      const error = new Error(data.error || ("the service answered " + reply.status));
      error.status = reply.status;
      throw error;
    }
    return data;
  }

  function when(seconds) {
    if (seconds === null || seconds === undefined) return "never";
    const delta = seconds - Date.now() / 1000;
    const ago = delta < 0;
    let left = Math.abs(delta);
    const units = [["day", 86400], ["hour", 3600], ["minute", 60]];
    for (const [name, size] of units) {
      if (left >= size) {
        const n = Math.floor(left / size);
        const text = n + " " + name + (n === 1 ? "" : "s");
        return ago ? text + " ago" : "in " + text;
      }
    }
    return ago ? "just now" : "in a moment";
  }

  function exact(seconds) {
    return seconds ? new Date(seconds * 1000).toLocaleString() : "";
  }

  function cell(text, className, title) {
    const td = document.createElement("td");
    td.textContent = text;
    if (className) td.className = className;
    if (title) td.title = title;
    return td;
  }

  function button(label, onClick) {
    const td = document.createElement("td");
    const b = document.createElement("button");
    b.className = "danger";
    b.type = "button";
    b.textContent = label;
    b.addEventListener("click", onClick);
    td.appendChild(b);
    return td;
  }

  async function revoke(name, what) {
    if (!confirm(what)) return;
    try {
      await api("DELETE", "tokens/" + encodeURIComponent(name));
    } catch (error) {
      alert(error.message);
    }
    await refresh();
  }

  function stateOf(t) {
    const now = Date.now() / 1000;
    if (t.revoked_at) return ["revoked", "retired"];
    if (t.expires_at && t.expires_at <= now) return ["expired", "retired"];
    return ["live", ""];
  }

  async function refresh() {
    const [{ invites }, { tokens }] = await Promise.all([api("GET", "invites"), api("GET", "tokens")]);

    const ib = $("invites");
    ib.replaceChildren();
    for (const inv of invites) {
      const tr = document.createElement("tr");
      tr.append(
        cell(inv.name, "mono"),
        cell(inv.user, "mono"),
        cell(when(inv.created_at), "", exact(inv.created_at)),
        cell(when(inv.expires_at), "", exact(inv.expires_at)),
        button("Cancel", () => revoke(inv.name,
          "Cancel the invite for " + inv.name + "? A live token of that name is revoked with it.")),
      );
      ib.appendChild(tr);
    }
    $("no-invites").hidden = invites.length > 0;

    const showRetired = $("show-retired").checked;
    const tb = $("tokens");
    tb.replaceChildren();
    let shown = 0;
    for (const t of tokens) {
      const [state, rowClass] = stateOf(t);
      if (rowClass && !showRetired) continue;
      shown += 1;
      const tr = document.createElement("tr");
      if (rowClass) tr.className = rowClass;
      tr.append(
        cell(t.name, "mono"),
        cell(t.user, "mono"),
        cell(t.display, "mono"),
        cell(when(t.created_at), "", exact(t.created_at)),
        cell(t.last_used_at ? when(t.last_used_at) : "never", "", exact(t.last_used_at)),
        cell(state, state === "live" ? "good" : ""),
        state === "live"
          ? button("Revoke", () => revoke(t.name, "Revoke " + t.name + "? It stops working now."))
          : cell(""),
      );
      tb.appendChild(tr);
    }
    $("no-tokens").hidden = shown > 0;
  }

  function show(signedIn) {
    $("login").hidden = signedIn;
    $("app").hidden = !signedIn;
    $("signout").hidden = !signedIn;
  }

  async function enter() {
    try {
      const info = await api("GET", "info");
      publicUrl = (info.public_url || "").replace(/\/+$/, "");
      await refresh();
      show(true);
      $("login-error").hidden = true;
    } catch (error) {
      sessionStorage.removeItem(KEY);
      show(false);
      if (error.status === 401 || error.status === 403) {
        $("login-error").textContent = "That is not the admin token.";
      } else {
        $("login-error").textContent = error.message;
      }
      $("login-error").hidden = false;
    }
  }

  function copy(event) {
    const input = $(event.target.dataset.copy);
    input.focus();
    input.select();
    const done = () => {
      const label = event.target.textContent;
      event.target.textContent = "Copied";
      setTimeout(() => { event.target.textContent = label; }, 1200);
    };
    // The clipboard API wants a secure context, and the tailnet page is
    // plain http: the old way, from the selection, is the one that works.
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(input.value).then(done, () => { document.execCommand("copy"); done(); });
    } else {
      document.execCommand("copy");
      done();
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("login-form").addEventListener("submit", (event) => {
      event.preventDefault();
      sessionStorage.setItem(KEY, $("token").value.trim());
      $("token").value = "";
      enter();
    });

    $("signout").addEventListener("click", () => {
      sessionStorage.removeItem(KEY);
      $("minted").hidden = true;
      show(false);
    });

    $("show-retired").addEventListener("change", () => refresh().catch((e) => alert(e.message)));

    $("name").addEventListener("input", () => {
      $("user").placeholder = $("name").value.split("-")[0] || "alice";
    });

    $("invite-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const name = $("name").value.trim();
      const body = { name, ttl_days: Number($("days").value) };
      const user = $("user").value.trim();
      if (user) body.user = user;
      try {
        const { code } = await api("POST", "invites", body);
        const link = (publicUrl || location.origin) + "/claim/" + code;
        $("minted-name").textContent = name;
        $("minted-expires").textContent = when(Date.now() / 1000 + body.ttl_days * 86400);
        $("minted-link").value = link;
        $("minted-play").value = "nix run github:dscrawford/GamesOnTheGo#login -- --claim " + link;
        $("minted-install").value =
          "curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/dscrawford/GamesOnTheGo/master/install.sh"
          + " | bash -s -- --claim " + link;
        $("minted").hidden = false;
        $("invite-error").hidden = true;
        $("name").value = "";
        $("user").value = "";
        await refresh();
      } catch (error) {
        $("invite-error").textContent = error.message;
        $("invite-error").hidden = false;
      }
    });

    for (const b of document.querySelectorAll("button[data-copy]")) b.addEventListener("click", copy);

    if (token()) enter(); else show(false);
  });
})();
"""

ROUTES: dict[str, tuple[bytes, str]] = {
    "admin": (PAGE.encode(), "text/html; charset=utf-8"),
    "admin/": (PAGE.encode(), "text/html; charset=utf-8"),
    "admin/app.js": (SCRIPT.encode(), "text/javascript; charset=utf-8"),
    "admin/app.css": (STYLE.encode(), "text/css; charset=utf-8"),
}
