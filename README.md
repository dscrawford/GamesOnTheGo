# GamesOnTheGo (GOTG)

Keep a large game library on the server; download individual games on demand to a
laptop or Steam Deck, then launch them in the right emulator from Steam.

Two components share one entry-id contract:

| Component | What it is | Where it runs |
|---|---|---|
| **importer** (`importer/`) | Radarr-style service that organizes completed game torrents into a canonical `/Games` tree using seeding-safe hardlinks, and publishes the catalog manifest | In-cluster CronJob (see `Kubernetes/games/config.yaml`) |
| **client** (`client/`) | `gotg` CLI — downloads a game from the server on demand, builds its emulator via nix, generates a Steam launcher | Desktop / Steam Deck |

## The entry-id contract

```
/Games/<platform>/<region>.<title_slug>[.<ext>]
```

The entry name **minus extension** is the GOTG game id. It is used verbatim as the
server path, the manifest id, the launcher filename (`play-<id>.sh`) and the CLI
argument (`gotg play <id>`).

- Matches `^[a-z]{3,5}\.[a-z0-9][a-z0-9_]*$` — lowercase `a-z 0-9 _` only.
- `region` is one of `usa`, `eur`, `jpn`, `world`.
- Single files keep their real extension (`.z64`, `.nsp`, `.zip`); directories get none.

Example: `Legend of Zelda, The - Majora's Mask (USA).z64` →
`/Games/n64/usa.legend_of_zelda_majoras_mask.z64`, id `usa.legend_of_zelda_majoras_mask`.

## Development

```bash
nix develop          # python + pytest + ruff + shellcheck + bats
nix flake check      # run every test suite and linter
```

See `importer/README.md` for the importer's runtime interface, and the
implementation spec at `Kubernetes/games/IMPORTER_SPEC.md`.
