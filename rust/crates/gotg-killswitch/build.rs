//! The palette, from config/theme.yaml -- the file the picker reads -- so the
//! overlay and the picker cannot drift. They did once: a copy of the palette
//! was the fifth, and nothing would have said so.
//!
//! GOTG_THEME names the file; the flake passes it, and a checkout finds it
//! three directories up.

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

fn main() {
    let manifest = PathBuf::from(env::var("CARGO_MANIFEST_DIR").expect("cargo sets CARGO_MANIFEST_DIR"));
    let path = env::var("GOTG_THEME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| manifest.join("../../../config/theme.yaml"));
    println!("cargo:rerun-if-env-changed=GOTG_THEME");
    println!("cargo:rerun-if-changed={}", path.display());

    let text = fs::read_to_string(&path).unwrap_or_else(|e| panic!("theme: {}: {e}", path.display()));
    let docs = YamlLoader::load_from_str(&text).unwrap_or_else(|e| panic!("theme: {e}"));
    let theme = docs.first().expect("theme: empty file");
    let colours = &theme["colours"];
    let players: Vec<[u8; 3]> = theme["players"]
        .as_vec()
        .expect("theme: players is not a list")
        .iter()
        .enumerate()
        .map(|(i, player)| rgb(player, &format!("players[{i}]")))
        .collect();
    assert!(!players.is_empty(), "theme: no player colours");

    let out = format!(
        "// Generated from {:?} by build.rs. Do not edit.\n\
         pub const PLAYERS: [[u8; 3]; {}] = {:?};\n\
         pub const BACKGROUND: [u8; 3] = {:?};\n\
         pub const TEXT: [u8; 3] = {:?};\n\
         pub const TEXT_DIM: [u8; 3] = {:?};\n",
        path,
        players.len(),
        players,
        rgb(&colours["background"], "colours.background"),
        rgb(&colours["text"], "colours.text"),
        rgb(&colours["text_dim"], "colours.text_dim"),
    );
    let dest = PathBuf::from(env::var("OUT_DIR").expect("cargo sets OUT_DIR")).join("theme.rs");
    fs::write(dest, out).expect("theme: cannot write theme.rs");
}
