//! Against the real Python service, and the real bash client as the Linux
//! machine: a Windows player's saves and a Linux player's are one save.
#![allow(clippy::unwrap_used)]

mod common;

use std::fs;

use common::{Linux, Scratch, bash_client, env_name, service, tree, write};
use gotg_saves::{Client, Env, Manifest, Mode, Pulled, Pushed, Restored, Saves, Side, State, Synced};
use serde_json::{Value, json};

fn manifest() -> Value {
    json!({"saves": ["saves/**"], "excludes": ["saves/*.bs[0-9]"]})
}

/// One Windows machine: its saves home, its state directory, its device.
struct Windows {
    _dir: Scratch,
    saves: Saves,
    env: Env,
}

impl Windows {
    fn new(env: &str, device: &str, (url, token): &(String, String)) -> Windows {
        let dir = Scratch::new("win");
        let saves = Saves::new(dir.path().join("saves")).with_client(Client {
            server: url.clone(),
            token: token.clone(),
            device: device.into(),
        });
        let env = Env::new(
            env,
            dir.path().join("env").join(env),
            Manifest::from_json(&manifest()).unwrap(),
        )
        .unwrap();
        Windows {
            _dir: dir,
            saves,
            env,
        }
    }

    fn state(&self) -> &std::path::Path {
        &self.env.state_dir
    }
}

fn published(p: Pushed) -> u64 {
    match p {
        Pushed::Published(m) => m.generation,
        other => panic!("not published: {other:?}"),
    }
}

#[test]
fn push_from_empty_then_nothing_to_do() {
    let Some(svc) = service() else { return };
    let w = Windows::new(&env_name("push"), "win-a", &svc);
    assert_eq!(w.saves.check(&w.env).unwrap().state, State::Same);
    assert_eq!(w.saves.push(&w.env, false).unwrap(), Pushed::NothingHere);

    write(w.state(), "saves/a.srm", b"one");
    assert_eq!(w.saves.check(&w.env).unwrap().state, State::HereOnly);
    assert_eq!(published(w.saves.push(&w.env, false).unwrap()), 1);
    let check = w.saves.check(&w.env).unwrap();
    assert_eq!(check.state, State::Same);
    assert_eq!(check.remote.unwrap().device, "win-a");
    assert_eq!(w.saves.push(&w.env, false).unwrap(), Pushed::Unchanged);
    assert_eq!(
        w.saves.sync(&w.env, Mode::Both).unwrap(),
        Synced::Nothing(State::Same)
    );
}

#[test]
fn pull_into_empty_and_excludes_honoured() {
    let Some(svc) = service() else { return };
    let name = env_name("pull");
    let a = Windows::new(&name, "win-a", &svc);
    write(a.state(), "saves/a.srm", b"one");
    write(a.state(), "saves/Nintendo 64/Zelda (USA).ram", b"zelda");
    write(a.state(), "saves/a.bs1", b"a save state, never synced");
    write(a.state(), "saves/sub/b.bs2", b"nor this");
    write(a.state(), "other/not-a-save", b"x");
    assert_eq!(published(a.saves.push(&a.env, false).unwrap()), 1);

    let b = Windows::new(&name, "win-b", &svc);
    assert_eq!(b.saves.check(&b.env).unwrap().state, State::RemoteOnly);
    assert_eq!(
        b.saves.sync(&b.env, Mode::PushOnly).unwrap(),
        Synced::Nothing(State::RemoteOnly)
    );
    assert!(!b.state().exists());
    assert_eq!(
        b.saves.sync(&b.env, Mode::Both).unwrap(),
        Synced::Pulled { generation: 1 }
    );
    assert_eq!(
        tree(b.state()),
        vec![
            ("saves/Nintendo 64/Zelda (USA).ram".to_string(), b"zelda".to_vec()),
            ("saves/a.srm".to_string(), b"one".to_vec()),
        ]
    );
    assert_eq!(b.saves.check(&b.env).unwrap().state, State::Same);
    assert_eq!(b.saves.push(&b.env, false).unwrap(), Pushed::Unchanged);
}

