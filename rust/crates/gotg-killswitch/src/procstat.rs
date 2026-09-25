//! What /proc says about the process being guarded.
//!
//! Two questions, both of which the obvious call gets wrong. "Is it still
//! running?" is not kill(pid, 0): a game whose parent has not reaped it yet
//! is a zombie, and a zombie answers that exactly like a healthy process. "Is
//! it still the *same* process?" has no signal-based answer at all -- pids are
//! recycled, and a watcher that sits for hours can outlive one.

use std::fs;

/// Everything after the process name.
///
/// The name is parenthesised and is whatever the program was called -- it
/// can contain spaces and a close paren. So the fields are found from the
/// *last* one in the line rather than by counting from the left, which is the
/// difference between reading a process called ") Z 1 1 1" right and
/// believing whatever it decided to claim.
fn after_comm(line: &str) -> Option<&str> {
    line.rfind(')').map(|end| &line[end + 1..])
}

/// The state letter -- 'R', 'S', 'Z' -- if the line parses.
pub fn state(line: &str) -> Option<char> {
    after_comm(line)?.trim_start_matches(' ').chars().next()
}

/// The start time in clock ticks since boot: the one field that makes a pid
/// identify a process rather than a slot. None means "do not claim to know".
pub fn starttime(line: &str) -> Option<u64> {
    // starttime is field 22 and the first field here is the state (field 3),
    // so it is the twentieth token along.
    let token = after_comm(line)?.split(' ').filter(|t| !t.is_empty()).nth(19)?;
    if !token.starts_with(|c: char| c.is_ascii_digit()) {
        return None;
    }
    token.parse().ok()
}

fn read_stat(pid: i32) -> Option<String> {
    fs::read_to_string(format!("/proc/{pid}/stat"))
        .ok()
        .filter(|line| !line.is_empty())
}

/// Whether that pid is a process that has not exited. A pid we may not
/// signal counts as alive: it exists, and it is not ours to judge.
pub fn alive(pid: i32) -> bool {
    // SAFETY: signal 0 checks for existence and delivers nothing.
    let exists = unsafe { libc::kill(pid, 0) } == 0
        || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM);
    if !exists {
        return false;
    }
    match read_stat(pid) {
        Some(line) => state(&line) != Some('Z'),
        // No /proc to ask, but the signal said the pid exists. Believing the
        // signal is the conservative answer: it delays a report of the game
        // ending, where the other way round would kill on a guess.
        None => true,
    }
}

/// This pid's start time, for pinning an identity to it.
pub fn started(pid: i32) -> Option<u64> {
    starttime(&read_stat(pid)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_state_is_read_past_the_process_name() {
        assert_eq!(state("42 (ares) S 1 42 42 0 -1 4194304 100"), Some('S'));
        assert_eq!(state("42 (Cemu (Wii U)) R 1 42"), Some('R'), "a name with parens");
        assert_eq!(
            state("42 () Z 1 1 1) S 1 42"),
            Some('S'),
            "a name that spells a fake state"
        );
        assert_eq!(
            state("42 (ares) Z 1 42"),
            Some('Z'),
            "a zombie is seen as a zombie"
        );
        assert_eq!(state("nonsense"), None, "no name at all is no answer");
    }

    #[test]
    fn the_start_time_is_the_twenty_second_field() {
        let line = "42 (ares) S 1 42 42 0 -1 4194304 1 2 3 4 5 6 7 8 9 10 11 12 987654 13 14 15 16 17 18";
        assert_eq!(starttime(line), Some(987654));
        assert_eq!(starttime("42 (ares) S 1 42"), None, "a line that stops short");
        assert_eq!(starttime(""), None);
    }

    #[test]
    fn a_live_process_is_told_from_one_that_never_existed() {
        let me = i32::try_from(std::process::id()).unwrap_or(i32::MAX);
        assert!(alive(me));
        assert!(
            started(me).is_some_and(|t| t > 0),
            "and has a start time to be pinned by"
        );
        // Above the default pid_max, so it is nobody rather than somebody else.
        assert!(!alive(4_194_305));
        assert_eq!(started(4_194_305), None);
    }

    #[test]
    fn a_zombie_is_not_alive() {
        // The reason this module exists: kill(pid, 0) answers for a zombie
        // exactly as for a healthy process. A child that has exited and not
        // been waited for is one.
        let mut child = std::process::Command::new("true").spawn().expect("spawn true");
        let pid = i32::try_from(child.id()).unwrap_or(i32::MAX);
        for _ in 0..100 {
            if read_stat(pid).and_then(|line| state(&line)) == Some('Z') {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        assert!(!alive(pid), "a zombie is not a game still running");
        let _ = child.wait();
    }

    #[test]
    fn the_start_time_must_be_a_number_throughout() {
        let line = "42 (ares) S 1 42 42 0 -1 4194304 1 2 3 4 5 6 7 8 9 10 11 12 123abc 13 14";
        assert_eq!(starttime(line), None);
    }

    #[test]
    fn repeated_spaces_do_not_shift_the_fields() {
        let line = "42 (ares)  S  1 42 42 0 -1 4194304 1 2 3 4 5 6 7 8 9 10 11 12 987654 13 14";
        assert_eq!(starttime(line), Some(987654));
    }
}
