# Playing in the browser

`/play/?platform=<slug>&id=<id>` on the service plays a game in a tab with
[EmulatorJS](https://emulatorjs.org). It streams the ROM from
`/games/<platform>/<id>/<file>` and keeps the save in step with `/saves`.

Who you are comes from the session cookie when the browser is signed in
(`docs/web-auth.md`); the web UI's game page links here with "Play in browser".
Signed out, the page offers "Sign in with your account" (`/auth/login`, which
brings you back to the same game) or a pasted GOTG token. The token is kept in
`sessionStorage` and is gone with the tab; one pasted into the web UI in the
same tab is used too. A token, when present, is sent as a bearer and wins over
the cookie.

Only systems that run at a useful speed in WASM are mapped (`CORES` in
`src/gotg/service/play_page.py`):

| GOTG slug | EmulatorJS system | libretro core |
|---|---|---|
| nes | nes | nestopia |
| snes | snes | snes9x |
| gb, gbc | gb | gambatte |
| gba | gba | mgba |
| genesis | segaMD | genesis_plus_gx |
| n64 (experimental) | n64 | mupen64plus_next |
| ps1 (experimental) | psx | pcsx_rearmed |

Every other platform (gamecube, switch, ...) shows the launcher instead:
`nix run gotg#<platform>.<id>`. `GET /play/plan/<platform>/<id>` (authenticated)
returns that decision as JSON.

## Saves

Pull before start (`GET /saves/<attr>`) and write the battery file into the
core once it starts. Push:
- when EmulatorJS writes the battery file: its "System Save interval" (set to
  30 s by default here) emits `saveSaveFiles`, and an unchanged file is not
  re-pushed;
- on the player's Save State;
- on tab hide (the page asks the core to write the file first) and on close.
  On close, EmulatorJS's own `beforeunload` handler writes the file one last
  time and then unmounts it. The page catches that final `saveSaveFiles` and
  pushes it straight away with `fetch` keepalive (64 KiB cap; larger bundles go
  on the timer). By `pagehide` the file is already gone.
The push carries `X-Gotg-Parent`; a 409 stops pushing and asks which side wins,
it never overwrites silently.

The attr is `env-web-<platform>-<id with . as _>`, per game. The bundle is a
small JSON `{"version":1,"files":{"save.srm":<base64>,"state.bin":<base64>}}`.
It is **not** the tar.zst native clients push; the separate `env-web-` prefix keeps
the two from ever being read as each other, so a browser save does **not** carry
over to a native emulator (the formats differ per emulator anyway).

## EmulatorJS assets: fetched, not vendored

The release is ~300 MB (every core, several builds each). It is not committed.
`scripts/fetch-emulatorjs.sh <dir>` downloads the pinned version (4.2.3) and
checks its sha256; start the service with `GOTG_EJS_DIR=<dir>` and `/play/ejs/`
serves it (same origin, so the page's CSP allows no third-party host). Without
it the page says EmulatorJS is not installed. A Nix derivation (`fetchurl` with the same hash plus
`p7zip`) is the natural deployment form; it is not written here.

Licensing: EmulatorJS is GPL-3.0. The cores keep their own licences (nestopia
GPL-2.0, snes9x non-commercial custom, gambatte GPL-2.0, mgba MPL-2.0,
genesis_plus_gx non-commercial custom, mupen64plus_next GPL-2.0, pcsx_rearmed
GPL-2.0). We do not redistribute them: the operator downloads them from upstream
for their own deployment, which is why this is a script and not a vendor
directory. The snes9x and genesis_plus_gx terms forbid commercial use; check
them before serving anyone but your own household. ROMs and BIOS files are not part
of any of this.

## Limits and what is unverified

- Same-origin only: the ROM comes from this service's `/games`, not a separate
  `files_url` host (that host would need CORS).
- Checked in headless Chromium against EmulatorJS 4.2.3, with homebrew NES and
  SNES test ROMs that keep a boot counter in battery SRAM:
  - The page loads the exact core the plan names (`EJS_core` is `plan.core`;
    passing just the system would give fceumm for NES).
  - Both games run and their saves round-trip: the counter goes up on each
    reload.
  - A second tab gets the 409 dialog, and "overwrite" force-pushes.
  The other systems have not been run.
- The CSP allows `'unsafe-eval'` on `/play` only. Every core ships as a 7z, and
  EmulatorJS's extractor (`compression/extract7z.js`) calls `eval()` in a blob
  worker, which inherits the page's policy. Without it the player hangs at
  "Decompress Game Core".
- When the page is served from `localhost`/`127.0.0.1`, EmulatorJS also tries an
  update check against `cdn.emulatorjs.org`. The CSP blocks it, and the result
  is a harmless console error.
- Multi-disc PS1 sets and BIOS provisioning are not handled.
