# Controller support — implementation plan

Execution plan for the work described in [controllers.md](controllers.md).
That document is the research and the *why*; this one is the task list. Section
references below like "(§1.2)" point into it.

**Objective.** Clone GOTG on any Linux machine, run `gotg`, and a Wii U
GameCube adapter, a Steam Controller and an Xbox pad all work, with each
physical controller landing on the console port declared in nix. The host's own
configuration should not have to know any of this exists.

---

## 0. Before you start

### 0.1 Two things the plan rests on, neither verified on hardware

Run these first. If either comes out differently, stop and report — the design
changes, not just the code.

1. **Does the new Steam Controller enumerate under SDL3 without Steam?**
   With Steam **not** running, puck plugged in:
   ```bash
   SDL_JOYSTICK_HIDAPI_STEAM=1 nix run nixpkgs#sdl-jstest -- --list
   ```
   Expect a gamepad. The research says yes — `sdl3-3.4.10` ships
   `SDL_hidapi_steam_triton.c` claiming `28de:1304` (§1.2) — but no one has
   watched it happen. Everything about the Steam Controller depends on this.

2. **One puck: four SDL gamepads, or one?** `/dev/input/by-id` shows four
   interface groups (`if02`–`if05`), but with a single controller paired that
   proves nothing. Pair a second controller if possible.

Record both results in this file before writing code.

#### Results — 2026-08-01

**Both come out as hoped. The design stands; nothing here changes.**

Measured with Steam shut down, the puck attached, and a Bluetooth pad paired.
Output of `gotg-pads` (built for this, see below):

```json
[{"instance":1,"name":"Steam Controller","guid":"03002854de2800000413000002006800",
  "slot":0,"vid":"28de","pid":"1304","gamepad":true,
  "path":"/dev/hidraw1","evdev":null,"steamSlot":null},
 {"instance":2,"name":"Xbox 360 Controller","guid":"050018dc5e0400008e02000030110000",
  "slot":0,"vid":"045e","pid":"028e","gamepad":true,
  "path":"/dev/input/event257","evdev":"/dev/input/event257","steamSlot":null}]
```

1. **Yes, the puck enumerates under SDL3 without Steam** — as a *gamepad*, named
   "Steam Controller", `28de:1304`. And `evdev` is `null` while `path` is
   `/dev/hidraw1`: the hidapi-only claim in §1.2 is confirmed rather than
   assumed, which is what forces `source = SDL` in the Dolphin generator (§4.1).

2. **One puck is one SDL gamepad, not four.** `/dev/input/by-id` shows five
   interface groups (`if02`–`if06`, not `if02`–`if05` as assumed above), each
   with a keyboard and mouse node — but SDL reports exactly one gamepad for the
   device. Those groups are lizard-mode endpoints, not separate pads.

**The check as written could not have answered this.** `sdl-jstest` links real
SDL2 (`ldd` shows no SDL3), and the triton driver is an SDL3 driver, so that
command cannot see the puck whatever the hint says. §0.1 needed the tool that
task 2.1 builds, so 2.1 was pulled forward to answer it. Do not reintroduce
`sdl-jstest` anywhere.

Not yet measured: **two identical pads** (§2 risk row). Both devices here have
distinct GUIDs, so `slot` was 0 for each and the counting path is untested
against the case it exists for.

Also worth recording: this host already carries
`/etc/udev/rules.d/60-steam-input.rules` and every puck `hidraw` node is
user-readable, so the Phase 1 acceptance criterion — "works after exactly one
`install-rules --apply`" — cannot be observed here. It needs a machine without
the rules, or the deliberate removal that Phase 2's acceptance already calls for.

### 0.2 A conflict already in the repo

`client/templates/launcher.sh.tpl:16-19` currently does:

```bash
export SDL_JOYSTICK_HIDAPI=0
export SDL_JOYSTICK_DISABLE_UDEV=0
unset SDL_GAMECONTROLLER_IGNORE_DEVICES
```

