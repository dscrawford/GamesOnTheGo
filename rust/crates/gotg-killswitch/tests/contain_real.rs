//! The stop against real processes in a real scope: the launch's chain as it
//! runs -- a wrapper that spawns bwrap --die-with-parent, an emulator inside
//! that ignores SIGTERM, and a helper that setsid'd out of the tree. Needs a
//! systemd user manager, busctl and bwrap, which a build sandbox has none of:
//! `cargo test -p gotg-killswitch --test contain_real -- --ignored`.

use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use gotg_killswitch::contain::{Scope, stop_scope_unless};
use gotg_killswitch::loading::Stopped;
use gotg_killswitch::procstat::alive;

const GAME: &str = r#"
set -u
trap '' TERM
busctl --user call org.freedesktop.systemd1 /org/freedesktop/systemd1 \
  org.freedesktop.systemd1.Manager StartTransientUnit 'ssa(sv)a(sa(sv))' \
  "gotg-game-$$.scope" fail 2 PIDs au 1 $$ CollectMode s inactive-or-failed 0 >/dev/null
until grep -q "/gotg-game-$$.scope" /proc/$$/cgroup; do sleep 0.02; done
bwrap --die-with-parent --dev-bind / / bash -c '
  trap "" TERM
  ( setsid bash -c "trap \"\" TERM; echo \$\$ >\"$1/escaped\"; exec sleep 300" & )
  echo $$ >"$1/emulator"
  exec sleep 300
' _ "$1" &
wait
"#;

fn read_pid(path: &PathBuf) -> Option<i32> {
    std::fs::read_to_string(path).ok()?.trim().parse().ok()
}

fn waited<T>(mut ask: impl FnMut() -> Option<T>) -> T {
    let start = Instant::now();
    loop {
        if let Some(found) = ask() {
            return found;
        }
        assert!(
            start.elapsed() < Duration::from_secs(10),
            "the chain did not come up"
        );
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[test]
#[ignore = "needs a systemd user manager, busctl and bwrap"]
fn a_contained_game_and_everything_that_left_its_tree_are_stopped() {
    let dir = std::env::temp_dir().join(format!("gotg-contain-real-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("a work directory");
    let mut wrapper = Command::new("bash")
        .args(["-c", GAME, "_"])
        .arg(&dir)
        .stdin(Stdio::null())
        .spawn()
        .expect("the wrapper starts");
    let pid = i32::try_from(wrapper.id()).expect("a pid");

    let emulator = waited(|| read_pid(&dir.join("emulator")));
    let escaped = waited(|| read_pid(&dir.join("escaped")));
    let scope = waited(|| Scope::of(pid));
    assert!(
        scope.procs().contains(&escaped),
        "the escapee is still in the scope"
    );
    let tree = gotg_killswitch::loading::descendants(pid, &gotg_killswitch::loading::processes());
    assert!(
        !tree.contains(&escaped),
        "and out of the tree, where the walk alone cannot reach it"
    );

    let stopped = stop_scope_unless(&scope, pid, 1000, 50, &|| false);
    let _ = wrapper.wait();

    assert_eq!(stopped, Stopped::Killed, "everything here ignores SIGTERM");
    assert!(!alive(emulator), "the emulator went");
    assert!(!alive(escaped), "and what left its tree");
    assert!(!scope.populated(), "the scope is empty");
    let _ = std::fs::remove_dir_all(&dir);
}
