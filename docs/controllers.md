# Controllers — research findings and implementation plan

Goal: the flake carries the controller support. Clone GOTG on any Linux machine,
run `gotg`, and a Wii U GameCube adapter, a Steam Controller and an Xbox pad all
work — with each physical controller landing on the console port you asked for,
decided in nix rather than in an emulator's GUI. The machine's own configuration
should not have to know that any of this exists.

Everything under "Findings" was verified on 2026-08-01 against kernel 6.18.38 /
NixOS 26.11 and against emulator, SDL and systemd source in `/nix/store`, not
from documentation alone. Claims that are inferred say so.

---

## 1. Findings

### 1.1 The privilege boundary — the one thing that decides the architecture

A user-level flake can ship binaries, config and environment variables. It
cannot install udev rules or load kernel modules. So the question that matters
is: **which of these controllers need privileged setup at all?**

Verified against systemd 261's own rules and the live ACLs on this machine:

| Device class | Needs root? | Why |
|---|---|---|
| Xbox pads (wired, xpadneo BT, any evdev/js pad) | **No** | systemd's `70-uaccess.rules:61` is `SUBSYSTEM=="input", ENV{ID_INPUT_JOYSTICK}=="?*", TAG+="uaccess"` — every joystick is ACL'd to the logged-in user on any systemd distro, out of the box. Confirmed live: `/dev/input/js0` carries `user:daniel:rw-`. |
| Steam Controller | **Yes** | SDL drives it over `hidraw`, and hidraw has no default uaccess rule. |
| Wii U GC adapter | **Yes** | Dolphin claims it over libusb, needing rw on `/dev/bus/usb/BBB/DDD`. No default rule. |
| GC adapter → virtual pads (for non-Dolphin emulators) | **Yes** | Needs `/dev/uinput` writable. |

And the good news: **one package covers all three privileged cases.**
`pkgs.steam-devices-udev-rules` is a single self-contained
`lib/udev/rules.d/60-steam-input.rules` (168 lines, no runtime deps) that grants
`uaccess` for `28de:*` on usb + hidraw + input (Steam Controller), for
`057e:0337` (Wii U GC adapter), and for uinput —
`KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"`,
which also creates the node.

So the privileged surface is exactly **one store path, symlinked into
`/etc/udev/rules.d`**. That is small enough to be reproducible and auditable,
and it is the *only* thing the flake cannot do by itself.

On this machine it is already in place (via `programs.steam.enable` →
`hardware.steam-hardware`), which is why `/dev/hidraw1` and
`/dev/bus/usb/001/003` currently show `user:daniel:rw-`.

### 1.2 Your Steam Controller is the new one, and the kernel won't drive it

`/sys/bus/usb` reports `28de:1304 "Steam Controller Puck"` — the 2nd-generation
receiver, carrying up to four controllers on HID interfaces `if02`–`if05`.

- **The kernel will not give you a gamepad for it.** `hid-steam` in
  `torvalds/linux` master claims only `0x1102` (wired SC), `0x1142` (SC dongle)
  and `0x1205` (Steam Deck). `0x1304` is absent. Matches the live state:
  `/dev/input/by-id` has only `-event-kbd` / `-event-mouse` nodes for the puck,
  i.e. it is in lizard mode.
- **SDL3 will.** `sdl3-3.4.10` — the exact SDL that nixpkgs `ares` and
  `dolphin-emu` link against (verified with `nix eval .buildInputs`) — ships
  `src/joystick/hidapi/SDL_hidapi_steam_triton.c`, which claims
  `USB_PRODUCT_VALVE_STEAM_PROTEUS_DONGLE = 0x1304`, turns lizard mode off, and
  exposes a full gamepad including grips and touchpads.
- Gated on `SDL_HINT_JOYSTICK_HIDAPI_STEAM` → falls back to
  `SDL_HINT_JOYSTICK_HIDAPI` → `SDL_HIDAPI_DEFAULT`, which is `true` on Linux.
  It should work unconfigured; set `SDL_JOYSTICK_HIDAPI_STEAM=1` anyway to pin it.

**This is a flake-side win:** support for the new Steam Controller comes from
*which SDL the emulator was built against*, which the flake pins. A host with an
older SDL gets nothing; a host running GOTG's emulators gets it regardless of
what the host has installed. Exactly the property you want.

Consequence for the design: an SDL-hidapi controller has **no evdev node**.
Dolphin's `evdev` backend, Steam Input, `input-remapper` and `evsieve` cannot
see it at all. Only SDL can. Dolphin must bind that pad on its `SDL` source.