Two consequences that shape this work:

- **`SDL_JOYSTICK_HIDAPI=0` disables the driver the new Steam Controller needs.**
  The triton driver's `IsEnabled()` is
  `SDL_GetHintBoolean(SDL_HINT_JOYSTICK_HIDAPI_STEAM, SDL_GetHintBoolean(SDL_HINT_JOYSTICK_HIDAPI, default))`,
  so with `HIDAPI=0` and no explicit `HIDAPI_STEAM`, the puck stays a
  keyboard-and-mouse.
  **Fix: set `SDL_JOYSTICK_HIDAPI_STEAM=1` explicitly** — an explicit hint wins
  over the fallback. Verified in source that `HIDAPI_JoystickInit()` runs
  regardless of `SDL_HINT_JOYSTICK_HIDAPI`; that hint only feeds per-driver
  `IsEnabled()`. So the existing line does **not** need removing, and should not
  be removed — see the next point.
- **Today's controller support is Steam-Input-dependent.** The template's own
  comment says these lines exist "so SDL sees the Steam Input virtual gamepad".
  That is the current working path for whoever is playing today. **Do not
  regress it.** Every phase below must leave the launch-from-Steam path working
  at least as well as it does now.

---

## 1. Task list

Dependencies in the right column. Phases 1–4 are the deliverable; 5–7 are
follow-on.

| # | Task | Depends on |
|---|---|---|
| 1.1 | Export `packages.controller-udev-rules` and `nixosModules.controllers` | — |
| 1.2 | `SDL_JOYSTICK_HIDAPI_STEAM=1` in every SDL environment | — |
| 1.3 | `gotg controllers install-rules [--apply]` | 1.1 |
| 1.4 | README: NixOS module vs. one-command path | 1.1–1.3 |
| 2.1 | `gotg-pads` — SDL3 enumerator, JSON out | 0.1 |
| 2.2 | `gotg controllers scan [--json]` | 2.1 |
| 2.3 | `gotg doctor` | 2.2, 1.3 |
| 3.1 | `client/env/controllers.nix` — roster schema + validation | — |
| 3.2 | `controllers` argument in `client/env/lib.nix` | 3.1 |
| 3.3 | `client/lib/pads.sh` — resolver | 2.1, 3.1 |
| 4.1 | `client/env/pads/dolphin.nix` | 3.2, 3.3 |
| 4.2 | `client/env/pads/ares.nix` | 3.2, 3.3 |
| 5.1 | Package `wii-u-gc-adapter` | — |
| 5.2 | Bridge wiring in `preLaunch` + mode guard | 5.1, 3.2 |
| 6.1 | Steam Input as an input source | 2.1, 3.3 |
| 7.x | Tests | each phase |

---

## Phase 1 — make controllers work from the flake

The smallest change that delivers the objective for Xbox pads and the Steam
Controller. No allocation logic yet.

### 1.1 Export the udev rules and a NixOS module

The privileged surface is exactly one store path (§1.1, §2 tier 2).

In `flake.nix`, inside the existing `packages = forAllSystems (pkgs: …)`:

```nix
controller-udev-rules = pkgs.steam-devices-udev-rules;
```

and, as a new top-level output beside `packages`:

```nix
nixosModules.controllers =
  { config, lib, pkgs, ... }:
  let cfg = config.programs.gotg.controllers; in
  {
    options.programs.gotg.controllers = {
      enable = lib.mkEnableOption "controller support for GOTG";
      xboxDongle = lib.mkEnableOption "the xone driver, for the Xbox Wireless USB dongle";
      gcAdapterOverclock = lib.mkEnableOption "1 ms polling for the Wii U GameCube adapter";
    };
    config = lib.mkIf cfg.enable {
      services.udev.packages = [ pkgs.steam-devices-udev-rules ];
      boot.kernelModules = [ "uinput" ];
      hardware.xone.enable = lib.mkIf cfg.xboxDongle true;
      boot.extraModulePackages =
        lib.mkIf cfg.gcAdapterOverclock [ config.boot.kernelPackages.gcadapter-oc-kmod ];
    };
  };
```

