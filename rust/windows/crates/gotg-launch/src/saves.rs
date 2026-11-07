//! Save sync through `gotg-saves` (docs/windows.md, steps 4 and 6): one
//! reconciliation before the game, a push after it, and `gotg saves ...`.
//! The decisions -- what stops a launch and what is only said -- are here;
//! the sync itself is the crate's.

use std::path::Path;
use std::time::Duration;

use gotg_saves::{Client, Env, Error, Manifest, Mode, Pulled, Pushed, Restored, Saves, Side, State, Synced};
use serde_json::{Value, json};

use crate::api::Api;

/// Everything a sync needs to know about this game, borrowed from the spec.
#[derive(Debug, Clone, Copy)]
pub struct SavesJob<'a> {
    /// The environment (`windows.name`): the store's key and `{state}`'s name.
    pub env: &'a str,
    /// `{state}`: the root the globs below are relative to.
    pub state: &'a Path,
    /// `windows.saves`, `windows.excludes`, `windows.states`: the Linux
    /// saves.json fields, so a save moves between the two.
    pub saves: &'a [String],
    pub excludes: &'a [String],
    pub states: Option<&'a Value>,
    /// The service, token and device name from api.json; None when not logged in.
    pub api: Option<&'a Api>,
    /// The journal and archives (`%LOCALAPPDATA%\gotg\saves`).
    pub home: &'a Path,
    /// Where the per-environment lock lives, shared with the downloads'.
    pub locks: &'a Path,
}

pub trait SaveSync {
    /// Before the game starts. An Err stops the launch: a conflict, or a pull
    /// that failed after it began writing here.
    fn before(&self, job: &SavesJob) -> Result<(), String>;
    /// After the game exits. An Err is reported; the game's exit code stands.
    fn after(&self, job: &SavesJob) -> Result<(), String>;
    /// `gotg saves <args...>`: the exit code.
    fn command(&self, job: &SavesJob, args: &[String]) -> Result<i32, String>;
}

/// The implementation this build uses.
pub fn backend() -> Box<dyn SaveSync> {
    Box::new(ServiceSync)
}

/// Syncs with the service through gotg-saves.
#[derive(Debug, Default)]
struct ServiceSync;

fn env_of(job: &SavesJob) -> Result<Env, String> {
    let manifest = Manifest::from_json(&json!({
        "saves": job.saves, "excludes": job.excludes, "states": job.states,
    }))
    .map_err(|e| e.to_string())?;
    Env::new(job.env, job.state, manifest).map_err(|e| e.to_string())
}

/// api.json's `name` is what a person reads when choosing between two
/// machines' saves; GOTG_DEVICE_NAME overrides it, as on Linux.
fn device(api: &Api) -> String {
    std::env::var("GOTG_DEVICE_NAME")
        .ok()
        .filter(|n| !n.is_empty())
        .or_else(|| api.name.clone())
        .unwrap_or_default()
}

fn store(job: &SavesJob) -> Saves {
    let saves = Saves::new(job.home);
    match job.api {
        Some(api) => saves.with_client(Client {
            server: api.url.clone(),
            token: api.token.clone(),
            device: device(api),
        }),
        None => saves,
    }
}

// Two gotg.exe on one environment (a double double-click) must not both
// write its saves; the bash client's with_lock waits 30 s the same way.
fn lock(job: &SavesJob) -> Result<std::fs::File, String> {
    crate::fsx::lock(
        &job.locks.join(format!("{}.saves.lock", job.env)),
        Duration::from_secs(30),
        &format!("syncing {}'s saves", job.env),
    )
}

/// The service could not be asked: the launch goes on, as on Linux.
fn unreachable(e: &Error) -> bool {
    matches!(
        e,
        Error::Unreachable { .. } | Error::TokenRefused | Error::NoService | Error::TooLarge { .. }
    )
}

pub fn conflict_message(env: &str, head: Option<&gotg_saves::Meta>) -> String {
    let remote = head.map_or_else(String::new, |h| {
        format!(
            "\n     remote: generation {} from {}, {}",
            h.generation,
            if h.device.is_empty() { "?" } else { &h.device },
            h.written_at
        )
    });
    format!(
        "{env}: this computer and the service both have new saves since they last agreed.{remote}\n     \
         Nothing was changed and the game was not started. Choose one, then start it again:\n       \
         keep the service's (what is here is archived first):  gotg saves keep remote\n       \
         keep this computer's (the service keeps its copy):    gotg saves keep here\n       \
         look first:                                           gotg saves list"
    )
}

