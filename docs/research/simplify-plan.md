# chore/simplify: the plan

*2026-10-07. Five surveys (bash client, Nix, service/indexer, picker, Rust),
each read-only, each asked for three things: comments that restate the code
or have gone stale, KISS/DRY opportunities with the check that guards each,
and testing gaps. The house style stays: a comment that says what broke, why
this shape, and what it cost to find out is kept. What goes is the comment
that says what the next line says, the pointer to a file that is not there,
and the paragraph copied into six game files.*

## Phase A — dead code and comments (no behaviour change)

| Area | What | Guard |
|---|---|---|
| bash | 9 uncalled functions (`config_set`, `config_write`, `env_pinned`, `manifest_files_url`, `need_cmd`, `validate_remote_path`, `versions_many`, `versions_at_most`; `qa_overlay_check` has only tests); adopt `url_encode_path` at its three inline `@uri` sites | shellcheck, client-tests |
| bash | stale: `config.sh` header (a credential model that no longer exists), `IMPORTER_SPEC.md §11` / `docs/saves.md` pointers, `keys.sh` doc blocks on the wrong function; ~45 restating one-liners | — |
| Nix | `new/flake.nix` (stray two-line file), `lib.nix` `baseEnv = { }`, `super_metroid.nix` template prose; stale "only confirmed for SNES" in `n64/nes/gba.nix`; `ryujinx.nix` doc blocks stranded on the wrong functions; the "Split out of helpers.nix" boilerplate ×4; the per-variant headers ×9; the Fl4sh pack prose ×4; the UltraCam window block ×5 | `nix build .#env-*`, checks.environments |
| service/indexer | `Handler._authenticated`, `binascii` import, `config.load(**_compat)`, `planner.summarize`, `CatalogStore.lookup` (tests only); `IMPORTER_SPEC.md` pointers ×10; `saves.Publish` docstring names a field that does not exist; `cli.py` help still describes the qBittorrent mode | python-tests, ruff |
| picker | `draw_cover`, `Pads.any_button_down`, `keys.seat_of`, `Danstick.status_word` (tests only); stale module docstring ("Milestone one draws placeholders"), `run()` docstring, the orphaned hush comment | pytest tests/ui, ruff |
| Rust | `leaders::crossings` → `#[cfg(test)]`; `frame.rs` `with_*`/getter docs; `MenuFrame` and `padlink::pump` docs are stale | checks.rust |

## Phase B — shared helpers, each landing with the test that guards it

| Area | Helper | Replaces | Test first |
|---|---|---|---|
| Nix | `gotgJqEdit` shell snippet (jq → tmp → mv, rm on failure) | 8 copies across switch.nix, ryujinx.nix, the UltraCams, harkinian.nix, oot-coop | preLaunch probes (new `nix/checks/prelaunch-probe.nix` harness): UltraCam on deck/desktop, Ryujinx helpers with `GOTG_MACHINE`, `lusSettings` asserted on its output |
| Nix | `nxOptimizerExefs`, `fl4shMod`, `withMod` (variant preLaunch + version window), `recompPort`, `coopVariant` | the duplicated UltraCam install loops, four Fl4sh pack fetches, five TOTK/BotW window blocks, dk64/sk2 (70% identical), nine `Np` files | the same probes; checks.catalog `variantsOf`; recipes.nix dk64Probe |
| Nix | one package set (`pkgs/default.nix`) | packages built twice in `env/default.nix` and `flake.nix` | `nix eval .#packages --apply attrNames` unchanged |
| bash | `zenity_supervise` | `_download_zenity` + `_env_build_zenity` | a download-cancel bats (today only the env build has one) |
| bash | `atomic_write` / `json_merge_file` | ~30 tmp+mv sites, 4 read-merge-chmod-mv sequences | tokens/saves-sync/controllers/pads bats |
| bash | `service_fetch_file` | keys_fetch + firmware_fetch | keys.bats, firmware.bats (refresh-on-dead-host) |
| bash | `lib/all.sh` | the 34-line source list in bin/gotg mirrored (and drifting) in tests/client/helper.bash | shellcheck, client-tests |
| bash | `with_lock` | six `exec N>; flock` sequences with hand-matched releases | download.bats (concurrent), steam.bats (queue), updates.bats |
| picker | `client.ask(argv, timeout) -> str \| None` | 8 subprocess copies (installed, updates, saves_choice, saves_list, versions, variants, prepare, storage) | one `conftest.py` fixture `fake_client`; tests/ui/test_client.py |
| picker | `GridView` dataclass; `draw(screen, font_at, view, typing, menu)` | 9–11 positional args at three call sites | fake-surface tests later; pure `status_line()`, `row_under_text()` now |
| picker | `intents.py`: `intent(event) -> BACK \| OK \| MOVE \| SPACE` | eight hand-written "B/Escape/b = back, A/Enter = confirm" branches | tests/ui/test_intents.py |
| service | `_json(code, obj)`; `_fetch()` for `_forward`/`_steamgriddb`; `_send_bundle` | 19 `self._send(200, json.dumps…)`, two near-identical upstream fetches, the saves GET branches | test_proxy, test_saves |
| service | `gotg/_db.py` (connect/read), `contract.utc_now`, `gotg/_http.py` (no-redirect opener) | catalog.py/tokens.py/saves.py/publish.py copies | test_tokens, test_catalog, test_publish |
| service tests | `conftest.py`: `serve()` context manager, one `call()` | 7 `call()` helpers, 2 `free_port()`, 46 server spins | — |
| Rust | bodies into the `Listen` impls; `Event::refuses(cmd)`; `controller_sprite()`, `lit()` | 4 delegating shims, 3 copied "unknown command" refusals, duplicated sprite literals | the module tests already there |