Notes:
- `xboxDongle` must stay **off by default**: `hardware.xone` blacklists `xpad`
  and `mt76x2u`, which changes how *wired* Xbox pads enumerate (§1.1). Say so in
  the option description.
- The module must be a no-op when a host already has `programs.steam.enable`
  — it will, since both go through `services.udev.packages`, but check that
  enabling both does not warn or conflict.
- **Open question (§6.5):** whether to ship a GOTG-specific rules subset
  (`28de:*`, `057e:0337`, uinput only) instead of the whole Valve file. Smaller
  audit surface; one more thing to keep current. Use the full package for now
  and leave a comment pointing at the question.

### 1.2 Set the Steam Controller hint

Add to the `env` attribute of every environment whose emulator uses SDL — today
that is all of them, since nixpkgs `ares` and `dolphin-emu` both link
`sdl3-3.4.10`:

```nix
env = { SDL_JOYSTICK_HIDAPI_STEAM = "1"; };
```

Put it in one place rather than eight: add a `baseEnv` in `client/env/lib.nix`
merged under each environment's own `env`, so a platform can still override it.
Keep the existing merge semantics from `client/env/default.nix` — `env` is one
of the two attributes merged rather than replaced.

Per §0.2, this is what makes the puck work **even inside the Steam launcher**,
which sets `SDL_JOYSTICK_HIDAPI=0`.

### 1.3 `gotg controllers install-rules`

New `client/lib/cmd-controllers.sh`, sourced from `bin/gotg` alongside the other
`cmd-*.sh`, following the existing dispatch style.

Without `--apply`, print the exact commands and exit 0:

```
sudo ln -sfn /nix/store/…-steam-devices-udev-rules/lib/udev/rules.d/60-steam-input.rules \
             /etc/udev/rules.d/60-steam-input.rules
sudo udevadm control --reload && sudo udevadm trigger
```

With `--apply`, run them. Requirements:

- Resolve the store path by building `<flake>#controller-udev-rules` through the
  existing `nix_bin` seam in `client/lib/env.sh`, so the tests can stub it.
- **Detect NixOS and refuse**: if `/etc/NIXOS` exists, point at
  `nixosModules.controllers` instead. Hand-symlinking into `/etc/udev/rules.d`
  on NixOS gets clobbered and is the wrong advice.
- Be idempotent — re-running when the symlink already points at the current
  store path should say so and change nothing.
- Never run `sudo` implicitly without `--apply`.

### 1.4 README

A short section under "Using the client":
- NixOS: `imports = [ gotg.nixosModules.controllers ]; programs.gotg.controllers.enable = true;`
- Everything else: `gotg controllers install-rules --apply`
- Xbox pads need neither — systemd already grants joystick access (§1.1).

### Acceptance for Phase 1

On a machine with no GOTG-related host configuration:
- an Xbox pad works in ares immediately, with no privileged step;
- the Steam Controller works after exactly one `install-rules --apply`;
- launching through Steam still behaves at least as well as before (§0.2).

---

## Phase 2 — the scan

### 2.1 `gotg-pads`

A small SDL3 program, a flake package, also called from `preLaunch` later.
Output:

```json
[{"instance":1,"guid":"03000000…","slot":0,"name":"Xbox Wireless Controller",
  "vid":"045e","pid":"0b13","evdev":"/dev/input/event24","steamSlot":null}]
```

Hard requirements:

- **Compute `slot` exactly as ares does**: count previously-seen devices with
  the same GUID, in SDL enumeration order. The algorithm is
  `ruby/input/joypad/sdl.cpp:126-152`; copy its logic, not its spirit. Getting
  this wrong silently swaps identical controllers, which is the single most
  annoying possible bug in this project.
