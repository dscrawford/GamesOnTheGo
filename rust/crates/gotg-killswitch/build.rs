//! What the overlay shares with the picker, read from the picker's own files
//! so the two cannot drift. They did once: a copy of the palette was the
//! fifth, and nothing would have said so.
//!
//! - config/theme.yaml: the palette (GOTG_THEME).
//! - config/icons.yaml: which drawing stands for which controller
//!   (GOTG_ICON_RULES).
//! - src/ui/assets/icons and src/ui/assets/controllers: the drawings
//!   themselves (GOTG_ICON_ART, GOTG_CONTROLLER_ART), embedded whole.
//!
//! The flake passes each; a checkout finds them three directories up.

use std::collections::BTreeMap;
use std::{env, fs, path::PathBuf};

use yaml_rust2::{Yaml, YamlLoader};

fn rgb(value: &Yaml, what: &str) -> [u8; 3] {
    let parts: Vec<u8> = value
        .as_vec()
        .unwrap_or_else(|| panic!("theme: {what} is not a list"))
        .iter()
        .map(|part| {
            let n = part
                .as_i64()
                .unwrap_or_else(|| panic!("theme: {what} holds a non-number"));
            u8::try_from(n).unwrap_or_else(|_| panic!("theme: {what} holds {n}, past 0..255"))
        })
        .collect();
    parts
        .try_into()
        .unwrap_or_else(|_| panic!("theme: {what} is not three numbers"))
}

/// A path named by `variable`, else this far up from the crate.
fn input(variable: &str, default: &str) -> PathBuf {
    let manifest = PathBuf::from(env::var("CARGO_MANIFEST_DIR").expect("cargo sets CARGO_MANIFEST_DIR"));
    let path = env::var(variable)
        .map(PathBuf::from)
        .unwrap_or_else(|_| manifest.join(default));
    println!("cargo:rerun-if-env-changed={variable}");
    println!("cargo:rerun-if-changed={}", path.display());
    path
}

