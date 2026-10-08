"""The other ways one game can be run.

A variant is a second environment for the same game — a mod, a port, a frame
rate, a four-player split screen — and the client reaches it as
`gotg play <id> <variant>`. They are files rather than catalog rows
(`env/games/<platform>/<id>.<variant>.nix`), because what a mod *is* is a
build, and the catalog knows only about bytes on a server.

So this reads the directory, which is what the client does. Not
`gotg complete variants`: that filters the whole catalog through jq to find the
platform this program already knows, and the grid asks the moment a menu opens.

The one thing the directory cannot answer is whether a mod can run: a mod
states the game versions it was built against, and with nothing here inside
that window there is no launch it could do. The client is asked for those by
name — a short list, usually empty — and they are left out, because a row that
answers with a paragraph is a row somebody presses twice.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .catalog import Game
from .client import ask

# What the client will accept after an id — env.sh's own rule. A file with a
# stray second dot makes a name `gotg play` rejects, and offering it here would
# be a menu row that cannot work.
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def env_dir() -> Path | None:
    """The client's environment files, which the wrapper points at. None where
    this is running from a checkout with no client beside it — then a game
    simply has no variants, and the menu is what it always was."""
    for key in ("GOTG_UI_ENV", "GOTG_ENV_DIR"):
        found = os.environ.get(key)
        if found:
            return Path(found)
    root = os.environ.get("GOTG_ROOT")
    return Path(root) / "env" if root else None


# The client walks one game's install directory and reads built manifests; a
# hung client must not hold a menu closed.
DISABLED_TIMEOUT = 3


def disabled_for(game: Game) -> frozenset[str]:
    """Which of this game's variants no version installed here can run.

    Silent on failure, and empty for a client too old to know the subcommand:
    the menu then offers what it always offered, and the launch still refuses
    in words. Hiding a working mod because a subprocess failed would be the
    worse half of the trade.
    """
    out = ask(["complete", "disabled", f"{game.platform}/{game.id}"], timeout=DISABLED_TIMEOUT)
    if out is None:
        return frozenset()
    return frozenset(line.strip() for line in out.splitlines() if line.strip())


def variants_for(game: Game, where: Path | None = None) -> tuple[str, ...]:
    """Every variant of one game that can run, sorted, or nothing at all.

    Sorted rather than in directory order: the list is a menu somebody learns
    the shape of, and a filesystem's order is not stable between machines.
    """
    where = where if where is not None else env_dir()
    if where is None:
        return ()
    try:
        files = list((where / "games" / game.platform).glob(f"{game.id}.*.nix"))
    except OSError:
        return ()
    names = {file.name[len(game.id) + 1 : -len(".nix")] for file in files}
    names = {name for name in names if NAME.match(name)}
    if names:
        names -= disabled_for(game)
    return tuple(sorted(names | emulate_for(game, where)))


def state_dir() -> Path:
    """The client's state directory, by the same rule it uses."""
    state = os.environ.get("GOTG_STATE_DIR")
    if not state:
        base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
        state = os.path.join(base, "gotg")
    return Path(state)


def built_env(attr: str) -> Path | None:
    """Where the environment `attr` is built, as the client's env_root finds it.

    A game built from a library is a root under games/ whose spec names its
    environment's store path; nothing is under roots/ for it any more. The
    old roots directory is still read for a machine that has not updated.
    """
    for spec in sorted((state_dir() / "games").glob("*/share/gotg/spec.json")):
        try:
            data = json.loads(spec.read_text())
        except (OSError, ValueError):
            continue
        env = data.get("env") if isinstance(data, dict) and data.get("attr") == attr else None
        if isinstance(env, str) and env:
            return Path(env)
    legacy = Path(os.environ.get("GOTG_ROOTS_DIR") or state_dir() / "roots") / attr
    return legacy if legacy.exists() else None


def emulate_for(game: Game, where: Path) -> set[str]:
    """`emulate` where it would do something, and nothing where it would not.

    Some games run on something other than their platform's emulator -- a
    native port off a decompilation. This is the way back, and it is worth
    offering: a port is younger than the emulator it replaces, so when one of
    the two has the bug, this is how a person finds out which.

    Not offered for every game with an environment of its own, which is a
    different and much larger set: most of those are the platform's emulator
    with settings added, and swapping one for the bare platform would only
    drop the settings. The environment says which it is, with a marker its
    build carries, so this reads the built environment -- the client's own rule, in
    env_variant_names, found the way env_root finds it.

    The name is reserved rather than a file (see env_attr), so there is
    nothing for the glob above to find. A real <id>.emulate.nix wins there,
    exactly as it does in the client.
    """
    platform = where / f"{game.platform}.nix"
    env = built_env(f"env-{game.platform}-{game.id.replace('.', '_')}")
    try:
        if env is not None and (env / "share" / "gotg" / "native-port").exists() and platform.is_file():
            return {"emulate"}
    except OSError:
        return set()
    return set()