- Report `steamSlot` for Steam Virtual Gamepads (`28de:11ff`), for Phase 6.
- Report `evdev: null` for hidapi-only devices — the Steam Controller has no
  evdev node (§1.2), and downstream code must be able to tell.
- Link `pkgs.sdl3`. Under ~150 lines; it is a dumper, not a library.
- Set `SDL_JOYSTICK_HIDAPI_STEAM=1` internally so it sees what the emulators see.

Do **not** shell out to `sdl-jstest`: its output is not a stability contract and
it cannot give you ares' slot semantics.

### 2.2 `gotg controllers scan [--json]`

Sources, all readable unprivileged:

| Source | Yields |
|---|---|
| `/sys/bus/usb/devices/*/{idVendor,idProduct,product,serial}` | attached devices with no input node — the GC adapter, a lizard-mode puck |
| `/dev/input/by-id`, `event*`, `js*` | evdev/js pads and stable names |
| `gotg-pads` | GUID, ares `slot`, hidapi-only devices, Steam slots |
| `access()` on the nodes | whether permission is actually granted |
| `/proc/modules` | `hid_xpadneo`, `xone`, `uinput` presence |

Human output classifies each device and, when unusable, names the fix:

```
Wii U GameCube adapter  057e:0337   detected, usable (native, Dolphin)
Steam Controller Puck   28de:1304   detected, NOT usable
    /dev/hidraw1 is not readable — udev rules missing
    fix: gotg controllers install-rules --apply
Xbox Wireless Controller            detected, usable   js0  GUID 030000005e04…
```

Rules:
- **Never require root.** A permission failure is a finding, not an error.
  Scanning a machine where nothing is set up yet is the main use.
- **Distinguish *detected* from *usable*.** A lizard-mode puck is in sysfs and
  invisible to evdev; "no controller found" would send someone debugging the
  wrong layer.
- Keep the known-device table (VID/PID → name, mode, tier of setup needed) in
  one data file next to `client/data/overrides.json`, not scattered through the
  scanner.

### 2.3 `gotg doctor`

The scan, plus: does the tier-2 symlink exist, and does it point at the
**current** store path? A flake update moves it, and the failure mode — "it
worked last week" — is worth catching explicitly.

### Acceptance for Phase 2

With the udev rules deliberately removed, `scan` still runs, reports the puck as
*detected but not usable*, and names the exact fix.

---

## Phase 3 — roster and resolver

### 3.1 `client/env/controllers.nix`

```nix
{
  players = [
    { id = "xbox";  sdlName = "Xbox Wireless Controller"; }
    { id = "steam"; vidpid  = "28de:1304"; }
    { id = "gc1";   gcPort  = 1; }
    { id = "gc2";   gcPort  = 2; }
  ];
  order = [ "xbox" "steam" "gc1" "gc2" ];
}
```

Fail at **eval** time on a duplicate `id`, an unknown `id` in `order`, or a
matcher with no recognised key. An eval error names the file and line; a runtime
error surfaces as a dead controller mid-game.

Generated by `gotg controllers scan --json` and then hand-edited for seating —
the scan supplies the identifiers, the human supplies the policy.

### 3.2 `controllers` argument in `client/env/lib.nix`

New optional argument alongside `isolate`, `configFiles` and the rest:

```nix
controllers ? null,   # { backend = "dolphin" | "ares"; gcAdapter ? "native" | "evdev"; }
```

**When `null`, behaviour must be byte-identical to today.** Verify by comparing
store paths of every existing environment before and after the change — same
input, same hash. Put that comparison in `checks` (task 7.x) so it stays true.

### 3.3 `client/lib/pads.sh`

Read the roster and `gotg-pads` output; emit `player→device` for the seats it
can fill; skip the rest.

- Matching precedence, most to least specific: `vidpid`+serial → `guid` →
  `sdlName` → `gcPort`. First match wins; a device fills at most one seat.