### 1.3 The Wii U GC adapter has two mutually exclusive modes

Not a HID device; it needs a driver holding it over libusb, and exactly one
process can.

**Mode A — Dolphin native.** `Dolphin.ini` `[Core] SIDevice0..3 = 12`, where 12
is `SIDEVICE_WIIU_ADAPTER` counted off the `SIDevices` enum in
`Source/Core/Core/HW/SI/SI_Device.h`. Adapter port *N* → console port *N*, with
rumble and correct analog triggers. **Player allocation is free and
deterministic** — nothing to generate. Right mode for GameCube and Wii.

**Mode B — userspace evdev bridge.** `wii-u-gc-adapter` (ToadKing /
dperelman) or `gcadapter-evdev` (PMArkive) claim the adapter and create up to
four uinput pads that every other emulator can see. **Neither is in nixpkgs**
(`nix search nixpkgs wii-u-gc` → 0 hits), so the flake must package it. Needed
to use GC pads with `ares` (SNES/N64/…).

The modes cannot coexist, so which one applies is a per-platform property and
belongs in `client/env/<platform>.nix`.

### 1.4 How each emulator names a controller — the crux of allocation

**Dolphin**, `GCPadNew.ini`, one `[GCPadN]` per port:

```ini
[GCPad1]
Device = evdev/0/Xbox Wireless Controller
```

`<source>/<index>/<name>`; `index` only disambiguates identical names. Distinct
models are stable by name alone. Source is `evdev` or `SDL` (both compiled in).

**ares**, `settings.bml`, under `Input/Controller.Port.N/Gamepad/`. From
`desktop-ui/input/input.cpp` and `ruby/input/joypad/sdl.cpp`:

```
assignment = "<identifier>/<groupID>/<inputID>"
identifier = "<SDL GUID>/<slot>"
slot       = count of already-seen devices with the same GUID
```

Different models → perfectly stable and templatable. **Identical controllers are
distinguished only by `slot`**, i.e. SDL's enumeration order. That is the entire
difficulty, and it is bounded: it only bites for four identical GC pads or two
identical Xbox pads.

SDL's Linux order is not arbitrary — `src/joystick/linux/SDL_sysjoystick.c`
scans `/dev/input` with `alphasort` then sorts by device number, and
`LINUX_ScanSteamVirtualGamepads()` runs *first*, sorting Steam virtual gamepads
by Steam slot.

### 1.5 Steam Input is not the right foundation here