## Phase C — structure (each its own commit, full check set each)

- `flake.nix` 665 → <450: `mkLibrary` into `lib/library.nix`, `mkApp`, one `devShim`, one `nixpkgs` import; move the env machinery (`lib.nix`, `helpers.nix`, `steps.nix`, `machine.sh`) out of the platform-scanned directory.
- picker `run()`: `Badges`, `ChipState`, `Screen` state; `plan_pick`, `plan_self_update`, `plan_restart`, `on_prepare_done` as pure functions with tests; then one dispatch instead of two parallel if-chains.
- service `_catalog` (137 lines), `_handle` (135), `_saves` (86) split; `Config`/`RateLimiter`/`TokenCache` out of app.py.
- Rust `frame.rs` encode/decode on one cursor (write the round-trip property test first); `watch()` state into `Chords`, `MenuDriver`, a listener bundle; `seat_rows`/`menu_frame`/`drawn` into tested modules.
- bash: `cmd_qa` (291 lines) and `cmd_login` (94) into named steps.

## Testing added along the way

- preLaunch probe harness and probes for UltraCam, the Ryujinx helpers, `lusSettings`, `switch.nix`'s Deck pass, Dolphin ini, the split-screen sessions.
- bats: `wait_for` instead of `sleep` (danstick keeper tests, killswitch, install, launch), `HOME` backstop in `setup_env`, `make_stub`.
- service: 304/ETag on art, containment refusals on `/files`, `cli.py` env mapping, `Config.validate` branches, `RateLimiter` with an injected clock.
- picker: the loop decisions, once extracted.
- Rust: `decode(encode(f)) == f` over corner values, scene geometry invariants over screen sizes and consoles.

## What is not on the list

- Per-platform Nix files as a data table: the discovery contract depends on one file per platform, and they are 3–12 lines each.
- `slugify.py` / `plan.py`: ported verbatim, kept diffable with upstream.
- The why-comments. Several surveys wanted to trim narratives (four-swords-split's 54 lines on the Gamescope WSI layer); those get tightened, not removed.

## Outcome (2026-10-08)

Sixteen commits on `chore/simplify`, every check green at each, the
controller e2e green on the cluster at the end (52 passed, 1 skipped).

What is gone: 20 uncalled functions, the paragraph copied into nine
variant files, the jq-and-mv shell copied eight times, the dialog
supervisor written twice, the server spin written forty-six times in
the service tests, the frame layout counted by hand twice, `run()`
answering "who owns the input" twice, `flake.nix`'s second half, two
copies of the package set. What was found on the way and fixed: a
download cancelled from its dialog ended the client silently (errexit
on `wait`, SIGPIPE on the full bar); the client's "slow curl" test shim
never reached the client; three service routes dropped the connection
on an unbalanced `[`; a PUT to a save's meta stored a save; the
`inheritsPlatform` check had never evaluated since it was written.

What it cost in lines, source only (`src`, `rust`, `nix`, `flake.nix`;
tests under `tests/` excluded): 47,408 -> 49,928. Of the 2,520 added,
~665 are Rust tests inside their modules, 291 the new `prelaunchProbe`
check, ~460 comment lines (every new module carries the why-paragraph
the house style asks for), and the rest is the structure that a split
costs -- imports, signatures, dataclasses -- against the duplication it
removed. Tests under `tests/`: +2,016 lines; 575 -> 667 picker, 724 ->
872 service, 251 -> 279 Rust, +55 bats.

Not done: the `run()` handlers are still closures over the loop's
state (a state object is the next seam, and wants pygame tests);
`steam_write_picker_launcher`, `admin_scan`, `updates_json` and
`play_prepare` stay whole; `machine.sh` and `foreign-gl.nix` stay in
the scanned directory, four importers outside it name them by path.