- **Never fail a launch because player 3 is absent.** Log unfilled seats and
  carry on. Someone launching a single-player game with one pad plugged in must
  not be blocked by a roster entry for a controller in a drawer.

---

## Phase 4 — emulator generators

One file per backend under `client/env/pads/`, each a pure function from
`{ roster, resolved }` to file contents. Pure functions are testable with golden
files; side-effecting shell is not.

### 4.1 `dolphin.nix` — do this first

- `gcAdapter = "native"`: write `[Core] SIDevice0..3 = 12` into `Dolphin.ini`.
  12 is `SIDEVICE_WIIU_ADAPTER` (§1.3). Nothing else — adapter port *N* maps to
  console port *N* by itself, so there are no bindings to generate and no
  ordering to get wrong.
- Otherwise: `[GCPad1..4] Device = <source>/<index>/<name>` in `GCPadNew.ini`,
  with `source = SDL` for the Steam Controller (§1.2 — no evdev node) and
  `evdev` for everything else.
- Reuse the button block already in `~/.config/dolphin-emu/GCPadNew.ini` as the
  template. Do not invent a mapping.

### 4.2 `ares.nix` — the fiddly one

Bindings are `<GUID>/<slot>/<groupID>/<inputID>` under
`Input/Controller.Port.N/Gamepad/*` in `settings.bml` (§1.4).

**Decoded from source, 2026-08-01 — the hand-capture is no longer needed for the
format.** ares 148, `desktop-ui/input/input.cpp` and `ruby/input/joypad/sdl.cpp`:

```
<identity>/<slot>/<groupID>/<inputID>[/<qualifier>]
```

- `identity` — the SDL GUID string, or `VID:<vid>|PID:<pid>` **in decimal** when
  the GUID comes back all zeros, with ares substituting its own generic ids
  (vendor `0x0000`, product `0x0003`) for a missing one. `inputAssignment()`
  picks this branch whenever the device has an identifier at all.
- `slot` — how many earlier devices in SDL enumeration order share that
  identity. `gotg-pads` now emits `identity` and `slot` precomputed, exactly as
  the driver computes them, so no generator has to reassemble either.
- `groupID` — enumerated in `nall/hid.hpp`: **Axis 0, Hat 1, Trigger 2,
  Button 3**.
- `inputID` — index within that group. The driver appends axes, then hats, then
  buttons, each `0..n`.
- `qualifier` — `Lo`/`Hi` for an axis or hat direction, `Rumble` for rumble.

Cross-checked against EmuDeck's shipped `configs/dev.ares.ares/.../settings.bml`,
where `0x3/3/6` reads as Button 6 and `0x3/1/1/Lo` as Hat 1 low — consistent.

**The `0x<hex>/...` form is not the gamepad form.** It is the fallback
`inputAssignment()` uses for a device with no identifier, which is why the only
device id in a freshly written `settings.bml` is `0x1`, the keyboard. An earlier
reading of that file suggested ares did not use GUIDs at all; it does, for
gamepads, and that reading was wrong.

**Bindings are per console, not global.** The real path is
`<Console>/Input/Controller.Port.N/Gamepad/<Button>` — `SuperFamicom`,
`Nintendo64`, `MegaDrive` and so on each carry their own `Controller.Port.1..4`.
One generated file therefore has to cover every console a platform set uses;
binding a pad for SNES does not bind it for N64. This is not reflected in the
task estimates above.

Still worth one hand-capture: confirming the input index ordering a specific pad
reports, since `inputID` is positional within its group. Record the ares version
beside anything captured that way.

`settings.bml` is whole-file: seed once via `isolate = true` + `configFiles`,
then rewrite only the `Input` subtree in `preLaunch`.

### The `configFiles` tension — decide it deliberately

`client/env/lib.nix` seeds a file once and then leaves it alone, "so that
settings changed in the emulator's own UI survive". Port bindings must be
regenerated every launch. These are in direct conflict.

