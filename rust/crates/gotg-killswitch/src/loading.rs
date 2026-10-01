//! Loading a save from the menu: the list, and starting the game again on one.
//!
//! The menu's saves row is the overlay's half of `gotg saves list` and
//! `gotg saves restore`. The other half is the environment's wrapper
//! (`gotg-play`, env/lib.nix), which runs the game as a child when `gotg play`
//! gave it a session: a directory private to this play, named in
//! GOTG_SESSION_DIR, where the wrapper writes the game's pid (`game.pid`) and
//! where a save picked here is named (`restart`). The overlay stops the game
//! alone -- not its process group, which holds `danstick-rs exec`, the process
//! danstick's session follows -- and the wrapper pushes what was played, puts
//! the pick back and starts the game again as the same process. Everybody
//! keeps their seat, which a new `gotg play` could not give them.
//!
//! The list is the client's (`gotg saves list <env> --lines`): a save a line,
//! its id, when it was made in this machine's words, and what it is. Run in
//! the background into the session, where the painter reads the words too --
//! a frame carries numbers, not a dozen dates and machine names.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};

/// Saves listed at most: more than anybody walks with a d-pad.
pub const LINES_MAX: usize = 50;
/// The client is given this long to list them.
const LIST_SECONDS: f64 = 60.0;

/// One save, as the client said it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SaveLine {
    /// What `gotg saves restore` takes back: `remote:<n>` or `local:<archive>`.
    pub id: String,
    pub when: String,
    pub detail: String,
}

/// The client's lines, as far as they are saves. An id not shaped like one
/// of the two kinds is dropped: it becomes a file the wrapper hands to the
/// client as an argument.
pub fn parse_lines(text: &str) -> Vec<SaveLine> {
    text.lines()
        .filter_map(|line| {
            let mut fields = line.split('\t');
            let (id, when, detail) = (fields.next()?, fields.next()?, fields.next().unwrap_or(""));
            valid_id(id).then(|| SaveLine {
                id: id.to_owned(),
                when: printable(when),
                detail: printable(detail),
            })
        })
        .take(LINES_MAX)
        .collect()
}

fn printable(text: &str) -> String {
    text.chars().filter(|c| !c.is_control()).take(80).collect()
}

fn valid_id(id: &str) -> bool {
    if let Some(generation) = id.strip_prefix("remote:") {
        return !generation.is_empty()
            && generation.len() <= 9
            && generation.bytes().all(|b| b.is_ascii_digit());
    }
    let Some(name) = id.strip_prefix("local:") else {
        return false;
    };
    let bytes = name.as_bytes();
    let shape = "dddd-dd-ddTddddddZ-hhhhhhhhhhhh.tar.zst";
    bytes.len() == shape.len()
        && shape.bytes().zip(bytes).all(|(want, &got)| match want {
            b'd' => got.is_ascii_digit(),
            b'h' => got.is_ascii_digit() || (b'a'..=b'f').contains(&got),
            other => other == got,
        })
}

/// An environment one game has to itself: env-n64-usa_donkey_kong_64, not
/// env-n64, which holds every N64 game without a file of its own -- going
/// back there would rewind all of them. Platform names have no dash.
pub fn dedicated(attr: &str) -> bool {
    let Some(rest) = attr.strip_prefix("env-") else {
        return false;
    };
    match rest.split_once('-') {
        Some((platform, game)) => {
            !platform.is_empty()
                && platform
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
                && game.starts_with(|c: char| c.is_ascii_lowercase() || c.is_ascii_digit())
        }
        None => false,
    }
}

/// This play's session directory.
#[derive(Debug, Clone)]
pub struct Session {
    dir: PathBuf,
}

impl Session {
    /// GOTG_SESSION_DIR, if `gotg play` made one: an absolute directory.
    pub fn from_env() -> Option<Self> {
        Self::at(Path::new(&std::env::var_os("GOTG_SESSION_DIR")?))
    }

    pub fn at(dir: &Path) -> Option<Self> {
        (dir.is_absolute() && dir.is_dir()).then(|| Self { dir: dir.to_owned() })
    }

    pub fn lines_path(&self) -> PathBuf {
        self.dir.join("saves.tsv")
    }

    /// The game, as the wrapper said when it started it. None from a wrapper
    /// that has no restart loop -- whose game cannot be started again.
    pub fn game_pid(&self) -> Option<i32> {
        let text = fs::read_to_string(self.dir.join("game.pid")).ok()?;
        text.trim().parse().ok().filter(|pid: &i32| *pid > 1)
    }

