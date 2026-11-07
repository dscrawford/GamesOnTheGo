//! The seam for save syncing, which is `gotg-saves`' (rust/crates/gotg-saves)
//! and not this crate's. A launch calls `pull` before the game and `push`
//! after it; `gotg saves ...` hands its words to `command`. Until that crate
//! is wired in, [`NoSync`] does nothing and says so once.

use std::path::Path;

use serde_json::Value;

use crate::api::Api;

/// Everything a sync needs to know about this game, borrowed from the spec.
// Read by the gotg-saves backend; NoSync needs only `saves`.
#[allow(dead_code)]
#[derive(Debug, Clone, Copy)]
pub struct SavesJob<'a> {
    /// The store's key: the spec's `attr` (`env-...`), as on Linux.
    pub attr: &'a str,
    /// `{state}`: the root the globs below are relative to.
    pub state: &'a Path,
    /// `windows.saves`, `windows.excludes`, `windows.states`: the Linux
    /// saves.json fields, so a save moves between the two.
    pub saves: &'a [String],
    pub excludes: &'a [String],
    pub states: Option<&'a Value>,
    /// The service and token from api.json; None when not logged in.
    pub api: Option<&'a Api>,
}

pub trait SaveSync {
    /// Before the game starts. An Err stops the launch: playing on top of a
    /// save that failed to arrive is how progress gets lost.
    fn pull(&self, job: &SavesJob) -> Result<(), String>;
    /// After the game exits. An Err is reported; the game's exit code stands.
    fn push(&self, job: &SavesJob) -> Result<(), String>;
    /// `gotg saves <args...>` (list | push | pull | restore ...): the exit code.
    fn command(&self, job: &SavesJob, args: &[String]) -> Result<i32, String>;
}

/// No syncing: saves stay on this machine.
#[derive(Debug, Default)]
pub struct NoSync;

impl SaveSync for NoSync {
    fn pull(&self, job: &SavesJob) -> Result<(), String> {
        if !job.saves.is_empty() {
            crate::log("saves are kept on this computer only (this gotg.exe does not sync them yet)");
        }
        Ok(())
    }

    fn push(&self, _job: &SavesJob) -> Result<(), String> {
        Ok(())
    }

    fn command(&self, _job: &SavesJob, _args: &[String]) -> Result<i32, String> {
        Err("this gotg.exe does not sync saves yet".into())
    }
}

/// The implementation this build uses.
pub fn backend() -> Box<dyn SaveSync> {
    Box::new(NoSync)
}