Keep bindings in separate files where the emulator allows it. Where it does not
(ares), rewrite in place and **document it in a comment at the rewrite site** —
do not quietly break the promise the existing code makes.

---

## Phase 5 — GC adapter bridge (Mode B)

Only needed to use GC pads outside Dolphin — ares, i.e. SNES/N64 multiplayer.

1. Package `wii-u-gc-adapter` in the flake. Small C, needs libusb and libudev.
   Determine whether `ToadKing` or `dperelman` is the live fork (§6.4).
2. Add to `path`; start from `preLaunch` for `gcAdapter = "evdev"` platforms,
   with a trap that stops it on exit.
3. **Guard the mode collision.** Refuse to start when the platform declares
   `gcAdapter = "native"`, with an error naming both settings. Only one process
   can hold the adapter (§1.3); the failure is otherwise silent and confusing.
4. The four uinput pads are created in adapter-port order at startup, so `eventN`
   order — hence ares' `slot` — should follow adapter port order. *This is
   inference. Verify empirically, including after replugging a pad mid-session.*

---

## Phase 6 — Steam Input as one input source

Only after 1–5 work.

When `gotg-pads` reports `steamSlot` values, map player *N* to
`steamSlot == N-1` and skip roster matching for those devices. Add a per-env
`steamInput = false` escape hatch setting
`SDL_GAMECONTROLLER_IGNORE_DEVICES=0x28de/0x11ff`.

Note the existing launcher template already tunes SDL for the Steam Input path
(§0.2). This phase makes that path explicit and selectable rather than implicit.

---

## Phase 7 — tests

Following `client/tests/` — bats, real files, no network, isolated `HOME` via
`setup_env` in `helper.bash`.

- `scan.bats` — recorded sysfs/by-id trees: rules present; rules absent; puck in
  lizard mode; nothing attached.
- `pads.bats` — the resolver against recorded `gotg-pads` JSON: four seats
  filled; two of four; two identical Xbox pads; Steam virtual gamepads; empty.
- `controllers.bats` — `install-rules` output; idempotent re-run; NixOS refusal.
  Stub `nix` through the existing `GOTG_NIX` seam.
- Golden files per generator: fixture JSON in, expected `GCPadNew.ini` /
  `settings.bml` fragment out.
- A `checks` entry asserting environments without a `controllers` attribute
  build to the same store path as before Phase 3 (task 3.2).

Hardware cannot be exercised in `nix flake check`. Keep every hardware-dependent
step behind fixtures, and note in each fixture which real device it was captured
from and when.

---

## 2. Risks

| Risk | Handling |
|---|---|
| The puck does not enumerate under SDL3 (§0.1) | Verify first. If it fails, the Steam Controller falls back to the Steam Input path and Phase 6 becomes mandatory rather than optional. |
| ares' `slot` shuffles across replug for identical pads | Phase 0-adjacent test with two identical pads. If it shuffles, identical pairs need a `by-id`-serial udev symlink pinning `eventN` order — which pushes that case into tier 2 and changes the tier-1 promise. |
| Phase 3.2 silently changes existing environment hashes | The `checks` entry in Phase 7 exists for this. Add it in Phase 3, not Phase 7, if it is cheap. |
| Regressing the current Steam launch path | §0.2. Test a Steam-launched shortcut at the end of every phase, not just at the end. |
| `configFiles` promise broken quietly | Phase 4 decision, documented at the rewrite site. |

---

## 3. Sequencing

Phase 1 is independently shippable and delivers the headline objective for two
of the three controllers. Do not start Phase 3 before Phase 2 — the roster
schema should be shaped by what the scanner actually finds on real hardware,
not the other way round.

Phases 5 and 6 are genuinely optional: Phase 4's `gcAdapter = "native"` covers
GameCube and Wii, which is where four-player GC pads matter most.
