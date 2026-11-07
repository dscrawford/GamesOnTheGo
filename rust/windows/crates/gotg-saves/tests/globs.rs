//! The shared glob fixtures: what this crate takes as a save, against what
//! they say -- and, given the bash client, against what it takes.
#![allow(clippy::unwrap_used)]

mod common;

use common::{Linux, Scratch, bash_client, write};
use gotg_saves::{Env, Manifest};
use serde_json::Value;

fn cases() -> Vec<Value> {
    let text = include_str!("fixtures/globs.json");
    serde_json::from_str::<Value>(text)
        .unwrap()
        .as_array()
        .unwrap()
        .clone()
}

fn strings(v: &Value) -> Vec<String> {
    v.as_array()
        .map(|a| a.iter().map(|s| s.as_str().unwrap().to_string()).collect())
        .unwrap_or_default()
}

fn lay_out(root: &std::path::Path, case: &Value) -> bool {
    for f in strings(&case["files"]) {
        write(root, &f, f.as_bytes());
    }
    let links = case["symlinks"].as_array().cloned().unwrap_or_default();
    if links.is_empty() {
        return true;
    }
    #[cfg(unix)]
    {
        for l in links {
            let link = root.join(l[0].as_str().unwrap());
            std::fs::create_dir_all(link.parent().unwrap()).unwrap();
            std::os::unix::fs::symlink(l[1].as_str().unwrap(), link).unwrap();
        }
        true
    }
    #[cfg(not(unix))]
    false
}

#[test]
fn members_are_what_the_fixtures_say() {
    for case in cases() {
        let dir = Scratch::new("globs");
        if !lay_out(dir.path(), &case) {
            continue;
        }
        let env = Env::new("env-globs", dir.path(), Manifest::from_json(&case).unwrap()).unwrap();
        assert_eq!(env.members(), strings(&case["expect"]), "{}", case["name"]);
    }
}

#[test]
fn members_are_what_bash_takes() {
    let Some(client) = bash_client() else { return };
    for case in cases() {
        let linux = Linux::new(&client, "env-globs", "http://127.0.0.1:9", "unused", &case);
        lay_out(&linux.state(), &case);
        let env = Env::new("env-globs", linux.state(), Manifest::from_json(&case).unwrap()).unwrap();
        assert_eq!(linux.members(), env.members(), "{}", case["name"]);
    }
}

#[test]
fn bash_pattern_comparison() {
    use gotg_saves::glob_matches as m;
    assert!(m("saves/**", "saves/a/b/c"));
    assert!(m("saves/*.bs[0-9]", "saves/x/y.bs7"));
    assert!(!m("saves/*.bs[0-9]", "saves/y.bsx"));
    assert!(m("*/tex/**", "data/sm64coopdx/tex/a.png"));
}