#[test]
fn conflict_is_refused_then_settled() {
    let Some(svc) = service() else { return };
    let name = env_name("conflict");
    let a = Windows::new(&name, "win-a", &svc);
    let b = Windows::new(&name, "win-b", &svc);
    write(a.state(), "saves/a.srm", b"one");
    published(a.saves.push(&a.env, false).unwrap());
    b.saves.sync(&b.env, Mode::Both).unwrap();

    write(a.state(), "saves/a.srm", b"two, from a");
    assert_eq!(published(a.saves.push(&a.env, false).unwrap()), 2);
    write(b.state(), "saves/a.srm", b"two, from b");

    assert_eq!(b.saves.check(&b.env).unwrap().state, State::Conflict);
    match b.saves.sync(&b.env, Mode::Both).unwrap() {
        Synced::Conflict { head: Some(head) } => {
            assert_eq!((head.generation, &head.device[..]), (2, "win-a"))
        }
        other => panic!("{other:?}"),
    }
    match b.saves.push(&b.env, false).unwrap() {
        Pushed::Conflict(head) => assert_eq!(head.generation, 2),
        other => panic!("{other:?}"),
    }
    // Nothing on either side was touched.
    assert_eq!(fs::read(b.state().join("saves/a.srm")).unwrap(), b"two, from b");

    match b.saves.keep(&b.env, Side::Here).unwrap() {
        Synced::Pushed(m) => assert_eq!(m.generation, 3),
        other => panic!("{other:?}"),
    }
    assert_eq!(a.saves.check(&a.env).unwrap().state, State::RemoteNewer);
    assert_eq!(
        a.saves.sync(&a.env, Mode::Both).unwrap(),
        Synced::Pulled { generation: 3 }
    );
    assert_eq!(fs::read(a.state().join("saves/a.srm")).unwrap(), b"two, from b");
    // What a had was archived before it was written over; being generation 2
    // byte for byte, the list offers it once, as the service's.
    let archives = fs::read_dir(a._dir.path().join("saves/local").join(&name))
        .unwrap()
        .count();
    assert_eq!(archives, 1);
    let list = a.saves.list(&a.env).unwrap();
    let ids: Vec<_> = list.saves.iter().map(|s| s.id.as_str()).collect();
    assert_eq!(ids, vec!["remote:3", "remote:2", "remote:1"]);
}

#[test]
fn keep_remote_archives_what_was_here() {
    let Some(svc) = service() else { return };
    let name = env_name("keep");
    let a = Windows::new(&name, "win-a", &svc);
    let b = Windows::new(&name, "win-b", &svc);
    write(a.state(), "saves/a.srm", b"a's");
    published(a.saves.push(&a.env, false).unwrap());
    write(b.state(), "saves/a.srm", b"b's");
    assert_eq!(b.saves.check(&b.env).unwrap().state, State::Conflict);
    assert_eq!(
        b.saves.keep(&b.env, Side::Remote).unwrap(),
        Synced::Pulled { generation: 1 }
    );
    assert_eq!(fs::read(b.state().join("saves/a.srm")).unwrap(), b"a's");
    let list = b.saves.list(&b.env).unwrap();
    let local: Vec<_> = list.saves.iter().filter(|s| s.source == "local").collect();
    assert_eq!(local.len(), 1);
    assert_eq!(b.saves.restore(&b.env, &local[0].id).unwrap(), Restored::Done);
    assert_eq!(fs::read(b.state().join("saves/a.srm")).unwrap(), b"b's");
    // Restored over the head: the next push is the newest generation.
    assert_eq!(published(b.saves.push(&b.env, false).unwrap()), 2);
}

#[test]
fn list_and_restore_a_kept_generation() {
    let Some(svc) = service() else { return };
    let w = Windows::new(&env_name("restore"), "win-a", &svc);
    write(w.state(), "saves/a.srm", b"v1");
    published(w.saves.push(&w.env, false).unwrap());
    write(w.state(), "saves/a.srm", b"v2");
    write(w.state(), "saves/later.srm", b"made after v1");
    published(w.saves.push(&w.env, false).unwrap());

    let list = w.saves.list(&w.env).unwrap();
    assert!(!list.offline);
    let ids: Vec<_> = list.saves.iter().map(|s| (s.id.as_str(), s.here)).collect();
    assert_eq!(ids, vec![("remote:2", true), ("remote:1", false)]);

    assert_eq!(w.saves.restore(&w.env, "remote:1").unwrap(), Restored::Done);
    assert_eq!(tree(w.state()), vec![("saves/a.srm".to_string(), b"v1".to_vec())]);
    assert_eq!(w.saves.check(&w.env).unwrap().state, State::HereNewer);
    assert_eq!(published(w.saves.push(&w.env, false).unwrap()), 3);
    assert_eq!(
        w.saves.restore(&w.env, "remote:3").unwrap(),
        Restored::AlreadyHere
    );
    assert!(w.saves.restore(&w.env, "remote:99").is_err());
    assert!(w.saves.restore(&w.env, "local:../../x").is_err());
}

