"""Set dotted keys in melonDS.toml from a JSON object, keeping everything else."""

import json
import os
import sys

import tomlkit


def apply(doc, settings):
    """Set each "A.B.C" key, making the tables on the way to it."""
    for dotted, value in settings.items():
        *tables, key = dotted.split(".")
        node = doc
        for name in tables:
            if name not in node:
                node[name] = tomlkit.table()
            node = node[name]
        node[key] = value
    return doc


def main(path, *sources):
    settings = {}
    for source in sources:
        if source.startswith("@"):
            try:
                with open(source[1:]) as f:
                    settings.update(json.load(f))
            except (OSError, ValueError):
                # A missing or broken pad file is a launch on the keyboard, not no launch.
                continue
        else:
            settings.update(json.loads(source))
    try:
        with open(path) as f:
            doc = tomlkit.parse(f.read())
    except FileNotFoundError:
        doc = tomlkit.document()
    except tomlkit.exceptions.TOMLKitError:
        # melonDS would discard it too and start from defaults.
        doc = tomlkit.document()
    apply(doc, settings)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".gotg"
    with open(tmp, "w") as f:
        f.write(tomlkit.dumps(doc))
    os.replace(tmp, path)


if __name__ == "__main__":
    main(*sys.argv[1:])