    /// Name the save the game is to start again on. Written whole before it
    /// is in place: the wrapper reads it the moment the game is gone.
    pub fn request(&self, id: &str) -> std::io::Result<()> {
        if !valid_id(id) {
            return Err(std::io::Error::other(format!("not a save: {id}")));
        }
        let part = self.dir.join("restart.part");
        fs::write(&part, id)?;
        fs::rename(part, self.dir.join("restart"))
    }

    /// The saves the client listed into the session.
    pub fn lines(&self) -> Vec<SaveLine> {
        fs::read_to_string(self.lines_path())
            .map(|text| parse_lines(&text))
            .unwrap_or_default()
    }
}

/// `CLIENT saves list ENV --lines`, running into the session.
#[derive(Debug)]
pub struct Listing {
    child: Option<Child>,
    started: f64,
}

impl Listing {
    pub fn start(client: &str, attr: &str, session: &Session, now: f64) -> Self {
        let child = fs::File::create(session.lines_path()).ok().and_then(|out| {
            Command::new(client)
                .args(["saves", "list", attr, "--lines"])
                .stdin(Stdio::null())
                .stdout(out)
                .stderr(Stdio::null())
                .spawn()
                .ok()
        });
        Self { child, started: now }
    }

    /// None while the client is still at it; then how many saves it listed,
    /// or None inside when it failed or took too long.
    pub fn poll(&mut self, session: &Session, now: f64) -> Option<Option<usize>> {
        let Some(child) = self.child.as_mut() else {
            return Some(None);
        };
        match child.try_wait() {
            Ok(Some(status)) if status.success() => Some(Some(session.lines().len())),
            Ok(Some(_)) | Err(_) => Some(None),
            Ok(None) if now - self.started > LIST_SECONDS => {
                let _ = child.kill();
                let _ = child.wait();
                Some(None)
            }
            Ok(None) => None,
        }
    }
}

/// Every process under `root`, from (pid, parent) pairs: what stopping the
/// game alone has to reach, since an emulator may have started its own.
pub fn descendants(root: i32, processes: &[(i32, i32)]) -> Vec<i32> {
    let mut found = vec![root];
    let mut at = 0;
    while at < found.len() {
        let parent = found[at];
        let children: Vec<i32> = processes
            .iter()
            .filter(|&&(pid, ppid)| ppid == parent && !found.contains(&pid))
            .map(|&(pid, _)| pid)
            .collect();
        found.extend(children);
        at += 1;
    }
    found
}

/// The parent pid in a /proc/<pid>/stat line: the field after the state.
pub fn parent(line: &str) -> Option<i32> {
    let rest = &line[line.rfind(')')? + 1..];
    rest.split(' ').filter(|t| !t.is_empty()).nth(1)?.parse().ok()
}

fn processes() -> Vec<(i32, i32)> {
    let Ok(entries) = fs::read_dir("/proc") else {
        return Vec::new();
    };
    entries
        .filter_map(|entry| {
            let pid: i32 = entry.ok()?.file_name().to_str()?.parse().ok()?;
            let line = fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
            Some((pid, parent(&line)?))
        })
        .collect()
}

