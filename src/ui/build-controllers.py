"""Rasterise the controller SVGs and write the anchors beside them.

Run at build time, never at runtime — see docs/controller-diagram-research.md.
The short version is that pygame's own SVG support is NanoSVG, which the docs
call "limited support", and pygame-ce's load_sized_svg silently clamps to the
source aspect ratio: asking it for 1600x900 returns 1600x800, measured. A
coordinate mapping computed from the size you *asked* for is then wrong by 100
pixels and nothing tells you. So the size is decided here, where it can be
checked, and the runtime only ever loads a PNG.

Anchors come out normalised to the viewBox rather than in pixels, which is what
lets one manifest serve every density: the runtime multiplies by whatever
surface it actually has. A pixel manifest would need one entry per tier and
would be wrong the moment a tier was added.

Only the `anchor-*` circles are read. The artwork is never parsed, so it can use
anything a renderer understands without this script having an opinion.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import xml.etree.ElementTree as ET

SVG_NS = "{http://www.w3.org/2000/svg}"
ANCHOR_PREFIX = "anchor-"

# 1x is the design size. 3x covers a 4K television; the Deck's 1280x800 panel
# sits between 1x and 2x. The runtime picks the smallest tier at or above what
# it needs and scales down, because scaling down is the direction that looks
# right.
DENSITIES = (1, 2, 3)


class BuildError(Exception):
    """Something about an SVG makes a diagram impossible to draw from it."""


def anchors_of(svg: pathlib.Path) -> tuple[dict[str, tuple[float, float]], tuple[float, float]]:
    """Every anchor in one SVG, normalised to its viewBox.

    Circles rather than arbitrary shapes, and on a transform-free layer, so
    this needs no matrix cascade and therefore no dependency to read. If an
    anchor ever has to live inside a transformed group, that is the moment to
    reach for `svgelements` or `inkscape --query-all` — not before.
    """
    root = ET.parse(svg).getroot()
    box = root.get("viewBox")
    if not box:
        raise BuildError(f"{svg.name}: no viewBox, so nothing can be normalised against it")
    min_x, min_y, width, height = (float(v) for v in box.split())
    if width <= 0 or height <= 0:
        raise BuildError(f"{svg.name}: viewBox has no area")

    found: dict[str, tuple[float, float]] = {}
    for element in root.iter():
        name = element.get("id") or ""
        if not name.startswith(ANCHOR_PREFIX):
            continue
        if element.tag != f"{SVG_NS}circle":
            raise BuildError(f"{svg.name}: {name} is a {element.tag.split('}')[-1]}, and anchors must be circles")
        if element.get("stroke") not in (None, "none"):
            # A stroked anchor has a visual bounding box wider than its
            # geometry, which is the ambiguity the anchors layer exists to
            # avoid. Refuse it rather than be quietly a few pixels off.
            raise BuildError(f"{svg.name}: {name} has a stroke; anchors must be unstroked")
        input_name = name[len(ANCHOR_PREFIX) :]
        found[input_name] = (
            (float(element.get("cx", "0")) - min_x) / width,
            (float(element.get("cy", "0")) - min_y) / height,
        )

    if not found:
        raise BuildError(f"{svg.name}: no anchor-* circles, so no leader could be drawn")
    return found, (width, height)


def rasterise(svg: pathlib.Path, out_dir: pathlib.Path, base_width: float) -> dict[str, str]:
    """One PNG per density, by resvg. Returns density -> filename."""
    written = {}
    for density in DENSITIES:
        name = f"{svg.stem}@{density}x.png"
        subprocess.run(
            ["resvg", "--width", str(int(base_width * density)), str(svg), str(out_dir / name)],
            check=True,
        )
        written[str(density)] = name
    return written


# How tall a strip icon is rasterised. One height rather than one width,
# because a row of them is read along a common baseline: a keyboard is wider
# than a pad and they should still look like one set. Generous enough that the
# runtime only ever scales down, which is the direction that looks right.
ICON_HEIGHT = 96


def build_icons(source_dir: pathlib.Path, out_dir: pathlib.Path) -> dict:
    """The strip's icons: one PNG per controller model, no anchors.

    A separate pass from the console diagrams because these answer a different
    question -- "who is holding what", at 28 pixels -- and carry no anchors at
    all. Running them through `build` would refuse every one of them.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    icons = {}
    for svg in sorted(source_dir.glob("*.svg")):
        name = f"{svg.stem}.png"
        subprocess.run(
            ["resvg", "--height", str(ICON_HEIGHT), str(svg), str(out_dir / name)],
            check=True,
        )
        icons[svg.stem] = name
        print(f"{svg.name}: icon at {ICON_HEIGHT}px", file=sys.stderr)
    if not icons:
        raise BuildError(f"no SVGs in {source_dir}")
    return {"version": 1, "height": ICON_HEIGHT, "icons": icons}


def build(source_dir: pathlib.Path, out_dir: pathlib.Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    controllers = {}
    for svg in sorted(source_dir.glob("*.svg")):
        anchors, (width, height) = anchors_of(svg)
        controllers[svg.stem] = {
            # The hash of the SVG this was built from. The runtime does not
            # check it, but a diagram that looks wrong is answerable with one
            # command instead of a guess about which tree it came from.
            "svg_sha256": hashlib.sha256(svg.read_bytes()).hexdigest(),
            "size": [width, height],
            "images": rasterise(svg, out_dir, width),
            "anchors": {name: list(uv) for name, uv in sorted(anchors.items())},
        }
        print(f"{svg.name}: {len(anchors)} anchors, {len(DENSITIES)} densities", file=sys.stderr)
    if not controllers:
        raise BuildError(f"no SVGs in {source_dir}")
    return {"version": 1, "controllers": controllers}


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[1] == "--icons":
        out = pathlib.Path(argv[3])
        manifest = build_icons(pathlib.Path(argv[2]), out)
        (out / "icons.json").write_text(json.dumps(manifest, indent=2) + "\n")
        return 0
    if len(argv) != 3:
        print("usage: build-controllers.py [--icons] <svg-dir> <out-dir>", file=sys.stderr)
        return 2
    manifest = build(pathlib.Path(argv[1]), pathlib.Path(argv[2]))
    (pathlib.Path(argv[2]) / "controllers.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except BuildError as error:
        print(f"build-controllers: {error}", file=sys.stderr)
        sys.exit(1)