fn say_synced(env: &str, s: &Synced) -> Result<(), String> {
    match s {
        Synced::Pushed(m) => crate::log(&format!("{env}: pushed saves, generation {}", m.generation)),
        Synced::Pulled { generation } => crate::log(&format!("{env}: pulled saves, generation {generation}")),
        Synced::Conflict { head } => return Err(conflict_message(env, head.as_ref())),
        Synced::Nothing(_) => {}
    }
    Ok(())
}

impl SaveSync for ServiceSync {
    fn before(&self, job: &SavesJob) -> Result<(), String> {
        if job.saves.is_empty() {
            return Ok(());
        }
        if job.api.is_none() {
            crate::warn("not signed in: saves stay on this computer (gotg login to sync them)");
            return Ok(());
        }
        let env = env_of(job)?;
        let saves = store(job);
        let _lock = lock(job)?;
        match saves.sync(&env, Mode::Both) {
            // An unreachable service or a refused token: said, and played on.
            Ok(Synced::Nothing(State::Offline)) => {
                let why = saves.check(&env).ok().and_then(|c| c.offline_reason);
                crate::warn(&format!(
                    "{}: saves not synced ({}); playing with the ones here, pushed next time",
                    job.env,
                    why.as_deref().unwrap_or("the service could not be asked")
                ));
                Ok(())
            }
            Ok(s) => say_synced(job.env, &s),
            Err(e) if unreachable(&e) => {
                crate::warn(&format!("{}: saves not synced: {e}", job.env));
                Ok(())
            }
            Err(e) => Err(format!("{}: saves: {e}", job.env)),
        }
    }

    fn after(&self, job: &SavesJob) -> Result<(), String> {
        if job.saves.is_empty() || job.api.is_none() {
            return Ok(());
        }
        let env = env_of(job)?;
        let _lock = lock(job)?;
        match store(job).push(&env, false) {
            Ok(Pushed::Published(m)) => {
                crate::log(&format!("{}: pushed saves, generation {}", job.env, m.generation));
                Ok(())
            }
            Ok(Pushed::Conflict(head)) => Err(format!(
                "another computer pushed {}'s saves during this game (generation {} from {}); \
                 both are intact: gotg saves keep here|remote",
                job.env,
                head.generation,
                if head.device.is_empty() { "?" } else { &head.device }
            )),
            Ok(Pushed::NothingHere | Pushed::Unchanged) => Ok(()),
            Err(e) => Err(format!("{e}; they are kept here for the next try")),
        }
    }

    fn command(&self, job: &SavesJob, args: &[String]) -> Result<i32, String> {
        let words: Vec<&str> = args.iter().map(String::as_str).collect();
        let env = env_of(job)?;
        let saves = store(job);
        let need_login = |e: Error| match e {
            Error::NoService => "not signed in -- run: gotg login".to_string(),
            e => e.to_string(),
        };
        match words.as_slice() {
            ["list"] => {
                let list = saves.list(&env).map_err(need_login)?;
                print!("{}", render_list(&list, job.api.is_some()));
                Ok(0)
            }
            ["check"] | ["status"] => {
                let c = saves.check(&env).map_err(need_login)?;
                println!("{}: {}", c.env, c.state.as_str());
                if let Some(r) = c.offline_reason {
                    println!("  {r}");
                }
                Ok(0)
            }
            ["push"] | ["push", "--force"] => {
                let _lock = lock(job)?;
                match saves.push(&env, words.len() == 2).map_err(need_login)? {
                    Pushed::Published(m) => println!("{}: pushed generation {}", job.env, m.generation),
                    Pushed::Conflict(h) => return Err(conflict_message(job.env, Some(&h))),
                    Pushed::NothingHere => println!("{}: no saves here to push", job.env),
                    Pushed::Unchanged => println!("{}: already up to date", job.env),
                }
                Ok(0)
            }
            ["pull"] => {
                let _lock = lock(job)?;
                match saves.pull(&env).map_err(need_login)? {
                    Pulled::Fetched { generation, .. } => {
                        println!("{}: pulled generation {generation}", job.env)
                    }
                    Pulled::AlreadyHave { generation } => {
                        println!("{}: already has generation {generation}", job.env)
                    }
                    Pulled::NothingRemote => println!("{}: nothing pushed yet", job.env),
                }
                Ok(0)
            }
            ["restore", pick] => {
                let _lock = lock(job)?;
                match saves.restore(&env, pick).map_err(need_login)? {
                    Restored::Done => println!("{}: restored {pick}", job.env),
                    Restored::AlreadyHere => println!("{}: {pick} is what is here already", job.env),
                }
                Ok(0)
            }
            ["keep", side @ ("here" | "remote")] => {
                let side = if *side == "here" { Side::Here } else { Side::Remote };
                let _lock = lock(job)?;
                match saves.keep(&env, side).map_err(need_login)? {
                    Synced::Pushed(m) => {
                        println!("{}: kept this computer's, generation {}", job.env, m.generation)
                    }
                    Synced::Pulled { generation } => {
                        println!("{}: kept the service's, generation {generation}", job.env)
                    }
                    Synced::Conflict { head } => return Err(conflict_message(job.env, head.as_ref())),
                    Synced::Nothing(_) => println!("{}: nothing to settle", job.env),
                }
                Ok(0)
            }
            _ => Err(format!("usage: {SAVES_USAGE}")),
        }
    }
}