fn yaml(path: &PathBuf) -> Yaml {
    let text = fs::read_to_string(path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    let mut docs = YamlLoader::load_from_str(&text).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    assert!(!docs.is_empty(), "{}: empty file", path.display());
    docs.swap_remove(0)
}

fn theme() -> String {
    let path = input("GOTG_THEME", "../../../config/theme.yaml");
    let theme = yaml(&path);
    let colours = &theme["colours"];
    let players: Vec<[u8; 3]> = theme["players"]
        .as_vec()
        .expect("theme: players is not a list")
        .iter()
        .enumerate()
        .map(|(i, player)| rgb(player, &format!("players[{i}]")))
        .collect();
    assert!(!players.is_empty(), "theme: no player colours");
    format!(
        "// Generated from {:?} by build.rs. Do not edit.\n\
         pub const PLAYERS: [[u8; 3]; {}] = {:?};\n\
         pub const BACKGROUND: [u8; 3] = {:?};\n\
         pub const TEXT: [u8; 3] = {:?};\n\
         pub const TEXT_DIM: [u8; 3] = {:?};\n\
         pub const EMPTY: [u8; 3] = {:?};\n",
        path,
        players.len(),
        players,
        rgb(&colours["background"], "colours.background"),
        rgb(&colours["text"], "colours.text"),
        rgb(&colours["text_dim"], "colours.text_dim"),
        rgb(&colours["empty"], "colours.empty"),
    )
}

/// `[{needle: icon}, ...]` in the order it is written: order is the whole of
/// it, as the picker's icons.py says.
fn pairs(list: &Yaml, what: &str) -> Vec<(String, String)> {
    let Some(list) = list.as_vec() else {
        return Vec::new();
    };
    list.iter()
        .filter_map(Yaml::as_hash)
        .flat_map(|entry| entry.iter())
        .map(|(needle, icon)| {
            let needle = needle
                .as_str()
                .unwrap_or_else(|| panic!("icons: a {what} key is not text"));
            let icon = icon
                .as_str()
                .unwrap_or_else(|| panic!("icons: {needle}'s drawing is not a name"));
            (needle.to_lowercase(), icon.to_owned())
        })
        .collect()
}

fn icons() -> String {
    let rules_path = input("GOTG_ICON_RULES", "../../../config/icons.yaml");
    let icon_dir = input("GOTG_ICON_ART", "../../../src/ui/assets/icons");
    let controller_dir = input("GOTG_CONTROLLER_ART", "../../../src/ui/assets/controllers");
    let rules = yaml(&rules_path);

    // Every drawing by name; one in icons/ outranks the console diagram of the
    // same name, as icons.icon_path looks there first.
    let mut drawings: BTreeMap<String, PathBuf> = BTreeMap::new();
    for dir in [&controller_dir, &icon_dir] {
        for entry in fs::read_dir(dir).unwrap_or_else(|e| panic!("{}: {e}", dir.display())) {
            let path = entry.expect("a directory entry").path();
            if path.extension().is_some_and(|ext| ext == "svg") {
                let name = path
                    .file_stem()
                    .expect("a file name")
                    .to_string_lossy()
                    .into_owned();
                drawings.insert(name, fs::canonicalize(&path).expect("a drawing's path"));
            }
        }
    }
    let names: Vec<&String> = drawings.keys().collect();
    let index = |name: &str| names.iter().position(|known| *known == name);
    let fallback_name = rules["fallback"].as_str().unwrap_or("generic");
    let fallback =
        index(fallback_name).unwrap_or_else(|| panic!("icons: no drawing for the fallback {fallback_name}"));
    // A rule naming a drawing nobody has is the fallback, as the picker draws it.
    let table = |list: Vec<(String, String)>| -> String {
        list.iter()
            .map(|(needle, icon)| format!("({needle:?}, {}),", index(icon).unwrap_or(fallback)))
            .collect::<Vec<_>>()
            .join(" ")
    };
    format!(
        "// Generated from {:?}, {:?} and {:?} by build.rs. Do not edit.\n\
         pub const NAMES: [&str; {n}] = {names:?};\n\
         pub static SVGS: [&[u8]; {n}] = [{svgs}];\n\
         pub const IDS: &[(&str, u8)] = &[{ids}];\n\
         pub const RULES: &[(&str, u8)] = &[{rules}];\n\
         pub const FALLBACK: u8 = {fallback};\n",
        rules_path,
        icon_dir,
        controller_dir,
        n = names.len(),
        svgs = drawings
            .values()
            .map(|path| format!("include_bytes!({:?})", path))
            .collect::<Vec<_>>()
            .join(", "),
        ids = table(pairs(&rules["ids"], "ids")),
        rules = table(pairs(&rules["rules"], "rules")),
    )
}

/// Every `anchor-*` circle in a drawing, normalised to its viewBox -- the
/// same points the picker's build-controllers.py writes -- and the viewBox's
/// width over its height.
fn anchors_of(svg: &PathBuf) -> (BTreeMap<String, (f32, f32)>, f32) {
    let text = fs::read_to_string(svg).unwrap_or_else(|e| panic!("{}: {e}", svg.display()));
    let doc = roxmltree::Document::parse(&text).unwrap_or_else(|e| panic!("{}: {e}", svg.display()));
    let root = doc.root_element();
    let view: Vec<f32> = root
        .attribute("viewBox")
        .unwrap_or_else(|| panic!("{}: no viewBox", svg.display()))
        .split(|c: char| c.is_whitespace() || c == ',')
        .filter(|part| !part.is_empty())
        .map(|part| part.parse().expect("a viewBox number"))
        .collect();
    let [min_x, min_y, width, height] = view[..] else {
        panic!("{}: a viewBox is four numbers", svg.display());
    };
    let mut anchors = BTreeMap::new();
    for node in doc.descendants().filter(|n| n.has_tag_name("circle")) {
        let Some(name) = node.attribute("id").and_then(|id| id.strip_prefix("anchor-")) else {
            continue;
        };
        let at = |attr: &str| -> f32 {
            node.attribute(attr)
                .and_then(|v| v.parse().ok())
                .unwrap_or_else(|| panic!("{}: anchor-{name} has no {attr}", svg.display()))
        };
        anchors.insert(
            name.to_owned(),
            ((at("cx") - min_x) / width, (at("cy") - min_y) / height),
        );
    }
    (anchors, width / height)
}

/// config/controllers/*.yaml, each with its drawing and where every control
/// it walks sits on that drawing: what the rebind shows over a game.
fn consoles() -> String {
    let config = input("GOTG_CONTROLLER_CONFIG", "../../../config/controllers");
    let art = input("GOTG_CONTROLLER_ART", "../../../src/ui/assets/controllers");
    let mut files: Vec<PathBuf> = fs::read_dir(&config)
        .unwrap_or_else(|e| panic!("{}: {e}", config.display()))
        .map(|entry| entry.expect("a directory entry").path())
        .filter(|path| path.extension().is_some_and(|ext| ext == "yaml"))
        .collect();
    files.sort();
    let mut artworks: Vec<String> = Vec::new();
    let mut entries = Vec::new();
    for file in &files {
        let raw = yaml(file);
        let name = file
            .file_stem()
            .expect("a file name")
            .to_string_lossy()
            .into_owned();
        let text = |key: &str, default: &str| raw[key].as_str().unwrap_or(default).to_owned();
        let layout = text("layout", "generic");
        let artwork = text("artwork", "generic");
        let svg = art.join(format!("{artwork}.svg"));
        let (anchors, aspect) = anchors_of(&svg);
        let art_index = artworks.iter().position(|a| *a == artwork).unwrap_or_else(|| {
            artworks.push(artwork.clone());
            artworks.len() - 1
        });
        let aliases = raw["anchors"].as_hash();
        let controls: Vec<String> = raw["controls"]
            .as_hash()
            .map(|controls| {
                controls
                    .iter()
                    .filter_map(|(id, label)| Some((id.as_str()?, label.as_str().unwrap_or_default())))
                    .map(|(id, label)| {
                        // The control's own circle, else the one the config
                        // points it at (the older drawings use the console's
                        // letters): schemes.anchor_names, the picker's rule.
                        let alias = aliases
                            .and_then(|a| a.get(&Yaml::String(id.to_owned())))
                            .and_then(Yaml::as_str);
                        let uv = anchors.get(id).or_else(|| alias.and_then(|a| anchors.get(a)));
                        let anchor = uv.map_or("None".to_owned(), |(u, v)| format!("Some(({u:?}, {v:?}))"));
                        format!("Control {{ id: {id:?}, label: {label:?}, anchor: {anchor} }}")
                    })
                    .collect()
            })
            .unwrap_or_default();
        let platforms: Vec<String> = raw["platforms"]
            .as_vec()
            .map(|list| list.iter().filter_map(Yaml::as_str).map(str::to_owned).collect())
            .unwrap_or_default();
        entries.push(format!(
            "Console {{ name: {name:?}, platforms: &{platforms:?}, layout: {layout:?}, artwork: {art_index}, \
             aspect: {aspect:?}, controls: &[{}] }}",
            controls.join(", ")
        ));
    }
    format!(
        "// Generated from {:?} and {:?} by build.rs. Do not edit.\n\
         pub static ARTWORK: [&[u8]; {n}] = [{svgs}];\n\
         pub static CONSOLES: &[Console] = &[{consoles}];\n",
        config,
        art,
        n = artworks.len(),
        svgs = artworks
            .iter()
            .map(|a| format!(
                "include_bytes!({:?})",
                fs::canonicalize(art.join(format!("{a}.svg"))).expect("a drawing's path")
            ))
            .collect::<Vec<_>>()
            .join(", "),
        consoles = entries.join(",\n"),
    )
}

fn main() {
    let out = PathBuf::from(env::var("OUT_DIR").expect("cargo sets OUT_DIR"));
    fs::write(out.join("theme.rs"), theme()).expect("cannot write theme.rs");
    fs::write(out.join("icons.rs"), icons()).expect("cannot write icons.rs");
    fs::write(out.join("consoles.rs"), consoles()).expect("cannot write consoles.rs");
}