#[test]
fn a_refused_token_is_offline_to_check_and_an_error_to_push() {
    let Some((url, _)) = service() else { return };
    let w = Windows::new(&env_name("token"), "win-a", &(url, "wrong".into()));
    write(w.state(), "saves/a.srm", b"x");
    let check = w.saves.check(&w.env).unwrap();
    assert_eq!(check.state, State::Offline);
    assert!(check.offline_reason.is_some());
    assert!(matches!(
        w.saves.push(&w.env, false),
        Err(gotg_saves::Error::TokenRefused)
    ));
}

#[test]
fn linux_and_windows_share_one_save() {
    let Some(svc) = service() else { return };
    let Some(client) = bash_client() else { return };
    let name = env_name("interop");
    let linux = Linux::new(&client, &name, &svc.0, &svc.1, &manifest());
    let long = format!("saves/deep/a/b/{}.srm", "n".repeat(120));
    write(&linux.state(), "saves/a.srm", b"from linux");
    write(
        &linux.state(),
        "saves/Nintendo 64/Zelda (USA).ram",
        &[0u8, 1, 2, 255],
    );
    write(&linux.state(), &long, &vec![7u8; 70_000]);
    write(&linux.state(), "saves/ünï.sav", "é".as_bytes());
    write(&linux.state(), "saves/a.bs1", b"a state, excluded");
    let (ok, _, err) = linux.saves(&["push", &name]);
    assert!(ok, "bash push: {err}");

    // The bash client's archive, pulled and laid out here.
    let w = Windows::new(&name, "win-a", &svc);
    assert_eq!(
        w.saves.sync(&w.env, Mode::Both).unwrap(),
        Synced::Pulled { generation: 1 }
    );
    let mut want = tree(&linux.state());
    want.retain(|(p, _)| p != "saves/a.bs1");
    assert_eq!(tree(w.state()), want);
    // Pulled is not changed, though our bytes for it would differ.
    assert_eq!(w.saves.check(&w.env).unwrap().state, State::Same);
    assert_eq!(w.saves.push(&w.env, false).unwrap(), Pushed::Unchanged);

    // Ours, pulled by the bash client: its own verify and extract.
    write(w.state(), "saves/a.srm", b"from windows");
    write(w.state(), "saves/new.srm", b"new on windows");
    write(w.state(), "saves/b.bs2", b"a state here, excluded");
    assert_eq!(published(w.saves.push(&w.env, false).unwrap()), 2);
    let (ok, _, err) = linux.saves(&["pull", &name]);
    assert!(ok, "bash pull: {err}");
    let mut want = tree(w.state());
    want.retain(|(p, _)| p != "saves/b.bs2");
    let mut got = tree(&linux.state());
    got.retain(|(p, _)| p != "saves/a.bs1");
    assert_eq!(got, want);
    assert!(!linux.state().join("saves/b.bs2").exists());
    // Pulled is not changed: the bash client pushes nothing back, though its
    // own bundle of these files is other bytes than ours, and a second pull
    // finds it already there. The service stays at generation 2.
    let (ok, _, err) = linux.saves(&["push", &name]);
    assert!(ok, "bash push after pull: {err}");
    let (ok, _, err) = linux.saves(&["pull", &name]);
    assert!(ok, "bash pull again: {err}");
    assert!(err.contains("already has generation 2"), "bash pull again: {err}");
    let check = w.saves.check(&w.env).unwrap();
    assert_eq!(check.state, State::Same);
    assert_eq!(check.remote.map(|m| m.generation), Some(2));

    // And the bash client's verdict on a conflict made across the two.
    write(&linux.state(), "saves/a.srm", b"linux again");
    let (ok, _, err) = linux.saves(&["push", &name]);
    assert!(ok, "bash push: {err}");
    write(w.state(), "saves/a.srm", b"windows again");
    assert_eq!(w.saves.check(&w.env).unwrap().state, State::Conflict);
    write(w.state(), "saves/a.srm", b"from windows");
    // Back to what it pushed: not changed, so the Linux head is simply newer.
    assert_eq!(w.saves.check(&w.env).unwrap().state, State::RemoteNewer);
    match w.saves.pull(&w.env).unwrap() {
        Pulled::Fetched { generation, .. } => assert!(generation >= 3),
        other => panic!("{other:?}"),
    }
    assert_eq!(fs::read(w.state().join("saves/a.srm")).unwrap(), b"linux again");
}