Tempting: it normalises everything to a virtual Xbox 360 pad, has a reorder UI,
and applies to non-Steam shortcuts — which is how `gotg install` tells you to
launch games. And SDL3 does honour its slot ordering (§1.4), so the old
"Steam's order comes out reversed in RetroArch" complaint
([steam-for-linux#10830](https://github.com/ValveSoftware/steam-for-linux/issues/10830),
open since 2024, no Valve response) looks like an SDL2-era problem. *That last
part is inference from source, not an end-to-end test.*

But against the goal stated at the top of this document:

- **It is a host dependency, not a flake dependency** — it needs Steam installed,
  running, and the game launched from Steam. That is precisely the thing this
  work is meant to eliminate.
- **It cannot see the GC adapter** unless Mode B is already running, so it does
  not solve the case you care about most.
- **It erases device identity.** Every pad becomes `Steam Virtual Gamepad` with
  the same GUID, so ares' `GUID/slot` degenerates to slot ordering alone —
  *less* deterministic than what the flake can build directly.
- **It fights SDL for the Steam Controller**, whose hidraw node Steam claims.
- `gotg play` from a terminal and `nix flake check` would behave differently from
  the Steam path.

Treat it as **one input source among several**, in a late optional phase: when
Steam Virtual Gamepads are present, map player *N* to virtual slot *N* and
honour Steam's reorder UI; otherwise ignore it entirely.

### 1.6 Levers, all of which are plain environment variables

| Lever | Effect |
|---|---|
| `SDL_JOYSTICK_HIDAPI_STEAM=1` | force-enable the Steam Controller / puck driver |
| `SDL_GAMECONTROLLER_IGNORE_DEVICES=0xVID/0xPID,…` | hide devices from an emulator |
| `SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT=…` | whitelist — show *only* these |
| `SDL_HIDAPI_IGNORE_DEVICES` | keep SDL off a device so another driver can claim it |

Every SDL3 hint is readable as an environment variable of the same name, so all
of these drop straight into the existing `env = { … }` attribute of
`client/env/lib.nix`. No new mechanism required.

nixpkgs packages worth knowing: `sc-controller` (userspace driver for the *old*
Steam Controller), `sdl-jstest`, `jstest-gtk`, `linuxConsoleTools`, `evsieve`,
`input-remapper`, `antimicrox`, `game-devices-udev-rules`,
`linuxKernel.packages.linux_6_18.gcadapter-oc-kmod` (1 ms adapter polling).

---

## 2. What the flake delivers, and the one thing it cannot

Three tiers. The design principle: push as much as possible into tier 1, make
tier 2 a single reviewable store path, keep tier 3 genuinely optional.

### Tier 1 — pure flake, no privileges, works anywhere

- Emulators, pinned, with the SDL that has the new Steam Controller driver.
- The SDL hints of §1.6, set per environment.
- `gotg-pads`, the enumerator (Phase 2).
- The GC adapter bridge binary (Phase 5).
- Roster resolution and per-emulator port-binding generation.
- **Any ordinary joystick — every Xbox pad — is fully functional at this tier**,
  because systemd already ACLs joystick nodes to the logged-in user (§1.1).

### Tier 2 — one udev rules symlink, needed only for the Steam Controller, the GC adapter, and uinput

Exported from the flake so it is versioned with everything else:

```nix
# flake.nix
packages.controller-udev-rules = pkgs.steam-devices-udev-rules;   # or a GOTG-specific subset
nixosModules.controllers = { ... };                               # for NixOS hosts
```

Two delivery paths, same store path underneath:

- **NixOS** — `imports = [ gotg.nixosModules.controllers ];` sets
  `services.udev.packages` and `boot.kernelModules = [ "uinput" ]`. Fully
  declarative, no manual step.
- **Anything else** (Debian, Fedora, Arch, Steam Deck) — `gotg controllers
  install-rules` prints and, with `--apply`, runs one command:

  ```
  sudo ln -sfn /nix/store/…-steam-devices-udev-rules/lib/udev/rules.d/60-steam-input.rules \
               /etc/udev/rules.d/60-steam-input.rules
  sudo udevadm control --reload && sudo udevadm trigger
  ```

  Still reproducible: the target is a store path, so the rules are pinned by
  `flake.lock` like everything else. `gotg doctor` re-checks that the symlink
  points at the *current* store path and tells you when a flake update has moved
  it.

This step is unavoidable. Granting a user raw USB and hidraw access is a
system-level security decision; no unprivileged process may make it. The honest
thing is to make it one command, make it auditable, and detect when it is
missing — not to pretend it can be avoided.

### Tier 3 — optional host modules, only for hardware that needs a kernel driver

`hardware.xone.enable` (Xbox Wireless **dongle** only — it blacklists `xpad`,
so it changes how wired pads enumerate) and `gcadapter-oc-kmod` (1 ms polling).
Neither is needed for the three controllers in scope. `gotg doctor` should
mention them only when it detects hardware that wants them.

---

## 3. `gotg controllers scan` — the hardware scan

One command, two audiences: a human wanting to know why player 3 is dead, and
the roster generator wanting a machine-readable device list.

What it inspects — all readable unprivileged:

| Source | Yields |
|---|---|
| `/sys/bus/usb/devices/*/{idVendor,idProduct,product,serial}` | what is physically attached, including devices with no input node (the GC adapter, a lizard-mode puck) |
| `/dev/input/by-id`, `/dev/input/event*`, `js*` | evdev/js pads and their stable names |
| SDL3 enumeration (via `gotg-pads`) | GUID, ares-style `slot`, hidapi-only devices, Steam virtual gamepads |
| `access()` on the relevant nodes | whether permissions are actually granted |
| `/proc/modules`, `/proc/config.gz` | whether `hid_xpadneo`, `xone`, `uinput` etc. are present |

Human output — classify each device and, when unusable, say exactly why and what
fixes it:

```
Wii U GameCube adapter  057e:0337   detected, usable (native, Dolphin)
Steam Controller Puck   28de:1304   detected, NOT usable
    /dev/hidraw1 is not readable — udev rules missing
    fix: gotg controllers install-rules --apply
Xbox Wireless Controller            detected, usable   js0  GUID 030000005e04…
```

JSON output feeds the roster (§4):

```
gotg controllers scan --json > client/data/controllers.json
```

Rules for the implementation:

- **Never require root to scan.** Report a permission failure as a finding, not
  as an error. Scanning must work on a machine where nothing is set up yet —
  that is its main use.
- Distinguish *detected* from *usable*. A lizard-mode puck is present in sysfs
  and invisible to evdev; saying "no controller found" would be wrong and would
  send someone debugging the wrong layer.
- Keep the known-device table (VID/PID → friendly name, which mode applies,
  which tier of setup it needs) in one data file, not scattered through the
  scanner.
- `gotg doctor` is the same scan plus the tier-2 symlink freshness check.

---

## 4. Design: scan → roster → resolve → generate

Four steps, mirroring how the repo already separates "what a platform is" from
"what a game changes about it".

1. **Scan** (§3) produces the ground truth about this machine.
2. **Roster** — `client/env/controllers.nix`, generated from a scan and then
   hand-edited for seating. Static, declarative, reviewable. This is the policy:
   who is player 1.
3. **Resolve** — at launch, `preLaunch` (already in `env/lib.nix`) matches the
   roster against what is connected *right now*.
4. **Generate** — write the emulator's port bindings into `$state`.

Why not pure nix: the set of connected devices, and `slot` (§1.4), are only
knowable at runtime. Why not pure runtime: the seating policy must stay
declarative and diffable, which is the idiom of this repo.

```nix
# client/env/controllers.nix — the roster
{
  players = [
    { id = "xbox";  sdlName = "Xbox Wireless Controller"; }
    { id = "steam"; vidpid  = "28de:1304"; }
    { id = "gc1";   gcPort  = 1; }
    { id = "gc2";   gcPort  = 2; }
  ];
  order = [ "xbox" "steam" "gc1" "gc2" ];   # a platform or game may override
}
```

```nix
# client/env/gamecube.nix — gains one attribute
{ pkgs, ... }:
{
  emulator = pkgs.dolphin-emu;
  bin = "dolphin-emu";
  args = [ "-e" "{target}" ];
  controllers = { backend = "dolphin"; gcAdapter = "native"; };
}
```

`gcAdapter = "native"` selects Mode A (§1.3) and makes the generator emit
`SIDevice0..3 = 12` rather than per-port bindings; `"evdev"` selects Mode B and
adds the bridge to `path` plus a `preLaunch` line that starts it.

Matching precedence, most to least specific: `vidpid`+serial → `guid` →
`sdlName` → `gcPort`. First match wins; a device fills at most one seat.

---

## 5. Plan for the implementing agent

Ordered so each phase is independently useful and testable. Phase 1 alone makes
Xbox pads work with zero host setup; Phases 1–4 are the full deliverable for the
three controllers in scope.

### Phase 0 — verify the two assumptions this plan rests on

Before writing code:

1. With Steam **not** running, plug in the puck and run
   `SDL_JOYSTICK_HIDAPI_STEAM=1 nix run nixpkgs#sdl-jstest -- --list`.
   Expect a gamepad. If not, §1.2 is wrong for this firmware — **stop and
   report**, because the whole Steam Controller story changes.
2. Check whether one puck exposes its four `if0N` interfaces as four independent
   SDL gamepads or only one. `/dev/input/by-id` shows four interface groups, but
   with a single controller paired that is not conclusive.

### Phase 1 — tier 1 and tier 2 plumbing, no allocation yet

The smallest change that makes controllers work at all from the flake.

1. Export from `flake.nix`:
   - `packages.controller-udev-rules` — `pkgs.steam-devices-udev-rules`
     (consider a GOTG-specific subset containing only the `28de`, `057e:0337`
     and uinput rules; smaller to audit, and it does not claim VR hardware).
   - `nixosModules.controllers` — `services.udev.packages` + `boot.kernelModules
     = [ "uinput" ]`, plus `hardware.xone.enable` and `gcadapter-oc-kmod` as
     opt-in options defaulting off.
2. Add `SDL_JOYSTICK_HIDAPI_STEAM = "1"` to the `env` of every environment that
   uses an SDL emulator — one line each in the platform files.
3. `gotg controllers install-rules [--apply]` for non-NixOS hosts (§2, tier 2).
4. Document in `README.md`: NixOS imports the module; everyone else runs one
   command; Xbox pads need neither.

**Acceptance:** on a machine with no GOTG-related host config, an Xbox pad works
in ares immediately, and the Steam Controller works after the single
`install-rules` command.

### Phase 2 — the scan

1. `gotg-pads` — a small SDL3 program (flake package, also used by `preLaunch`)
   printing connected pads as JSON:

   ```json
   [{"instance":1,"guid":"03000000…","slot":0,"name":"Xbox Wireless Controller",
     "vid":"045e","pid":"0b13","evdev":"/dev/input/event24","steamSlot":null}]
   ```

   Compute `slot` **exactly** as ares does — count prior devices with the same
   GUID in SDL enumeration order (`ruby/input/joypad/sdl.cpp:126-152`). Getting
   this wrong silently swaps identical controllers. Report `steamSlot` for Steam
   Virtual Gamepads (`28de:11ff`). Link `pkgs.sdl3`; keep it under ~150 lines.

   Do **not** shell out to `sdl-jstest`: its output is not a stability contract
   and it will not give you ares' slot semantics.

2. `gotg controllers scan [--json]` per §3 — sysfs + by-id + `gotg-pads` +
   permission probes + module checks, with the known-device table in one data
   file alongside `client/data/overrides.json`.

3. `gotg doctor` — the scan plus the tier-2 symlink freshness check.

**Acceptance:** on a machine with the udev rules deliberately removed, the scan
still runs, reports the puck as *detected but not usable*, and names the exact
fix.

### Phase 3 — roster and resolver

1. `client/env/controllers.nix` per §4, with eval-time validation rejecting a
   duplicate `id` or an unknown `id` in `order`.
2. Extend `client/env/lib.nix` with an optional `controllers` argument. When
   absent, behaviour must be **byte-identical** to today — verify by comparing
   store paths of the existing envs before and after.
3. `client/lib/pads.sh` (or a `gotg` subcommand) — read roster + `gotg-pads`,
   emit `player→device` for fillable seats, skip the rest, log unfilled seats
   clearly. **Never fail a launch because player 3 is absent.**

### Phase 4 — emulator generators

One file per backend under `client/env/pads/`, each a pure function from
`{ roster, resolved }` to file contents.

**`dolphin.nix`** — do this first, it is the easy one.
- `gcAdapter = "native"`: write `[Core] SIDevice0..3 = 12` into `Dolphin.ini`.
  Nothing further; ports map themselves.
- Otherwise: `[GCPad1..4] Device = <source>/<index>/<name>` in `GCPadNew.ini`,
  with `source = SDL` for the Steam Controller (§1.2 — no evdev node) and
  `evdev` for everything else.
- Reuse the button block already in `~/.config/dolphin-emu/GCPadNew.ini` as the
  template rather than inventing one.

**`ares.nix`** — the fiddly one.
- Bindings are `<GUID>/<slot>/<groupID>/<inputID>` (§1.4) under
  `Input/Controller.Port.N/Gamepad/*` in `settings.bml`.
- Group/input numbering is undocumented. **Do not guess.** Bind one controller
  by hand in ares' UI, diff the resulting `settings.bml`, and template that with
  `<GUID>/<slot>` as the substituted part. Record the captured numbering in a
  comment beside the template.
- `settings.bml` is whole-file: seed once via `isolate = true` + `configFiles`,
  then rewrite only the `Input` subtree in `preLaunch`.

Note the tension with the existing `configFiles` contract, which seeds a file
once and then leaves it alone so in-emulator changes survive. Port bindings must
be regenerated every launch. Keep them in separate files where the emulator
allows it; where it does not (ares), rewrite in place and **say so in a comment**
— do not quietly break the "your settings survive" promise.

### Phase 5 — the GC adapter bridge (Mode B)

Only needed to use GC pads outside Dolphin.

1. Package `wii-u-gc-adapter` in the flake — small C, needs libusb and libudev.
   Check whether ToadKing or dperelman is the live fork.
2. Add it to `path`; start it from `preLaunch` for `gcAdapter = "evdev"`
   platforms, with a trap that stops it on exit.
3. Refuse to start it when the platform declares `gcAdapter = "native"`, with an
   error naming both settings.
4. The four uinput pads are created in adapter-port order at startup, so `eventN`
   order — hence ares' `slot` — should follow adapter port order. *Inference from
   how uinput allocates nodes; verify empirically, including after replugging a
   pad mid-session.*

### Phase 6 — Steam Input, optional

Only after 1–5 work. When `gotg-pads` reports `steamSlot`, map player *N* to
`steamSlot == N-1` and skip roster matching for those devices. Add a per-env
`steamInput = false` escape hatch setting
`SDL_GAMECONTROLLER_IGNORE_DEVICES=0x28de/0x11ff`, so a launcher added to Steam
can bypass Steam Input when it gets in the way.

### Phase 7 — tests

Following `client/tests/` (bats, real files, no network):

- `scan.bats` — the scanner against recorded sysfs/by-id trees: rules present;
  rules absent; puck in lizard mode; nothing attached.
- `pads.bats` — the resolver against recorded `gotg-pads` JSON: four seats
  filled; two of four; two identical Xbox pads; Steam virtual gamepads; empty.
- Golden-file tests per generator: fixture JSON in, expected `GCPadNew.ini` /
  `settings.bml` fragment out.
- A `checks` entry asserting envs without a `controllers` attribute build to the
  same store path as before Phase 3.

Hardware cannot be exercised in `nix flake check`. Keep every hardware-dependent
step behind fixtures, and note in each fixture file which real device it was
captured from and when.

---

## 6. Open questions to resolve early

1. Does the puck enumerate as a gamepad under SDL3 with Steam stopped?
   (Phase 0. Everything about the Steam Controller depends on it.)
2. Four independent SDL gamepads per puck, or one?
3. Does ares distinguish two identical Xbox pads across a replug, or does `slot`
   shuffle? If it shuffles, identical pairs need a `by-id`-serial udev symlink
   pinning `eventN` order — which would push that case into tier 2.
4. Is `dperelman/wii-u-gc-adapter` or `ToadKing/wii-u-gc-adapter` the live fork?
5. Is a GOTG-specific udev subset worth maintaining over just using
   `steam-devices-udev-rules` wholesale? Smaller audit surface versus one more
   thing to keep current as Valve adds devices.

---

## 7. Sources

Verified locally (strongest evidence):

- `sdl3` 3.4.10 source: `src/joystick/hidapi/SDL_hidapi_steam_triton.c`,
  `src/joystick/usb_ids.h`, `src/joystick/linux/SDL_sysjoystick.c`,
  `src/joystick/hidapi/SDL_hidapijoystick_c.h`
- `ares` 148 source: `desktop-ui/input/input.cpp`, `ruby/input/joypad/sdl.cpp`
- `dolphin-emu` 2606 source: `Source/Core/Core/HW/SI/SI_Device.h`
- `systemd` 261 source: `70-uaccess.rules`, `70-joystick.rules`,
  `60-persistent-hidraw.rules`
- `steam-devices-udev-rules` 1.0.0.61 package output
- nixpkgs NixOS modules `hardware/steam-hardware.nix`, `hardware/xone.nix`
- Host: `/proc/config.gz`, `/sys/bus/usb/devices/*`, `/dev/input/by-id`,
  ACLs on `/dev/hidraw*`, `/dev/bus/usb/001/003`, `/dev/input/js0`
- `torvalds/linux` master `drivers/hid/hid-ids.h` (fetched)

Web:

- [SDL3 SDL_HINT_JOYSTICK_HIDAPI_STEAM](https://wiki.libsdl.org/SDL3/SDL_HINT_JOYSTICK_HIDAPI_STEAM)
- [SDL3 SDL_HINT_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT](https://wiki.libsdl.org/SDL3/SDL_HINT_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT)
- [SDL3 environment variables](https://wiki.libsdl.org/SDL3/EnvironmentVariables)
- [Dolphin — using the official GC adapter for Wii U](https://dolphin-emu.org/docs/guides/how-use-official-gc-controller-adapter-wii-u/)
- [Dolphin — configuring controllers](https://dolphin-emu.org/docs/guides/configuring-controllers/)
- [ToadKing/wii-u-gc-adapter](https://github.com/ToadKing/wii-u-gc-adapter) ·
  [dperelman fork](https://github.com/dperelman/wii-u-gc-adapter) ·
  [PMArkive/gcadapter-evdev](https://github.com/PMArkive/gcadapter-evdev)
- [steam-for-linux#10830 — Steam Input controller order for non-Steam games](https://github.com/ValveSoftware/steam-for-linux/issues/10830)
- [Arch Wiki — Gamepad](https://wiki.archlinux.org/title/Gamepad)
- [NixOS Wiki — Dolphin Emulator](https://wiki.nixos.org/wiki/Dolphin_Emulator)
- [Batocera — remapping controls per emulator](https://wiki.batocera.org/remapping_controls_per_emulator)
  (prior art for the roster + per-emulator generator model)