pub const SAVES_USAGE: &str =
    "gotg saves list | check | push [--force] | pull | restore <id> | keep here|remote";

fn human(n: u64) -> String {
    match n {
        n if n >= 1 << 20 => format!("{:.1} MiB", n as f64 / f64::from(1 << 20)),
        n if n >= 1 << 10 => format!("{:.1} KiB", n as f64 / f64::from(1 << 10)),
        n => format!("{n} B"),
    }
}

/// `gotg saves list`: one line per save, its id first (what restore takes).
pub fn render_list(list: &gotg_saves::SaveList, logged_in: bool) -> String {
    let mut out = format!("{}\n", list.env);
    if list.offline {
        out.push_str(if logged_in {
            "  (the service could not be asked: this computer's archives only)\n"
        } else {
            "  (not signed in: this computer's archives only)\n"
        });
    }
    if list.saves.is_empty() {
        out.push_str("  no saves to go back to\n");
    }
    for s in &list.saves {
        out.push_str(&format!(
            "  {}{}  {}  {}  {}\n",
            s.id,
            if s.here { " (here)" } else { "" },
            if s.device.is_empty() { "?" } else { &s.device },
            s.written_at,
            human(s.size)
        ));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use gotg_saves::{Meta, SaveEntry, SaveList};

    #[test]
    fn conflict_says_how_to_settle_it() {
        let head = Meta {
            generation: 4,
            hash: "0".repeat(64),
            size: 10,
            device: "deck".into(),
            written_at: "2026-11-01T10:00:00Z".into(),
        };
        let m = conflict_message("env-snes", Some(&head));
        assert!(
            m.contains("gotg saves keep here") && m.contains("gotg saves keep remote"),
            "{m}"
        );
        assert!(m.contains("generation 4 from deck"), "{m}");
        assert!(conflict_message("env-snes", None).contains("not started"));
    }

    #[test]
    fn a_list_names_what_restore_takes() {
        let list = SaveList {
            env: "env-snes".into(),
            dedicated: false,
            offline: true,
            saves: vec![SaveEntry {
                id: "local:2026-08-01T120000Z-0123456789ab.tar.zst".into(),
                source: "local",
                generation: None,
                hash: String::new(),
                device: "pc".into(),
                written_at: "2026-08-01T12:00:00Z".into(),
                size: 2048,
                here: true,
            }],
        };
        let text = render_list(&list, false);
        assert!(text.contains("not signed in"), "{text}");
        assert!(
            text.contains(
                "  local:2026-08-01T120000Z-0123456789ab.tar.zst (here)  pc  2026-08-01T12:00:00Z  2.0 KiB"
            ),
            "{text}"
        );
    }

    fn job<'a>(root: &'a Path, saves: &'a [String]) -> SavesJob<'a> {
        SavesJob {
            env: "env-snes",
            state: root,
            saves,
            excludes: &[],
            states: None,
            api: None,
            home: root,
            locks: root,
        }
    }

    #[test]
    fn not_signed_in_launches_and_commands_say_login() {
        let root = std::env::temp_dir().join(format!("gotg-saves-nologin-{}", std::process::id()));
        let globs = vec!["saves/**".to_string()];
        let j = job(&root, &globs);
        assert!(backend().before(&j).is_ok());
        assert!(backend().after(&j).is_ok());
        let err = backend()
            .command(&j, &["push".to_string()])
            .expect_err("needs the service");
        assert!(err.contains("gotg login"), "{err}");
        assert!(backend().command(&j, &["frob".to_string()]).is_err());
        assert_eq!(backend().command(&j, &["list".to_string()]), Ok(0));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn device_is_the_logins_name() {
        let api = Api {
            url: "https://x".into(),
            token: "t".into(),
            name: Some("daniel-laptop".into()),
        };
        if std::env::var_os("GOTG_DEVICE_NAME").is_none() {
            assert_eq!(device(&api), "daniel-laptop");
        }
    }
}
