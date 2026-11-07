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

/// What a launch's `first_run` comes to, decided without touching anything
/// (a dry run prints it), then carried out by [`first_run_apply`].
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct FirstRunPlan {
    /// Archives stamped by another version (or unstamped): deleted first.
    pub delete: Vec<PathBuf>,
    /// The stamp to write, on a first run with one.
    pub stamp: Option<(PathBuf, String)>,
    /// Start with `first_run.args` rather than `args`.
    pub first: bool,
}

pub fn first_run_plan(spec: &Spec, ph: &Placeholders) -> FirstRunPlan {
    let Some(fr) = &spec.windows.first_run else {
        return FirstRunPlan::default();
    };
    let paths: Vec<PathBuf> = fr.unless_exists.iter().map(|p| ph.path(p, &ph.state)).collect();
    let stamp = fr
        .stamp
        .as_ref()
        .map(|s| (ph.path(&s.file, &ph.state), s.value.clone()));
    // As the Linux preLaunch reads it, $(cat): trailing newlines are not the value.
    let stale = stamp.as_ref().is_some_and(|(file, value)| {
        fs::read_to_string(file)
            .map(|t| t.trim_end_matches(['\n', '\r']) != value)
            .unwrap_or(true)
    });
    let delete: Vec<PathBuf> = if stale {
        paths.iter().filter(|p| p.exists()).cloned().collect()
    } else {
        Vec::new()
    };
    let first = paths.iter().all(|p| !p.exists() || delete.contains(p));
    FirstRunPlan {
        delete,
        stamp: if first { stamp } else { None },
        first,
    }
}

pub fn first_run_apply(plan: &FirstRunPlan) -> Result<(), String> {
    for p in &plan.delete {
        crate::log(&format!(
            "{} is from another version; extracting again",
            p.display()
        ));
        let r = if p.is_dir() {
            fs::remove_dir_all(p)
        } else {
            fs::remove_file(p)
        };
        r.map_err(|e| format!("{}: {e}", p.display()))?;
    }
    // Stamped before the start, as on Linux: an extraction that fails leaves
    // no archive, and the next launch tries again.
    if let Some((file, value)) = &plan.stamp {
        if let Some(dir) = file.parent() {
            fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
        }
        fsx::write_atomic(file, value.as_bytes(), None)?;
    }
    Ok(())
}

/// The working directory, rendered (relative: under `{state}`).
pub fn cwd(spec: &Spec, ph: &Placeholders) -> PathBuf {
    ph.path(&spec.windows.cwd, &ph.state)
}

