//! The game's own cgroup: the launch moves itself into a `gotg-game-<pid>.scope`
//! before it becomes the game (src/client/lib/killswitch.sh), so stopping the
//! game is stopping that scope -- whatever forked out of the process tree or
//! its group goes with it.

use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

use crate::loading::{self, Stopped};

pub const PREFIX: &str = "gotg-game-";
pub const CGROUP_ROOT: &str = "/sys/fs/cgroup";
const KILLED_WAIT_MS: u64 = 2000;

/// The cgroup v2 path in a /proc/<pid>/cgroup file, when it is one of ours.
pub fn scope_path(cgroup_file: &str) -> Option<&str> {
    let path = cgroup_file.lines().find_map(|line| line.strip_prefix("0::"))?;
    let leaf = path.rsplit('/').next()?;
    (leaf.starts_with(PREFIX) && leaf.ends_with(".scope") && !path.contains("..")).then_some(path)
}

/// One game's scope, on a cgroup2 mount.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Scope {
    dir: PathBuf,
}

impl Scope {
    /// The scope `pid` is in, if the launch put it in one.
    pub fn of(pid: i32) -> Option<Self> {
        let file = fs::read_to_string(format!("/proc/{pid}/cgroup")).ok()?;
        Self::at(Path::new(CGROUP_ROOT), &file)
    }

    pub fn at(root: &Path, cgroup_file: &str) -> Option<Self> {
        let dir = root.join(scope_path(cgroup_file)?.trim_start_matches('/'));
        dir.join("cgroup.procs").is_file().then_some(Self { dir })
    }

    pub fn procs(&self) -> Vec<i32> {
        fs::read_to_string(self.dir.join("cgroup.procs"))
            .map(|text| text.lines().filter_map(|line| line.trim().parse().ok()).collect())
            .unwrap_or_default()
    }

    /// Something in it has not exited. A zombie counts as gone.
    pub fn populated(&self) -> bool {
        self.procs().into_iter().any(crate::procstat::alive)
    }

    /// Everything in it, at once, by the kernel; each pid by hand where the
    /// kernel is older than cgroup.kill (5.14).
    pub fn kill(&self) {
        if fs::write(self.dir.join("cgroup.kill"), "1").is_ok() {
            return;
        }
        for pid in self.procs() {
            // SAFETY: a signal to a pid; nothing is shared.
            unsafe { libc::kill(pid, libc::SIGKILL) };
        }
    }
}

/// The pids of the scope that are not in the tree, in the order to ask them:
/// the tree has had its SIGTERM, deepest first, and these are what left it.
pub fn escaped(procs: &[i32], tree: &[i32]) -> Vec<i32> {
    let tree: BTreeSet<i32> = tree.iter().copied().collect();
    procs.iter().copied().filter(|pid| !tree.contains(pid)).collect()
}

/// [`loading::stop_tree_unless`] for a contained game: the tree is asked
/// first and deepest first -- bwrap's --die-with-parent would SIGKILL an
/// emulator whose wrapper went before it, mid-save -- then whatever escaped
/// it; the wait is on the scope, and the end of the grace is `cgroup.kill`.
pub fn stop_scope_unless(
    scope: &Scope,
    game: i32,
    grace_ms: u64,
    poll_ms: u64,
    abort: &dyn Fn() -> bool,
) -> Stopped {
    let tree = loading::descendants(game, &loading::processes());
    for &pid in tree.iter().rev().chain(escaped(&scope.procs(), &tree).iter()) {
        // SAFETY: a signal to a pid; nothing is shared.
        unsafe { libc::kill(pid, libc::SIGTERM) };
    }
    let mut waited = 0;
    while waited < grace_ms && scope.populated() && !abort() {
        std::thread::sleep(std::time::Duration::from_millis(poll_ms.max(10)));
        waited += poll_ms.max(10);
    }
    if !scope.populated() {
        return Stopped::Gently;
    }
    if abort() {
        return Stopped::LeftToIt;
    }
    scope.kill();
    // cgroup.kill is asynchronous: "killed" is said once the scope is empty.
    let mut waited = 0;
    while waited < KILLED_WAIT_MS && scope.populated() {
        std::thread::sleep(std::time::Duration::from_millis(10));
        waited += 10;
    }
    Stopped::Killed
}

#[cfg(test)]
mod tests {
    use super::*;

    const OURS: &str = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/gotg-game-4242.scope\n";

    #[test]
    fn only_a_gotg_game_scope_is_ours() {
        assert_eq!(
            scope_path(OURS),
            Some("/user.slice/user-1000.slice/user@1000.service/app.slice/gotg-game-4242.scope")
        );
        for theirs in [
            "0::/user.slice/user-1000.slice/session-3.scope\n",
            "0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-steam.scope\n",
            "0::/app.slice/gotg-game-1.service\n",
            "0::/a/../gotg-game-1.scope\n",
            "1:name=systemd:/gotg-game-1.scope\n",
            "",
        ] {
            assert_eq!(scope_path(theirs), None, "{theirs:?}");
        }
    }

    #[test]
    fn a_scope_is_found_under_the_root_only_when_it_is_there() {
        let root = std::env::temp_dir().join(format!("gotg-contain-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        assert_eq!(Scope::at(&root, OURS), None, "gone already: nothing to stop");
        let dir = root.join(scope_path(OURS).expect("ours").trim_start_matches('/'));
        fs::create_dir_all(&dir).expect("a scope directory");
        fs::write(dir.join("cgroup.procs"), "17\n4242\n\n").expect("cgroup.procs");
        let scope = Scope::at(&root, OURS).expect("there");
        assert_eq!(scope.procs(), vec![17, 4242]);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn what_left_the_tree_is_asked_after_it_and_nothing_twice() {
        assert_eq!(escaped(&[10, 11, 99, 12], &[10, 11, 12]), vec![99]);
        assert!(escaped(&[], &[10]).is_empty());
    }
}
