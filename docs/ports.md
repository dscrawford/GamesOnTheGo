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

## Windows

The same output can be built as a zip that plays on Windows:
`<library>#<output>.gotgWindows` (nix/windows/bundle.nix), named
`gotg-<output>-win64.zip`, whose one directory `gotg-<output>/` holds
`gotg.exe`, `spec.json` and `emulator/` (the emulator's own Windows build).
How it is made, and which games have one, is [docs/windows.md](windows.md).

```bash
gotg port push --windows usa.donkey_kong_64   # on a machine with Nix and a library
gotg port pull --windows usa.donkey_kong_64   # or: the web app's Download for Windows
gotg port rm --windows usa.donkey_kong_64
gotg port list                                # both kinds; the last column says which
```

On Windows: unzip it anywhere, open a terminal in the folder and run
`gotg.exe login` once (it stores the server's token), then double-click
`gotg.exe`, or run it from the terminal. Like a `.run`, the zip holds no game
data: gotg.exe downloads the game, keeps saves in sync with every other
machine, and starts the emulator.

A game whose environment has no Windows build fails to build, with the reason.
The game's page shows its pushed zips beside the `.run`s, and with none, the
`push --windows` to make one.

## The store

`src/gotg/ports.py`, enabled by `GOTG_PORTS_DIR`. Per user, like the saves:
an uploaded file is a program, and one person's upload is never another's
download. The newest build of each output is kept and nothing else.
`GOTG_PORTS_MAX_BYTES` caps one (default 8 GiB), `GOTG_PORTS_MAX_WIN_BYTES` a
Windows zip (default 1 GiB; they run to 65 MB for ares and 100-odd for
Dolphin). A build is the only body the
service streams to disk rather than reading into memory; a body cut short, or
one that does not match its `X-Gotg-Sha256`, leaves nothing behind.

A Windows zip is a second kind beside the `.run`, with its own pointer and
file, kept and dropped on its own:

    <root>/<user>/<output>/current.json   build-<12 hex>.run    the .run
    <root>/<user>/<output>/win.json       win-<12 hex>.zip      the Windows zip

Before its pointer moves, an upload must be a zip (its magic, a central
directory that reads) whose every entry is under `gotg-<output>/`, with
`gotg-<output>/gotg.exe` and `spec.json` among them; nothing is extracted.
Anything else is a 400 and leaves nothing.

| Route | |
|---|---|
| `GET /ports` | `{ports: [record]}`, this user's builds, both kinds |
| `GET /ports/<platform>/<id>` | `{available: [{output, variant, description, build: record\|null}], windows: [{output, variant, build: record}]}` |
| `GET /ports/<output>` | one record: `{kind, output, sha256, size, device, written_at}` |
| `GET /ports/<output>/file` | the bytes; Range and ETag, like `/games` |
| `PUT /ports/<output>` | push one; `Content-Length` required, `X-Gotg-Sha256`, `X-Gotg-Device` |
| `DELETE /ports/<output>` | drop it |
| `GET\|PUT\|DELETE /ports/<output>/win`, `GET /ports/<output>/win/file` | the same for its Windows zip (`kind: "win"`) |

`kind` is `"run"` or `"win"`; a record written before there were two has none
on disk and is served as `"run"`. `windows` is read from the store, not the
environment files: most games are only their emulator, and that plain output
is the one with a zip.

What a game *can* be built as comes from the environment files
(`src/client/env/games`, `GOTG_ENV_GAMES_DIR`; the package sets it): the
game's own file is the default output and adds `emulate`, and each
`<id>.<variant>.nix` is a variant. The description is the file's header
comment, to its first sentence.
