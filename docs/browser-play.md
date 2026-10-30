# Playing in the browser

`/play/?platform=<slug>&id=<id>` on the service plays a game in a tab with
[EmulatorJS](https://emulatorjs.org). The page asks for a GOTG token (kept in
`sessionStorage`, gone with the tab), streams the ROM from `/games/<platform>/<id>/<file>`
with it, and keeps the save in step with `/saves`.

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

Pull before start (`GET /saves/<attr>`), push after the battery file or a state
changes (debounced), on the player's Save State, and on tab hide/close
(`fetch` keepalive, which caps at 64 KiB; larger bundles go on the debounce).
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
- The EmulatorJS callbacks (`EJS_onSaveUpdate`, `EJS_onSaveState`,
  `gameManager.getSaveFile/getSaveFilePath/loadSaveFiles`) are the 4.2 API as
  documented; they have not been exercised in a real browser.
- The CSP omits `unsafe-eval`; if a core needs it, it will fail in the console.
- Multi-disc PS1 sets and BIOS provisioning are not handled.
