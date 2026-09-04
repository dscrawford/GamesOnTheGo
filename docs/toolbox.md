# The toolbox

What already exists, so a change reaches for it instead of rebuilding it in
shell. High level on purpose: each entry says what the thing is for and where
it lives, and the file itself carries the contract.

The rule this document exists to enforce: **if a step, a helper or a check
already does it, use that one.** Hand-rolling an unpack in a `preLaunch` skips
the extraction limits and symlink refusals the step library carries, and the
skip is invisible until something malformed arrives.

## Making a game run — `src/client/env/`

| Reach for | When |
|---|---|
| `<platform>.nix` | every game on a console shares it |
| `games/<platform>/<id>.nix` | one game needs its own settings — merged over the platform |
| `games/<platform>/<id>.<variant>.nix` | a mod or a second engine for one game; `gotg play <id> <variant>` |
| `helpers.nix` | the emulator *shapes* — `aresPlatform`, `harkinianPort`, dolphin, ryujinx, cemu |
| `mods/` | what a mod does to one game's files |
| `lib.nix` | the attributes an environment may set, and what each means |

An environment is data, not a script: `emulator`, `bin`, `args`, `isolate`,
`env`, `path`, `preLaunch`, `saves`/`saveExcludes`, `legacyPaths`, `keys`,
`firmware`, `padConsole`, `configurable`, `recipes`. `args` and `env` take the
placeholders `{target}`, `{install}`, `{state}` and a bare `{fullscreen}`.
`preLaunch` is for what only that port needs; it is not where files get
unpacked.

## Turning a download into a playable file — `src/client/env/steps.nix`

A recipe is a list of steps run left to right, each moving a cursor. The
vocabulary: `verifySfv`, `unrar`, `extract7z`, `unzip`, `pickLargest`,
`pickBase`, `collectExtras`, and the terminal steps `placeTree`,
`keepExtension`, `placeBundle`, `convertRvz`. Three canned chains live in
`helpers.nix` — `sceneArchiveRecipe`, `discArchiveRecipe` and `switchRecipe`.

A catalog entry may carry updates and DLC as members under `extras/<release>/`
(the importer attaches a release named `... Update v1.4.3` or `(DLC)` to its
base game). `collectExtras` unpacks those beside the game and `placeBundle`
installs the pair as `<id>/<id>.<ext>` plus `extras/`; the client resolves the
launch target inside such a directory on its own. What the emulator then does
with the extras is the platform file's job — `switch.nix` registers them with
Ryujinx from the NCA headers (`switch/content.py`) before every launch.

Declare them per handler (`single_file`, `no_intro_set`, `scene_archive`,
`single_archive`) on the environment. A port that reads a bare ROM out of a
No-Intro zip wants `[ steps.unzip steps.placeTree ]`.

**The matching half is `src/client/data/overrides.json`.** `unzip: true` tells
the CLI the installed path is a directory before anything is built, and
`target` is the glob for the file to launch inside it. The recipe and the flag
are useless apart, and `nix/checks/recipes.nix` asserts they agree.

## Packaging something nixpkgs lacks — `pkgs/`

One directory per package, wired into `gotgPkgs` in `src/client/env/default.nix`
so env files reach it without a relative path, and into `flake.nix` if it
should be buildable on its own. Prebuilt upstream binaries want
`autoPatchelfHook`; let it name the missing libraries rather than guessing.

## Checking it works — `gotg qa`

Runs a game headless with a virtual pad and grades the recording on five axes:
boots, audio, video, controller, graphics. `src/client/lib/cmd-qa.sh` assembles
the session, `qa-analyze.sh` grades it, `src/client/qa/` holds the pad, the
in-session script and the container image, and `k8s/qa/` runs it on the
cluster. `docs/qa-platform-plan.md` records what it can and cannot judge.

Its parts are useful alone: the same `cage` + `wf-recorder` + `ffmpeg` in
`qa-tools` will capture a frame of anything headless, which is how a launcher
menu gets read without taking over a display.

## Proving it — `nix/checks/` and `tests/client/`

| Check | Pins |
|---|---|
| `recipes` | the step chains, with heavy tools stubbed |
| `environments`, `platforms` | every environment builds; every platform has one |
| `ares-system`, `fullscreen` | settings that are load-bearing and easy to lose |
| `client-tests` | the bats suite, against a real service |
| `shellcheck`, `ruff` | the shell and the python |

`tests/client/*.bats` is where CLI behaviour goes; `tests/client/helper.bash`
gives every test an isolated HOME and a real service. A check that cannot fail
is worth nothing — break it once to see it bite before trusting it.

## The shapes that keep coming back

- **A first-run gate.** Several ports ask a question once — an asset
  extraction, a ROM picker — through a dialog no unattended run can answer.
  Seed what the gate produces from real state rather than trying to click it;
  `qa_seed_bootstrap` does this for the HarbourMasters archives.
- **State belongs to the environment.** `isolate` points XDG at `{state}`, but
  a port that ignores XDG needs `HOME` redirected instead. Check where it
  actually writes before believing either.
- **Saves are declared, not discovered.** `saves`/`saveExcludes` globs travel
  between machines; anything derived from the ROM does not belong in them.
