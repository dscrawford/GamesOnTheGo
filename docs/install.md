# Installing GOTG

This installs Nix and makes a **library**: a flake in `~/.config/gotg/library`
that names your server and pins its catalog, each game an output of it
([nix-games.md](nix-games.md)). Nothing goes in a Nix profile. It comes with
no games: the server it names is one you host, filled with games you own and
have dumped yourself or bought digitally. See the note at the top of the
[README](../README.md).

## On a Steam Deck

In Desktop Mode, open Konsole and paste:

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/dscrawford/GamesOnTheGo/master/install.sh | bash
```

It will ask for your password once or twice, and say what for before it asks.
If the Deck says `deck is not in the sudoers file` or refuses to prompt, you
have never set a password on it — run `passwd`, pick one, and start again. It
is the Deck's own password, not your Steam one.

Then restart Steam. **Games On The Go** will be in your library, and works from
Game Mode with a controller.

## Anywhere else without Nix

The same line:

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/dscrawford/GamesOnTheGo/master/install.sh | bash
```

## With Nix already there

GOTG is a flake. Nix 2.30 or newer, with flakes on, needs no installer.

One game, right now, with a token somebody gave you and nothing set up:

```bash
GOTG_TOKEN=… nix run github:dscrawford/GamesOnTheGo#play -- n64.usa.donkey_kong_64
```

That makes the library below in `~/.config/gotg/library`, keeps the token
as `gotg login` would, and runs the game by the library's path. Controllers
still want the udev rule further down; until then it is the keyboard and
the mouse. The rest of this section is the same, by hand and named `gotg`.

Make a library, and edit its `server` and catalog url to yours:

```bash
nix flake new ~/.config/gotg/library -t github:dscrawford/GamesOnTheGo#library
nix run github:dscrawford/GamesOnTheGo#login   # the token, first: the catalog is fetched with it (a netrc)
nix flake lock ~/.config/gotg/library          # by its path: Nix will not write a lock through the registry
nix registry add gotg ~/.config/gotg/library   # so it is gotg#… from anywhere (the installer does this)
nix run gotg#ui                                # the picker, straight from it
```

Where `nix.conf` is not yours to write (home-manager), `gotg login` says
so: give Nix the netrc yourself, `nix.settings.netrc-file =
"~/.config/gotg/netrc"`, or `NIX_CONFIG="netrc-file = …"` for one command.

Upgrade later:

```bash
nix flake update gotg --flake ~/.config/gotg/library && nix run gotg#update
```

Steam needs a built copy to start, which `#update` keeps under
`~/.local/state/gotg`; that is the one thing a plain `nix run` does not do.

home-manager: the library is a flake input like any other, and
`programs.gotg` (`inputs.gotg.homeManagerModules.gotg`) puts its picker and
the games you name on PATH:

```nix
programs.gotg = {
  enable = true;
  library = inputs.library;
  games = [ "n64.usa.donkey_kong_64" "gamecube.usa.super_smash_bros_melee_rev2" ];
};
```

The token stays `nix run gotg#login`'s: an option would copy it into
the world-readable store.

Two things the installer would have done, which you do once by hand:

```bash
nix run gotg#steam -- picker
```

```bash
sudo tee /etc/udev/rules.d/99-gotg-uinput.rules <<'EOF'
KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"
EOF
```

The first puts the picker in Steam; the second lets danstick publish
controllers through `/dev/uinput` (on NixOS: `hardware.uinput.enable = true;`
instead). The installer itself also works here:

```bash
nix run github:dscrawford/GamesOnTheGo#install
```

## Why not an AppImage or a Flatpak

Both were considered and neither fits, for the same reason: **the thing being
installed is Nix.** GOTG is a front end for a flake. Every emulator it runs,
every game environment it builds, comes out of the Nix store — there is no
"application" here to bundle that would work without one.

**Flatpak** is sandboxed. It cannot create `/nix`, cannot write a udev rule,
cannot see `/dev/uinput`, and cannot reach Steam's `shortcuts.vdf`. Each of
those would have to be handed back to the host through `flatpak-spawn --host`,
at which point the sandbox is a costume over a shell script, with a
`flatpak run` in front of it.

