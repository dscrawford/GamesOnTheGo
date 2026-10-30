// GOTG browser player. No framework, no third-party origin: EmulatorJS is served
// from /play/ejs/ (see docs/browser-play.md). The token is typed in and kept for
// the tab; every request carries it as a bearer, the same as the CLI.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const query = new URLSearchParams(location.search);
  const platform = query.get("platform") || "";
  const gameId = query.get("id") || "";
  const DEVICE = "web";
  const PUSH_DELAY_MS = 5000;

  let token = sessionStorage.getItem("gotg-token") || "";
  let plan = null;
  let parent = ""; // X-Gotg-Hash of the generation this tab descends from
  let dirty = null; // files waiting to be pushed: {name: Uint8Array}
  let pushTimer = null;
  let conflicted = false;

  const status = (text) => { $("status").textContent = text; };
  const fail = (text) => { $("error").textContent = text; $("error").hidden = false; status(""); };
  const auth = () => ({ Authorization: "Bearer " + token });

  // --- the save bundle: {"version":1,"files":{name: base64}} ------------------
  // The store never opens a bundle. A browser's is JSON, under its own attr
  // prefix (env-web-), so a native client can never try to untar it.
  const b64 = (bytes) => {
    let s = "";
    for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return btoa(s);
  };
  const unb64 = (text) => Uint8Array.from(atob(text), (c) => c.charCodeAt(0));
  const pack = (files) => JSON.stringify({
    version: 1,
    files: Object.fromEntries(Object.entries(files).map(([k, v]) => [k, b64(v)])),
  });
  const unpack = (text) => {
    const bundle = JSON.parse(text);
    if (bundle.version !== 1) throw new Error("unknown save bundle version " + bundle.version);
    return Object.fromEntries(Object.entries(bundle.files).map(([k, v]) => [k, unb64(v)]));
  };

  // --- saves: pull before start, push on save / exit ---------------------------
  async function pull() {
    const response = await fetch("/saves/" + plan.save_attr, { headers: auth() });
    if (response.status === 404) return {}; // nothing pushed yet
    if (!response.ok) throw new Error("pulling the save: HTTP " + response.status);
    parent = response.headers.get("X-Gotg-Hash") || "";
    return unpack(await response.text());
  }

  async function push(files, { force = false, keepalive = false } = {}) {
    if (conflicted && !force) return "conflict";
    const body = pack(files);
    const response = await fetch("/saves/" + plan.save_attr + (force ? "?force=1" : ""), {
      method: "PUT",
      headers: { ...auth(), "X-Gotg-Parent": parent, "X-Gotg-Device": DEVICE, "Content-Type": "application/json" },
      body,
      keepalive: keepalive && body.length < 60000, // fetch's keepalive cap is 64 KiB
    });
    if (response.status === 409) {
      conflicted = true;
      $("conflict").hidden = false;
      return "conflict";
    }
    if (!response.ok) throw new Error("pushing the save: HTTP " + response.status);
    parent = (await response.json()).hash || parent;
    lastFiles = files;
    status("saved " + new Date().toLocaleTimeString());
  }

  function queue(name, bytes) {
    dirty = { ...(dirty || {}), [name]: bytes };
    clearTimeout(pushTimer);
    pushTimer = setTimeout(flush, PUSH_DELAY_MS);
  }
  function flush(keepalive = false) {
    clearTimeout(pushTimer);
    if (!dirty) return;
    const sending = dirty;
    dirty = null; // anything queued while this is in flight goes in the next push
    push({ ...lastFiles, ...sending }, { keepalive }).then((outcome) => {
      if (outcome === "conflict") dirty = { ...sending, ...(dirty || {}) }; // kept for "keep mine"
    }).catch((e) => {
      dirty = { ...sending, ...(dirty || {}) };
      fail(e.message);
    });
  }
  let lastFiles = {}; // everything known server-side, so a partial push never drops a file
  let exiting = false;
  const sameBytes = (a, b) => !!b && a.length === b.length && a.every((v, i) => v === b[i]);

  // --- the ROM -----------------------------------------------------------------
  async function findRom() {
    const response = await fetch("/catalog", { headers: auth() });
    if (!response.ok) throw new Error("reading the catalog: HTTP " + response.status);
    const catalog = await response.json();
    const game = catalog.games.find((g) => g.platform === platform && g.id === gameId);
    if (!game) throw new Error("no such game: " + platform + "/" + gameId);
    const ext = (n) => (n.lastIndexOf(".") < 0 ? "" : n.slice(n.lastIndexOf(".")).toLowerCase());
    const files = game.files;
    // The first member in the system's preference order; a lone file is taken as is.
    for (const wanted of plan.exts) {
      const hit = files.find((f) => ext(f.name) === wanted);
      if (hit) return hit;
    }
    if (files.length === 1) return files[0];
    throw new Error("none of " + files.length + " files looks like a " + plan.system + " ROM");
  }

  async function download(file) {
    const url = "/games/" + [platform, gameId, ...file.name.split("/")].map(encodeURIComponent).join("/");
    const response = await fetch(url, { headers: auth() });
    if (!response.ok) throw new Error("fetching the ROM: HTTP " + response.status);
    const total = Number(response.headers.get("Content-Length")) || file.size_bytes || 0;
    const reader = response.body.getReader();
    const chunks = [];
    let got = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      got += value.length;
      status("downloading " + (total ? Math.round((100 * got) / total) + "%" : got + " bytes"));
    }
    return new Blob(chunks);
  }

  // --- EmulatorJS --------------------------------------------------------------
  const SRAM = "save.srm";
  const STATE = "state.bin";

  function start(romBlob, saved) {
    const named = (blob, name) => URL.createObjectURL(new File([blob], name));
    window.EJS_player = "#game";
    window.EJS_core = plan.core || plan.system; // the exact core the plan (and the licence table) names
    window.EJS_pathtodata = "/play/ejs/data/";
    window.EJS_gameUrl = named(romBlob, gameId + (plan.rom_ext || ""));
    window.EJS_gameName = gameId;
    window.EJS_startOnLoaded = true;
    if (saved[STATE]) window.EJS_loadStateURL = URL.createObjectURL(new Blob([saved[STATE]]));

    // EmulatorJS writes the battery file out on its own timer (System Save
    // interval) and once more while it exits on beforeunload; each time it emits
    // "saveSaveFiles" with the bytes. That event is the only reliable hook: by
    // pagehide its exit handler has already unmounted /data/saves.
    window.EJS_defaultOptions = { "save-save-interval": "30" };
    window.EJS_ready = () => {
      status("loading core");
      window.EJS_emulator.on("saveSaveFiles", (bytes) => {
        if (!bytes || !bytes.length || sameBytes(bytes, lastFiles[SRAM])) return;
        queue(SRAM, new Uint8Array(bytes));
        if (exiting) flush(true);
      });
    };
    window.EJS_onGameStart = () => {
      status("playing");
      // Battery file: write the pulled one into the core's filesystem, then have
      // the core re-read it (EmulatorJS 4.2.3; checked in Chromium).
      const gm = window.EJS_emulator && window.EJS_emulator.gameManager;
      if (gm && saved[SRAM]) {
        try {
          gm.FS.writeFile(gm.getSaveFilePath(), saved[SRAM]);
          gm.loadSaveFiles();
        } catch (e) { console.warn("could not restore the battery save", e); }
      }
    };
    // The player's own "Save State" button.
    window.EJS_onSaveState = (e) => {
      if (e && e.state) { queue(STATE, e.state); flush(); }
    };

    const script = document.createElement("script");
    script.src = "/play/ejs/data/loader.js";
    script.onerror = () => fail("EmulatorJS is not installed on this service (docs/browser-play.md).");
    document.body.appendChild(script);
  }

  // Tab hidden (switching away, phone locking): the core is still alive, so ask
  // it to write the battery file now; its saveSaveFiles event queues the bytes.
  function pushOnExit() {
    const gm = window.EJS_emulator && window.EJS_emulator.gameManager;
    try { if (gm && window.EJS_emulator.started) gm.saveSaveFiles(); } catch (e) { /* core already gone */ }
    flush(true);
  }

  async function run() {
    if (!/^[a-z0-9][a-z0-9_-]*$/.test(platform) || !/^[a-z]{3,5}\.[a-z0-9_]+$/.test(gameId)) {
      return fail("open this page as /play/?platform=<slug>&id=<id>");
    }
    if (!token) { $("login").hidden = false; return; }
    $("login").hidden = true;
    status("checking");
    const response = await fetch("/play/plan/" + platform + "/" + gameId, { headers: auth() });
    if (response.status === 401) { sessionStorage.removeItem("gotg-token"); token = ""; $("login").hidden = false; return fail("that token was refused"); }
    if (!response.ok) return fail("HTTP " + response.status);
    plan = await response.json();

    if (plan.mode !== "browser") {
      $("nix-title").textContent = gameId;
      $("nix-command").textContent = plan.nix;
      $("nix").hidden = false;
      return status("");
    }
    try {
      status("pulling save");
      lastFiles = await pull();
      const rom = await findRom();
      plan.rom_ext = rom.name.includes(".") ? rom.name.slice(rom.name.lastIndexOf(".")) : "";
      start(await download(rom), lastFiles);
    } catch (e) { fail(e.message); }
  }

  $("login-form").addEventListener("submit", (e) => {
    e.preventDefault();
    token = $("token").value.trim();
    sessionStorage.setItem("gotg-token", token);
    run();
  });
  $("keep-server").addEventListener("click", () => location.reload());
  $("keep-mine").addEventListener("click", () => {
    conflicted = false; $("conflict").hidden = true;
    push({ ...lastFiles, ...(dirty || {}) }, { force: true }).catch((e) => fail(e.message));
  });
  // Registered before EmulatorJS loads, so this runs before its own
  // beforeunload handler, whose final saveSaveFiles must push at once.
  window.addEventListener("beforeunload", () => { exiting = true; });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") pushOnExit(); });
  window.addEventListener("pagehide", pushOnExit);
  run();
})();