pub fn run(spec: &Spec, ph: &Placeholders) -> Result<(), String> {
    saves_dirs(spec, &ph.state)?;
    seed_config(spec, ph)?;
    for (i, a) in spec.windows.actions.iter().enumerate() {
        apply(a, ph).map_err(|e| format!("action {}: {e}", i + 1))?;
    }
    let dir = cwd(spec, ph);
    fs::create_dir_all(&dir).map_err(|e| format!("cwd {}: {e}", dir.display()))
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

    #[test]
    fn cwd_defaults_to_state_and_is_made() {
        let (_t, spec, ph) = setup("cwd", json!({"config_files": {}, "actions": []}));
        assert_eq!(cwd(&spec, &ph), ph.state);
        let (_t, spec, ph) = setup(
            "cwd2",
            json!({"config_files": {}, "cwd": "{state}/data/soh", "actions": []}),
        );
        assert_eq!(cwd(&spec, &ph), ph.state.join("data").join("soh"));
        run(&spec, &ph).expect("run");
        assert!(ph.state.join("data").join("soh").is_dir());
        // Relative: under {state}, like an action's path.
        let (_t, spec, ph) = setup(
            "cwd3",
            json!({"config_files": {}, "cwd": "data/x", "actions": []}),
        );
        assert_eq!(cwd(&spec, &ph), ph.state.join("data").join("x"));
    }

    fn harkinian(tag: &str) -> (Tmp, Spec, Placeholders) {
        setup(
            tag,
            json!({"config_files": {}, "actions": [], "args": [], "cwd": "{state}/data/soh", "first_run": {
                "unless_exists": ["{state}/data/soh/oot.o2r", "{state}/data/soh/oot-mq.o2r"],
                "stamp": {"file": "{state}/data/soh/.gotg-archive-version", "value": "9.3.0"},
                "args": ["{target}"]}}),
        )
    }

    #[test]
    fn first_run_without_an_archive_stamps_and_passes_the_rom() {
        let (_t, spec, ph) = harkinian("fr1");
        let data = ph.state.join("data").join("soh");
        let plan = first_run_plan(&spec, &ph);
        assert!(plan.first && plan.delete.is_empty());
        assert_eq!(
            plan.stamp,
            Some((data.join(".gotg-archive-version"), "9.3.0".into()))
        );
        first_run_apply(&plan).expect("apply");
        assert_eq!(
            fs::read_to_string(data.join(".gotg-archive-version")).expect("stamp"),
            "9.3.0"
        );
    }

    #[test]
    fn an_archive_of_this_version_is_a_normal_run() {
        let (_t, spec, ph) = harkinian("fr2");
        let data = ph.state.join("data").join("soh");
        fs::create_dir_all(&data).expect("mkdir");
        fs::write(data.join("oot-mq.o2r"), "archive").expect("write");
        // Linux's echo leaves a newline; $(cat) does not read it.
        fs::write(data.join(".gotg-archive-version"), "9.3.0\n").expect("write");
        let plan = first_run_plan(&spec, &ph);
        assert_eq!(plan, FirstRunPlan::default());
        first_run_apply(&plan).expect("apply");
        assert!(data.join("oot-mq.o2r").exists());
    }

    #[test]
    fn an_archive_of_another_version_is_deleted_and_extracted_again() {
        for (tag, stamp) in [("fr3", Some("9.2.0")), ("fr4", None)] {
            let (_t, spec, ph) = harkinian(tag);
            let data = ph.state.join("data").join("soh");
            fs::create_dir_all(&data).expect("mkdir");
            fs::write(data.join("oot.o2r"), "old").expect("write");
            if let Some(s) = stamp {
                fs::write(data.join(".gotg-archive-version"), s).expect("write");
            }
            let plan = first_run_plan(&spec, &ph);
            assert!(plan.first, "{tag}");
            assert_eq!(plan.delete, vec![data.join("oot.o2r")]);
            first_run_apply(&plan).expect("apply");
            assert!(!data.join("oot.o2r").exists());
            assert_eq!(
                fs::read_to_string(data.join(".gotg-archive-version")).expect("stamp"),
                "9.3.0"
            );
        }
    }

    #[test]
    fn no_stamp_means_only_presence_counts() {
        let (_t, spec, ph) = setup(
            "fr5",
            json!({"config_files": {}, "actions": [], "first_run": {"unless_exists": ["a.o2r"], "stamp": null, "args": ["x"]}}),
        );
        assert!(first_run_plan(&spec, &ph).first);
        assert_eq!(first_run_plan(&spec, &ph).stamp, None);
        fs::create_dir_all(&ph.state).expect("mkdir");
        fs::write(ph.state.join("a.o2r"), "").expect("write");
        assert!(!first_run_plan(&spec, &ph).first);
        // And without first_run at all, nothing.
        let (_t, spec, ph) = setup("fr6", json!({"config_files": {}, "actions": []}));
        assert_eq!(first_run_plan(&spec, &ph), FirstRunPlan::default());
    }

    #[test]
    fn copy_from_the_target_once() {
        let (_t, spec, ph) = setup(
            "target",
            json!({"config_files": {}, "actions": [{"copy": {"from": "{target}", "to": "{state}/data/battleship/baserom.us.z64", "once": true}}]}),
        );
        fs::create_dir_all(&ph.install).expect("mkdir");
        fs::write(&ph.target, "ROM").expect("write");
        run(&spec, &ph).expect("run");
        let dest = ph.state.join("data").join("battleship").join("baserom.us.z64");
        assert_eq!(fs::read_to_string(&dest).expect("copied"), "ROM");
        fs::write(&ph.target, "NEWER").expect("write");
        run(&spec, &ph).expect("run again");
        assert_eq!(fs::read_to_string(&dest).expect("kept"), "ROM");
    }
}