**AppImage** bundles an application together with its libraries. This
application's libraries are the Nix store: the bundle would either contain
nothing useful, or contain a second copy of Nix beside the one it just
installed. There is also no `appimagetool` in nixpkgs, so building one would
mean vendoring a prebuilt runtime blob into a flake whose entire point is that
nothing is prebuilt or unpinned.

What actually helps on a Deck is the Steam shortcut, and that is what the
installer writes: `nix run gotg#steam -- picker`.

## What the installer does

Each step checks first, so running it again only upgrades GOTG: the library's
`gotg` pin is moved to the newest and what is built here is rebuilt. To see
what it would do without doing it:

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/dscrawford/GamesOnTheGo/master/install.sh | bash -s -- --dry-run
```

Another server than the default: `bash -s -- --server https://games.example.org`.

| step | why |
| --- | --- |
| install Nix | SteamOS 3.5+ ships `/nix` already, bind-mounted to the home partition and kept across updates; the installer just takes ownership and runs the single-user install. Elsewhere it uses the official multi-user installer. |
| turn on flakes | GOTG is a flake and they are still behind a flag |
| a library | `nix flake new ~/.config/gotg/library -t github:dscrawford/GamesOnTheGo#library`, of `https://gotg.dcraw.net` unless `--server <url>` (or `GOTG_SERVER`) says another; or the one here followed to the newest `gotg` |
| a login | `--claim <url>` redeems an invite link; else the token is asked for. First, because the catalog is a flake input Nix fetches with it |
| `nix run <library>#update` | builds the launcher, the picker and every game already built here into `~/.local/state/gotg`, where Steam starts them; then tells gotg where the library is, which also names it `gotg` in your flake registry: `nix run gotg#ui` |
| leave the profile | `gotg` and `gotg-ui` from an older install are taken out of the Nix profile, where they would shadow the library's copies |
| a udev rule | danstick publishes each controller as a new device through `/dev/uinput`, and cannot open it without permission. The rule tags it `uaccess`, which gives it to whoever is logged in at the seat. |
| a Steam shortcut | so Game Mode can launch the picker ([the gotchas](steam.md)). Steam only takes a new entry while closed, so with Steam open the installer asks, closes it, adds GOTG, and starts it again; declined, the entry is queued and `~/.local/state/gotg/app/bin/gotg steam picker` with Steam closed applies it |

## After a SteamOS update

Your games and everything Nix built survive: they live on the home partition,
which updates do not touch. The **udev rule does not** — it is on the system
partition, and a SteamOS update replaces that wholesale.

Run the installer again. It will find Nix there, upgrade GOTG, and put back the
rule.

## Where things end up

| | |
| --- | --- |
| Nix store | `/nix` — on the Deck, the home partition, via Valve's own bind mount |
| the library | `~/.config/gotg/library` (`flake.lock` is which catalog and which GOTG) |
| the launcher, the picker, each game's build | `~/.local/state/gotg/{app,picker,games}`, GC roots `#update` keeps |
| games and saves | `$XDG_STATE_HOME/gotg`, i.e. `~/.local/state/gotg` |
| the udev rule | `/etc/udev/rules.d/99-gotg-uinput.rules` — the one thing an update removes |

## Uninstalling

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/dscrawford/GamesOnTheGo/master/uninstall.sh | bash
```

Or, with Nix, `nix run github:dscrawford/GamesOnTheGo#uninstall`.

It removes what was built from the library and the library itself, takes the
picker out of Steam, the udev rule off the system partition, and `gotg` and
`gotg-ui` out of the Nix profile where an older install put them. `--dry-run`
shows the steps without taking them.

Games, saves and settings stay in `~/.local/state/gotg` and `~/.config/gotg`
unless you ask:

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/dscrawford/GamesOnTheGo/master/uninstall.sh | bash -s -- --games
```

Nix stays: it is a package manager, not part of GOTG, and other things may use
it. The script ends by printing the one command that removes it on your kind of
machine — `sudo /nix/nix-installer uninstall` where the Determinate installer
put it, `sudo rm -rf /nix …` for a single-user install, or the manual's page
for a daemon install.