/// Stop the game and what it started, and nothing else: SIGTERM, a grace to
/// write its own saves in, then SIGKILL for whatever is left.
pub fn stop_tree(game: i32, grace_ms: u64, poll_ms: u64) {
    let tree = descendants(game, &processes());
    for &pid in tree.iter().rev() {
        // SAFETY: a signal to a pid; nothing is shared.
        unsafe { libc::kill(pid, libc::SIGTERM) };
    }
    let mut waited = 0;
    while waited < grace_ms && tree.iter().any(|&pid| crate::procstat::alive(pid)) {
        std::thread::sleep(std::time::Duration::from_millis(poll_ms.max(10)));
        waited += poll_ms.max(10);
    }
    for &pid in &tree {
        if crate::procstat::alive(pid) {
            // SAFETY: as above.
            unsafe { libc::kill(pid, libc::SIGKILL) };
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_clients_lines_are_read_as_saves() {
        let text = "remote:7\ttoday, 21:10\tdaniel-deck · saved to the service · what you have now\n\
                    local:2026-09-29T080000Z-0123456789ab.tar.zst\tSep 29, 08:00\tdaniel-desktop · archived on this machine\n";
        let lines = parse_lines(text);
        assert_eq!(lines.len(), 2);
        assert_eq!(lines[0].id, "remote:7");
        assert_eq!(lines[0].when, "today, 21:10");
        assert!(lines[1].detail.starts_with("daniel-desktop"));
    }

    #[test]
    fn a_line_whose_id_restore_would_refuse_is_not_a_save() {
        for id in [
            "remote:",
            "remote:x",
            "remote:-1",
            "local:../../etc/passwd",
            "local:2026-09-29T080000Z-0123456789ab.tar.zst.extra",
            "--force",
            "",
        ] {
            assert!(parse_lines(&format!("{id}\twhen\twhat")).is_empty(), "{id}");
        }
    }

    #[test]
    fn words_are_kept_to_what_a_line_on_screen_can_hold() {
        let lines = parse_lines(&format!("remote:1\ttoday\x1b[31m\t{}", "x".repeat(500)));
        assert_eq!(lines[0].when, "today[31m");
        assert_eq!(lines[0].detail.chars().count(), 80);
        let many: String = (1..=80).map(|n| format!("remote:{n}\tw\td\n")).collect();
        assert_eq!(parse_lines(&many).len(), LINES_MAX);
    }

    #[test]
    fn only_an_environment_of_one_games_own_is_offered() {
        assert!(dedicated("env-n64-usa_donkey_kong_64"));
        assert!(dedicated("env-gamecube-usa_pikmin_rev1"));
        assert!(!dedicated("env-n64"));
        assert!(!dedicated("env-snes"));
        assert!(!dedicated(""));
        assert!(!dedicated("n64-usa_x"));
    }

    #[test]
    fn a_session_says_the_game_and_takes_a_request() {
        let dir = std::env::temp_dir().join(format!("gotg-session-test-{}", std::process::id()));
        fs::create_dir_all(&dir).expect("set up by this test");
        let session = Session::at(&dir).expect("an absolute directory");
        assert_eq!(session.game_pid(), None, "no wrapper loop has said so");
        fs::write(dir.join("game.pid"), "4242\n").expect("set up by this test");
        assert_eq!(session.game_pid(), Some(4242));
        session.request("remote:3").expect("set up by this test");
        assert_eq!(
            fs::read_to_string(dir.join("restart")).expect("set up by this test"),
            "remote:3"
        );
        assert!(session.request("remote:3; rm -rf ~").is_err());
        assert!(Session::at(Path::new("relative/dir")).is_none());
        fs::remove_dir_all(dir).expect("set up by this test");
    }

    #[test]
    fn the_tree_under_the_game_is_found_and_nothing_beside_it() {
        // 10 is the game; 11 and 12 its children, 13 a grandchild; 20 a
        // sibling (the wrapper's other business) and 1 everything else.
        let processes = [(10, 5), (11, 10), (12, 10), (13, 11), (20, 5), (5, 1)];
        let mut tree = descendants(10, &processes);
        tree.sort_unstable();
        assert_eq!(tree, [10, 11, 12, 13]);
    }

    #[test]
    fn stopping_the_game_reaches_what_it_started_and_nothing_beside_it() {
        // A game that started a helper of its own, and a process beside it
        // that is not the game's -- the wrapper's other business.
        let mut game = Command::new("sh")
            .args(["-c", "sleep 30 & echo $! ; wait"])
            .stdout(Stdio::piped())
            .spawn()
            .expect("sh runs");
        let mut beside = Command::new("sleep").arg("30").spawn().expect("sleep runs");
        let mut helper = String::new();
        std::io::BufRead::read_line(
            &mut std::io::BufReader::new(game.stdout.take().expect("piped")),
            &mut helper,
        )
        .expect("the helper's pid");
        let helper: i32 = helper.trim().parse().expect("a pid");
        stop_tree(game.id() as i32, 2000, 20);
        let _ = game.wait();
        assert!(!crate::procstat::alive(helper), "the helper went with the game");
        assert!(crate::procstat::alive(beside.id() as i32), "and nothing else did");
        let _ = beside.kill();
        let _ = beside.wait();
    }

    #[test]
    fn a_stat_lines_parent_is_read_past_a_name_with_parentheses() {
        assert_eq!(parent("4242 (dolphin-emu) S 4200 4242 4242 0 -1"), Some(4200));
        assert_eq!(parent("77 (a) Z 1 1) S 9 77"), Some(9));
    }
}
