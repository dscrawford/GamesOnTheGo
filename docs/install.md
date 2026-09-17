# Installing GOTG

This installs the client. It comes with no games: the library it connects to is
one you host, filled with games you own and have dumped yourself or bought
digitally. See the note at the top of the [README](../README.md).

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

Install both commands into your profile:

```bash
nix profile add github:dscrawford/GamesOnTheGo#gotg github:dscrawford/GamesOnTheGo#gotg-ui
```

Upgrade them later:

```bash
nix profile upgrade gotg gotg-ui
```

Try the picker without installing:

```bash
nix run github:dscrawford/GamesOnTheGo#gotg-ui
```

NixOS or home-manager, as a flake input:

```nix
inputs.gotg.url = "github:dscrawford/GamesOnTheGo";
```

```nix
environment.systemPackages = [
  inputs.gotg.packages.${pkgs.stdenv.hostPlatform.system}.gotg
  inputs.gotg.packages.${pkgs.stdenv.hostPlatform.system}.gotg-ui
];
```

Two things the installer would have done, which you do once by hand:

```bash
gotg steam picker
```

```bash
sudo tee /etc/udev/rules.d/99-gotg-uinput.rules <<'EOF'
KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"
EOF
```

The first puts the picker in Steam; the second lets padmap publish
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
installer writes: `gotg steam picker`.

## What the installer does

Each step checks first, so running it again only upgrades GOTG. To see what it
would do without doing it:

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/dscrawford/GamesOnTheGo/master/install.sh | bash -s -- --dry-run
```

| step | why |
| --- | --- |
| install Nix | SteamOS 3.5+ ships `/nix` already, bind-mounted to the home partition and kept across updates; the installer just takes ownership and runs the single-user install. Elsewhere it uses the official multi-user installer. |
| turn on flakes | GOTG is a flake and they are still behind a flag |
| `nix profile add` / `upgrade` | `gotg` and `gotg-ui`, from `github:dscrawford/GamesOnTheGo`; upgraded in place when already there |
| a udev rule | padmap publishes each controller as a new device through `/dev/uinput`, and cannot open it without permission. The rule tags it `uaccess`, which gives it to whoever is logged in at the seat. |
| a Steam shortcut | so Game Mode can launch the picker. Steam only takes a new entry while closed, so with Steam open the installer asks, closes it, adds GOTG, and starts it again; declined, the entry is queued and `gotg steam picker` with Steam closed applies it |

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
| GOTG and the picker | your Nix profile (`nix profile list`) |
| games and saves | `$XDG_STATE_HOME/gotg`, i.e. `~/.local/state/gotg` |
| the udev rule | `/etc/udev/rules.d/99-gotg-uinput.rules` — the one thing an update removes |

## Uninstalling

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/dscrawford/GamesOnTheGo/master/uninstall.sh | bash
```

Or, with GOTG installed, `gotg-uninstall`; with Nix, `nix run github:dscrawford/GamesOnTheGo#uninstall`.

It takes the two commands out of the Nix profile, the picker out of Steam,
and the udev rule off the system partition. `--dry-run` shows the steps
without taking them.

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
