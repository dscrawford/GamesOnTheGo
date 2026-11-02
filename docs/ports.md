# Portable builds: native ports from the web app

A game's library output -- a native port such as Ship of Harkinian or
Rekongpiled, a variant such as `.rando` or `.2p`, or any game on its
emulator -- can be built as **one file that runs on Linux without Nix**,
pushed to the server, and downloaded from the game's page in the web app.

```bash
gotg port push usa.donkey_kong_64            # on a machine with Nix and a library
gotg port push usa.super_mario_64 pc         # a variant
gotg port list                               # what this account has pushed
gotg port pull usa.donkey_kong_64            # or: the web app's Download
gotg port rm usa.donkey_kong_64
```

On the machine without Nix:

```bash
chmod +x gotg-n64.usa.donkey_kong_64.run
./gotg-n64.usa.donkey_kong_64.run login      # once per machine: the server's token
./gotg-n64.usa.donkey_kong_64.run            # the game
```

## The file

`nix build <library>#<output>.gotgPortable` (nix/portable.nix): a shell
header, an uncompressed tar of two static tools (`nix-user-chroot`, `zstd`)
and the output's closure as a tar.zst. The first run unpacks it under
`~/.cache/gotg/portable/<output>-<hash>/` (`GOTG_PORTABLE_DIR` moves it) and
removes an older unpacked build of the same output; every run starts the game
with that directory mounted at `/nix` in a user namespace, so the store paths
are the ones it was built with and nothing is relinked. The host needs
unprivileged user namespaces (SteamOS, Arch, Fedora and Ubuntu have them).

What the game launches is the same `gotg launch --spec` a `nix run` launches:
it downloads the ROM from the server, keeps saves in step, starts the overlay.
So the file holds no game data; it needs `login` for the ROM. The mesa a
machine without `/run/opengl-driver` loads (src/client/env/foreign-gl.nix) is
in the closure, since there is no Nix there to fetch it. That costs most of a
gigabyte, and is why a portable build is far bigger than the port itself.

sm64coopdx (`n64.usa.super_mario_64.pc*`) still compiles against the ROM on
its first launch, on the machine that runs it: its toolchain is in the file.

## The store

`src/gotg/ports.py`, enabled by `GOTG_PORTS_DIR`. Per user, like the saves:
an uploaded file is a program, and one person's upload is never another's
download. The newest build of each output is kept and nothing else.
`GOTG_PORTS_MAX_BYTES` caps one (default 8 GiB). A build is the only body the
service streams to disk rather than reading into memory; a body cut short, or
one that does not match its `X-Gotg-Sha256`, leaves nothing behind.

| Route | |
|---|---|
| `GET /ports` | `{ports: [record]}`, this user's builds |
| `GET /ports/<platform>/<id>` | `{available: [{output, variant, description, build: record\|null}]}` |
| `GET /ports/<output>` | one record: `{output, sha256, size, device, written_at}` |
| `GET /ports/<output>/file` | the bytes; Range and ETag, like `/games` |
| `PUT /ports/<output>` | push one; `Content-Length` required, `X-Gotg-Sha256`, `X-Gotg-Device` |
| `DELETE /ports/<output>` | drop it |

What a game *can* be built as comes from the environment files
(`src/client/env/games`, `GOTG_ENV_GAMES_DIR`; the package sets it): the
game's own file is the default output and adds `emulate`, and each
`<id>.<variant>.nix` is a variant. The description is the file's header
comment, to its first sentence.

## Browser builds: a port played in a tab

A game with a file under `src/client/env/web/<platform>/<id>.nix` can also be
played in the browser as a port compiled to WebAssembly (today: Super Mario
64, through sm64ex's WebGL target). The decomp bakes Nintendo's assets in at
compile time, so the server never builds it and nothing in the repository
holds it: the file is a *builder*, `gotg-web-<name> <rom> <out.js>`, run on
one of the user's own machines against the ROM.

```bash
gotg port worker            # on a machine with Nix and a library; leave it running
gotg port worker --once     # or: build what is asked for now, then exit
```

Pressing **Play the port in browser** on the game's page opens the player with
`?port=web`. With no build yet, the page files a request and says it is
waiting for a machine; a worker polls `GET /ports/requests` (every
`GOTG_PORT_WORKER_INTERVAL` seconds, default 15), claims the request, runs
`nix build <library>#<output>.gotgWeb`, builds against the ROM it downloads
from the server, and pushes the one JavaScript file (SINGLE_FILE: the wasm is
inside it). The page shows which machine is building, then downloads and
starts it. The first build is about five minutes; afterwards Play starts it
straight away. A failed build shows the worker's error on the page, and
reloading asks again. A claim that has not landed in three hours goes back to
pending for any worker.

The builder runs with no network: SDL2, which `-s USE_SDL=2` would download,
is pinned in the store and handed to Emscripten. Emscripten's compiled system
libraries are cached under `~/.cache/gotg/emscripten/`, keyed by compiler.

Builds are per user, like `.run` files, and stored beside them (`web.json`,
`web-<sha>.js`, `request.json`). The service learns which games have a builder
from `GOTG_ENV_WEB_DIR` (the package sets it).

| Route | |
|---|---|
| `GET /ports/requests` | pending requests: what a worker should build |
| `GET /ports/<output>/web` | `{build, request}`, what the player page polls |
| `POST /ports/<output>/web` | ask for a build; the live request if there is one |
| `PUT /ports/<output>/web` | push one, as for `.run`; ends the request |
| `GET /ports/<output>/web/file` | the bytes |
| `POST /ports/<output>/web/claim` | a worker takes the request (409 if it is gone) |
| `POST /ports/<output>/web/fail` | a worker gave up: `{"error": "..."}` |
| `DELETE /ports/<output>/web` | drop the build |
