//! Step 4 of docs/windows.md, after `saves pull`: the saves directories made
//! (an emulator told to save into a missing one may call it read-only and
//! lose the session), the seed config copied in only where absent (the
//! person's own settings win), then the actions, in order, every launch.

use std::fs;
use std::path::{Path, PathBuf};

use crate::fsx;
use crate::ini;
use crate::render::{Placeholders, under};
use crate::spec::{Action, ConfigRoot, Spec};

pub fn config_root(spec: &Spec, ph: &Placeholders) -> PathBuf {
    match spec.windows.config_root {
        ConfigRoot::State => ph.state.clone(),
        ConfigRoot::Emulator => ph.emulator.clone(),
    }
}

pub fn saves_dirs(spec: &Spec, state: &Path) -> Result<(), String> {
    fs::create_dir_all(state).map_err(|e| format!("{}: {e}", state.display()))?;
    for d in &spec.windows.saves_dirs {
        let p = under(state, d);
        fs::create_dir_all(&p).map_err(|e| format!("{}: {e}", p.display()))?;
    }
    Ok(())
}

/// Each `config_files` entry copied from the bundle, unless already there.
pub fn seed_config(spec: &Spec, ph: &Placeholders) -> Result<(), String> {
    let root = config_root(spec, ph);
    for (rel, src) in &spec.windows.config_files {
        let dest = under(&root, rel);
        if dest.exists() {
            continue;
        }
        let from = under(&ph.bundle, src);
        if !from.is_file() {
            return Err(format!("the bundle is missing {} (config_files)", from.display()));
        }
        fsx::copy_tree(&from, &dest)?;
    }
    Ok(())
}

pub fn apply(action: &Action, ph: &Placeholders) -> Result<(), String> {
    match action {
        Action::Ini {
            file,
            section,
            key,
            value,
        } => ini::set_file(&ph.path(file, &ph.state), section, key, &ph.text(value)),
        Action::Mkdir { path } => {
            let p = ph.path(path, &ph.state);
            fs::create_dir_all(&p).map_err(|e| format!("{}: {e}", p.display()))
        }
        Action::Copy { from, to, once } => {
            let (from, to) = (ph.path(from, &ph.bundle), ph.path(to, &ph.state));
            if *once && to.exists() {
                return Ok(());
            }
            fsx::copy_tree(&from, &to)
        }
    }
}

pub fn run(spec: &Spec, ph: &Placeholders) -> Result<(), String> {
    saves_dirs(spec, &ph.state)?;
    seed_config(spec, ph)?;
    for (i, a) in spec.windows.actions.iter().enumerate() {
        apply(a, ph).map_err(|e| format!("action {}: {e}", i + 1))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::render::fullscreen_words;
    use crate::spec::tests::sample;
    use serde_json::json;

    struct Tmp(PathBuf);
    impl Drop for Tmp {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn setup(tag: &str, windows: serde_json::Value) -> (Tmp, Spec, Placeholders) {
        let root = std::env::temp_dir().join(format!("gotg-prep-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let bundle = root.join("bundle");
        fs::create_dir_all(bundle.join("config").join("0")).expect("mkdir");
        fs::write(bundle.join("config").join("0").join("settings.bml"), "seed").expect("seed");
        fs::create_dir_all(bundle.join("emulator")).expect("mkdir");
        let mut v = sample();
        for (k, val) in windows.as_object().expect("obj") {
            v["windows"][k] = val.clone();
        }
        let spec = Spec::parse(&v.to_string()).expect("spec");
        let ph = Placeholders {
            target: root.join("games").join("g.z64"),
            install: root.join("games"),
            state: root.join("state"),
            emulator: bundle.join("emulator"),
            bundle,
            fullscreen: fullscreen_words(None, true),
        };
        (Tmp(root), spec, ph)
    }

    #[test]
    fn seeds_only_when_absent() {
        let (_t, spec, ph) = setup(
            "seed",
            json!({"config_files": {"Ares/settings.bml": "config/0/settings.bml"}, "actions": []}),
        );
        run(&spec, &ph).expect("run");
        let dest = ph.state.join("Ares").join("settings.bml");
        assert_eq!(fs::read_to_string(&dest).expect("read"), "seed");
        assert!(ph.state.join("saves").is_dir());
        fs::write(&dest, "mine").expect("write");
        run(&spec, &ph).expect("run again");
        assert_eq!(fs::read_to_string(&dest).expect("read"), "mine");
    }

    #[test]
    fn seeds_beside_the_emulator() {
        let (_t, spec, ph) = setup(
            "emu",
            json!({"config_root": "emulator", "config_files": {"portable/settings.bml": "config/0/settings.bml"}, "actions": []}),
        );
        run(&spec, &ph).expect("run");
        assert!(ph.emulator.join("portable").join("settings.bml").is_file());
    }

    #[test]
    fn a_missing_seed_is_an_error() {
        let (_t, spec, ph) = setup(
            "missing",
            json!({"config_files": {"a": "config/nope"}, "actions": []}),
        );
        assert!(run(&spec, &ph).expect_err("should fail").contains("missing"));
    }

    #[test]
    fn actions_in_order() {
        let (_t, spec, ph) = setup(
            "actions",
            json!({"config_files": {}, "actions": [
                {"mkdir": {"path": "{state}/User/Cache"}},
                {"ini": {"file": "User/Config/Dolphin.ini", "section": "Core", "key": "SIMDisplay", "value": "False"}},
                {"ini": {"file": "{state}/User/Config/Dolphin.ini", "section": "General", "key": "ISOPath0", "value": "{install}"}},
                {"copy": {"from": "config/0/settings.bml", "to": "copied.bml", "once": true}},
                {"copy": {"from": "config/0", "to": "{state}/tree"}}
            ]}),
        );
        run(&spec, &ph).expect("run");
        assert!(ph.state.join("User").join("Cache").is_dir());
        let ini = fs::read_to_string(ph.state.join("User").join("Config").join("Dolphin.ini")).expect("ini");
        assert_eq!(
            ini,
            format!(
                "[Core]\nSIMDisplay = False\n[General]\nISOPath0 = {}\n",
                ph.install.display()
            )
        );
        assert_eq!(
            fs::read_to_string(ph.state.join("copied.bml")).expect("copy"),
            "seed"
        );
        assert!(ph.state.join("tree").join("settings.bml").is_file());
        // once: a second launch leaves the copy alone.
        fs::write(ph.state.join("copied.bml"), "mine").expect("write");
        run(&spec, &ph).expect("run again");
        assert_eq!(
            fs::read_to_string(ph.state.join("copied.bml")).expect("copy"),
            "mine"
        );
    }
}
