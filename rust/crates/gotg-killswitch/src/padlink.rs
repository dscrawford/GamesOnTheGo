//! The overlay's connection to padmap: one more client on its socket.
//!
//! Never blocks. A game runs with or without padmap, and an overlay waiting on
//! a socket is an exit chord that stops working, so the socket is read only
//! when it has something and connected only when it is due -- every couple of
//! seconds while it is not there, which picks up a daemon that starts (or
//! restarts) mid-game.

use std::collections::HashMap;
use std::io::{ErrorKind, Read};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};
use std::os::unix::net::UnixStream;
use std::path::PathBuf;

use crate::events;
use crate::pairing::Pairing;
use crate::rebind::Rebind;

/// Long enough for padmap's longest line (an `sdl_mapping` carries a whole
/// mapping string per pad); a line longer still is skipped whole.
pub const BUFFER: usize = 65536;

/// How long to wait before asking again for a socket that was not there.
const RETRY_SECONDS: f64 = 2.0;

/// Reads per pump. Bounded, because the kill chord is looked at between
/// pumps: a peer that never stops talking would otherwise hold the loop.
const READS_PER_PUMP: usize = 16;

#[derive(Debug)]
pub struct Link {
    path: Option<PathBuf>,
    stream: Option<UnixStream>,
    buffer: Vec<u8>,
    /// Inside a line too long to keep, until its newline.
    skipping: bool,
    next_try: f64,
    /// Each pad's drawing by its node, looked up once: padmap reads out a
    /// hold every ~58 ms, and the look-up reads sysfs in the loop that
    /// watches the exit chord. Forgotten on close, since a node's number is
    /// another device's once it has gone.
    icons: HashMap<String, u8>,
    resolve: fn(&str, &str) -> u8,
}

/// Nodes remembered at most; more than any room holds, and a bound for a
/// socket that names a new one on every line.
const ICONS_KEPT: usize = 64;

impl Link {
    /// `path` None or empty means padmap's own rule --
    /// $XDG_RUNTIME_DIR/padmap/padmap.sock -- and no link at all when there is
    /// no XDG_RUNTIME_DIR. padmap falls back to /tmp then, where any local
    /// user can put a socket first; the joining picture is not worth listening
    /// to a stranger for.
    pub fn new(path: Option<&str>) -> Self {
        let path = match path.filter(|path| !path.is_empty()) {
            Some(path) => Some(PathBuf::from(path)),
            None => std::env::var_os("XDG_RUNTIME_DIR")
                .filter(|dir| !dir.is_empty())
                .map(|dir| PathBuf::from(dir).join("padmap/padmap.sock")),
        };
        Self {
            path,
            stream: None,
            buffer: Vec::with_capacity(BUFFER),
            skipping: false,
            next_try: 0.0,
            icons: HashMap::new(),
            resolve: crate::icons::resolve,
        }
    }

    /// Where it connects, if anywhere.
    pub fn path(&self) -> Option<&PathBuf> {
        self.path.as_ref()
    }

    /// The descriptor to wait on, when connected.
    pub fn fd(&self) -> Option<RawFd> {
        self.stream.as_ref().map(AsRawFd::as_raw_fd)
    }

    pub fn close(&mut self) {
        self.stream = None;
        self.buffer.clear();
        self.skipping = false;
        self.icons.clear();
    }

    /// Connect if not connected and due. Cheap when it is neither.
    pub fn tick(&mut self, now: f64) {
        if self.stream.is_some() || now < self.next_try {
            return;
        }
        let Some(path) = &self.path else { return };
        self.next_try = now + RETRY_SECONDS;
        self.stream = connect(path);
    }

    /// Read what has arrived and apply every complete line to `pairing` and
    /// `rebind`. Returns how many events were applied; a closed or broken
    /// socket is dropped and retried later.
    pub fn pump(&mut self, pairing: &mut Pairing, rebind: &mut Rebind, now: f64) -> usize {
        let mut applied = 0;
        let mut chunk = [0u8; 8192];
        for _ in 0..READS_PER_PUMP {
            let Some(mut stream) = self.stream.as_ref() else {
                break;
            };
            match stream.read(&mut chunk) {
                Ok(0) => {
                    self.close();
                    break;
                }
                Ok(got) => applied += self.feed(&chunk[..got], pairing, rebind, now),
                Err(error) if error.kind() == ErrorKind::WouldBlock => break,
                Err(error) if error.kind() == ErrorKind::Interrupted => {}
                Err(_) => {
                    self.close();
                    break;
                }
            }
        }
        applied
    }

    /// Apply every complete line in `bytes` and what was held before them.
    /// Split out so a test can feed bytes without a socket.
    pub fn feed(&mut self, mut bytes: &[u8], pairing: &mut Pairing, rebind: &mut Rebind, now: f64) -> usize {
        let mut applied = 0;
        while !bytes.is_empty() {
            let take = bytes.len().min(BUFFER - self.buffer.len());
            self.buffer.extend_from_slice(&bytes[..take]);
            bytes = &bytes[take..];
            applied += self.drain(pairing, rebind, now);
        }
        applied
    }

    fn drain(&mut self, pairing: &mut Pairing, rebind: &mut Rebind, now: f64) -> usize {
        let (icons, resolve) = (&mut self.icons, self.resolve);
        let mut icon_of = |node: &str, name: &str| -> u8 {
            // A keyboard seat names no node, and needs no look-up to name.
            if node.is_empty() {
                return resolve(node, name);
            }
            if let Some(&icon) = icons.get(node) {
                return icon;
            }
            if icons.len() >= ICONS_KEPT {
                icons.clear();
            }
            let icon = resolve(node, name);
            icons.insert(node.to_owned(), icon);
            icon
        };
        let mut applied = 0;
        let mut start = 0;
        while let Some(at) = self.buffer[start..].iter().position(|&b| b == b'\n') {
            let line = &self.buffer[start..start + at];
            if self.skipping {
                self.skipping = false;
            } else if !line.is_empty()
                && let Some(event) = events::parse(line)
            {
                events::apply(&event, pairing, now, &mut icon_of);
                rebind.apply(&event, now);
                applied += 1;
            }
            start += at + 1;
        }
        // Keep the partial line for the next read.
        self.buffer.drain(..start);
        if self.buffer.len() == BUFFER {
            // A whole buffer and no newline: this line is too long to keep.
            // Skip to its end rather than lose the framing of every line after.
            self.buffer.clear();
            self.skipping = true;
        }
        applied
    }

    /// One line to padmap: a command. False when there is no daemon to send
    /// it to, or it would not take the line now -- a rebind the bar then
    /// gives up on (`rebind::ASK_SECONDS`), never a loop that waits.
    pub fn send(&mut self, line: &str) -> bool {
        use std::io::Write;
        let Some(mut stream) = self.stream.as_ref() else {
            return false;
        };
        let mut bytes = line.as_bytes().to_vec();
        bytes.push(b'\n');
        match stream.write(&bytes) {
            Ok(wrote) if wrote == bytes.len() => true,
            // Half a command is a stranger's line to padmap; start over.
            Ok(_) => {
                self.close();
                false
            }
            Err(error) if error.kind() == ErrorKind::WouldBlock => false,
            Err(_) => {
                self.close();
                false
            }
        }
    }

    #[cfg(test)]
    fn with_stream(stream: UnixStream) -> Self {
        Self {
            stream: Some(stream),
            ..Self::new(Some("/nonexistent"))
        }
    }
}

/// A non-blocking connection to `path`, if something of this user's is
/// listening there.
fn connect(path: &std::path::Path) -> Option<UnixStream> {
    use std::os::unix::ffi::OsStrExt;
    let bytes = path.as_os_str().as_bytes();
    // SAFETY: sockaddr_un is plain data; zeroed is a valid empty address.
    let mut address: libc::sockaddr_un = unsafe { std::mem::zeroed() };
    if bytes.len() >= address.sun_path.len() {
        return None;
    }
    address.sun_family = libc::AF_UNIX as libc::sa_family_t;
    for (slot, &byte) in address.sun_path.iter_mut().zip(bytes) {
        *slot = byte as libc::c_char;
    }
    // Non-blocking from the start. A unix socket does not always answer at
    // once: a listener whose backlog is full -- a daemon that has stopped
    // accepting -- holds a blocking connect until it accepts, and this runs
    // in the loop that watches the kill chord. A connect that cannot finish
    // now is tried again on a later tick.
    // SAFETY: plain socket(2); the descriptor is owned at once.
    let raw = unsafe {
        libc::socket(
            libc::AF_UNIX,
            libc::SOCK_STREAM | libc::SOCK_CLOEXEC | libc::SOCK_NONBLOCK,
            0,
        )
    };
    if raw < 0 {
        return None;
    }
    // SAFETY: `raw` is a fresh descriptor nothing else owns.
    let socket = unsafe { OwnedFd::from_raw_fd(raw) };
    let length = std::mem::size_of::<libc::sockaddr_un>() as libc::socklen_t;
    // SAFETY: the address is initialised and `length` is its size.
    if unsafe { libc::connect(raw, (&raw const address).cast(), length) } != 0 {
        return None;
    }
    // padmap is this user's own daemon; a socket anyone else answers is not
    // padmap, whatever it is called.
    // SAFETY: ucred is plain data, and getsockopt writes at most `size` bytes.
    let mut peer: libc::ucred = unsafe { std::mem::zeroed() };
    let mut size = std::mem::size_of::<libc::ucred>() as libc::socklen_t;
    let asked = unsafe {
        libc::getsockopt(
            raw,
            libc::SOL_SOCKET,
            libc::SO_PEERCRED,
            (&raw mut peer).cast(),
            &mut size,
        )
    };
    // SAFETY: getuid cannot fail.
    if asked != 0 || peer.uid != unsafe { libc::getuid() } {
        return None;
    }
    Some(UnixStream::from(socket))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::os::unix::net::UnixListener;

    fn pairing() -> Pairing {
        Pairing::new(1.5)
    }

    fn feed_text(text: &str) -> usize {
        Link::new(Some("/nonexistent")).feed(text.as_bytes(), &mut pairing(), &mut Rebind::default(), 10.0)
    }

    #[test]
    fn lines_split_across_reads_are_joined() {
        let mut link = Link::new(Some("/nonexistent"));
        let mut p = pairing();
        let first = r#"{"event":"progress","frac":0.2,"node":"/dev/in"#;
        let rest = "put/event9\",\"player\":1}\n";
        assert_eq!(
            link.feed(first.as_bytes(), &mut p, &mut Rebind::default(), 10.0),
            0,
            "half a line is kept, not applied"
        );
        assert_eq!(
            link.feed(rest.as_bytes(), &mut p, &mut Rebind::default(), 10.0),
            1,
            "and applied once the rest arrives"
        );
        assert_eq!(p.now(10.0)[0].key, "/dev/input/event9");
    }

    #[test]
    fn a_line_too_long_is_skipped_whole() {
        let mut link = Link::new(Some("/nonexistent"));
        let mut p = pairing();
        link.feed(&vec![b'x'; BUFFER + 100], &mut p, &mut Rebind::default(), 10.0);
        let after = "\n{\"event\":\"progress\",\"frac\":0.3,\"node\":\"n\"}\n";
        assert_eq!(
            link.feed(after.as_bytes(), &mut p, &mut Rebind::default(), 10.0),
            1,
            "the line after it still parses"
        );
    }

    #[test]
    fn a_buffer_full_with_no_newline_is_dropped_and_recovered_from() {
        let mut link = Link::new(Some("/nonexistent"));
        let mut p = pairing();
        assert_eq!(
            link.feed(&vec![b'x'; BUFFER], &mut p, &mut Rebind::default(), 10.0),
            0
        );
        let after = "\n{\"event\":\"progress\",\"frac\":0.3,\"node\":\"n\"}\n";
        assert_eq!(
            link.feed(after.as_bytes(), &mut p, &mut Rebind::default(), 10.0),
            1
        );
    }

    #[test]
    fn a_line_exactly_the_buffer_size_still_parses() {
        let (prefix, suffix) = ("{\"event\":\"progress\",\"frac\":0.5,\"node\":\"", "\"}\n");
        let line = format!(
            "{prefix}{}{suffix}",
            "a".repeat(BUFFER - prefix.len() - suffix.len())
        );
        assert_eq!(line.len(), BUFFER);
        assert_eq!(
            Link::new(Some("/nonexistent")).feed(
                line.as_bytes(),
                &mut pairing(),
                &mut Rebind::default(),
                10.0
            ),
            1
        );
    }

    #[test]
    fn line_endings_and_blank_lines() {
        assert_eq!(
            feed_text("{\"event\":\"progress\",\"frac\":0.4,\"node\":\"n\"}\r\n"),
            1,
            "CRLF"
        );
        let three = "{\"event\":\"progress\",\"frac\":0.1,\"node\":\"a\"}\n\
                     {\"event\":\"progress\",\"frac\":0.2,\"node\":\"b\"}\n\
                     {\"event\":\"progress\",\"frac\":0.3,\"node\":\"c\"}\n";
        assert_eq!(feed_text(three), 3, "three lines in one read all apply");
        assert_eq!(
            feed_text("\n\n{\"event\":\"progress\",\"frac\":0.1,\"node\":\"a\"}\n\n"),
            1,
            "blank lines"
        );
    }

    #[test]
    fn a_socket_is_read_and_its_closing_noticed() {
        let (ours, mut theirs) = UnixStream::pair().expect("socketpair");
        let mut link = Link::with_stream(ours);
        theirs
            .write_all(b"{\"event\":\"claim\",\"player\":3}\n")
            .expect("write");
        // Blocking in the test: one read gets the line, the close ends it.
        drop(theirs);
        assert_eq!(
            link.pump(&mut pairing(), &mut Rebind::default(), 10.0),
            1,
            "a claim comes in over the socket"
        );
        assert_eq!(link.fd(), None, "and the other end closing drops the connection");
    }

    #[test]
    fn tick_connects_to_a_listening_socket() {
        let path = std::env::temp_dir().join(format!("gotg-overlay-test-{}.sock", std::process::id()));
        let _ = std::fs::remove_file(&path);
        let listener = UnixListener::bind(&path).expect("a listening socket for the test");
        let mut link = Link::new(path.to_str());
        link.tick(0.0);
        assert!(
            link.fd().is_some(),
            "tick connects to a socket that is listening, as this user"
        );
        link.close();
        assert_eq!(link.fd(), None, "and close drops it");
        drop(listener);
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn tick_on_nothing_or_nonsense_stays_disconnected() {
        let mut missing = Link::new(Some("/nonexistent-gotg-overlay-test.sock"));
        missing.tick(0.0);
        missing.tick(0.01);
        let mut overlong = Link::new(Some(&"a".repeat(1023)));
        overlong.tick(0.0);
        assert!(missing.fd().is_none() && overlong.fd().is_none());
    }

    #[test]
    fn no_runtime_directory_is_no_link() {
        // Only this test touches the variable, and it puts it back.
        let saved = std::env::var_os("XDG_RUNTIME_DIR");
        // SAFETY: no other thread in this test binary reads XDG_RUNTIME_DIR.
        unsafe { std::env::remove_var("XDG_RUNTIME_DIR") };
        let mut link = Link::new(None);
        link.tick(10.0);
        if let Some(saved) = saved {
            // SAFETY: as above.
            unsafe { std::env::set_var("XDG_RUNTIME_DIR", saved) };
        }
        assert!(
            link.path().is_none() && link.fd().is_none(),
            "nothing in /tmp is trusted"
        );
    }

    #[test]
    fn an_idle_socket_is_neither_waited_on_nor_dropped() {
        let (ours, _theirs) = UnixStream::pair().expect("socketpair");
        ours.set_nonblocking(true).expect("nonblocking");
        let mut link = Link::with_stream(ours);
        assert_eq!(link.pump(&mut pairing(), &mut Rebind::default(), 10.0), 0);
        assert!(link.fd().is_some(), "nothing to say is not a closed connection");
    }

    static RESOLVED: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);

    fn counted(_: &str, _: &str) -> u8 {
        RESOLVED.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        3
    }

    #[test]
    fn a_pad_s_drawing_is_looked_up_once_while_it_holds() {
        // padmap sends a reading every ~58 ms; the device behind a node does
        // not change between them, and the look-up reads sysfs in the loop
        // that watches the exit chord.
        let mut link = Link {
            resolve: counted,
            ..Link::new(Some("/nonexistent"))
        };
        let mut p = pairing();
        let line = |frac: f64| {
            format!("{{\"event\":\"progress\",\"frac\":{frac},\"node\":\"/dev/input/event9\"}}\n")
        };
        let before = RESOLVED.load(std::sync::atomic::Ordering::SeqCst);
        for step in 1..=10 {
            link.feed(
                line(f64::from(step) / 20.0).as_bytes(),
                &mut p,
                &mut Rebind::default(),
                10.0,
            );
        }
        assert_eq!(RESOLVED.load(std::sync::atomic::Ordering::SeqCst) - before, 1);
        assert_eq!(p.now(10.0)[0].icon, 3);
        link.close();
        link.feed(line(0.9).as_bytes(), &mut p, &mut Rebind::default(), 10.0);
        assert_eq!(
            RESOLVED.load(std::sync::atomic::Ordering::SeqCst) - before,
            2,
            "a new connection asks again"
        );
    }

    #[test]
    fn a_keyboard_joining_is_drawn_as_the_keyboard() {
        // padmap names a keyboard's hold and seat but no node: the bar keys it
        // by name, draws the picker's keyboard drawing filling in, and takes
        // it down when the seat is claimed.
        let mut link = Link::new(Some("/nonexistent"));
        let mut p = pairing();
        let hold = "{\"event\":\"progress\",\"frac\":0.4,\"name\":\"Keyboard\",\"node\":\"\",\"player\":2}\n";
        link.feed(hold.as_bytes(), &mut p, &mut Rebind::default(), 10.0);
        let holds = p.now(10.0);
        assert_eq!(holds.len(), 1);
        assert_eq!(crate::icons::NAMES[usize::from(holds[0].icon)], "keyboard-mouse");
        let claim =
            "{\"event\":\"claim\",\"player\":2,\"name\":\"Keyboard\",\"node\":\"\",\"icon\":\"keyboard\"}\n";
        link.feed(claim.as_bytes(), &mut p, &mut Rebind::default(), 10.1);
        assert!(p.now(10.1).is_empty(), "the hold that took the seat is over");
        let [(player, icon)] = p.joined(10.1)[..] else {
            panic!("one seat taken")
        };
        assert_eq!(
            (player, crate::icons::NAMES[usize::from(icon)]),
            (2, "keyboard-mouse")
        );
    }

    #[test]
    fn a_command_goes_to_padmap_as_one_line_and_the_wizard_comes_back_to_the_rebind() {
        let (ours, mut daemon) = UnixStream::pair().expect("a socket pair");
        ours.set_nonblocking(true).expect("non-blocking");
        daemon.set_nonblocking(true).expect("non-blocking");
        let mut link = Link::with_stream(ours);
        let mut rebind = Rebind::default();
        let line = rebind.start(1, "n64", "console:n64", 10.0).expect("a command");
        assert!(link.send(&line));
        let mut sent = String::new();
        let _ = std::io::Read::read_to_string(&mut daemon, &mut sent);
        assert_eq!(sent, format!("{line}\n"));
        daemon
            .write_all(b"{\"event\":\"mapping\",\"player\":1,\"control\":\"b\",\"index\":1,\"total\":14}\n")
            .expect("padmap writes");
        link.pump(&mut pairing(), &mut rebind, 10.2);
        assert_eq!(rebind.view(10.2).map(|v| v.control), Some("b".into()));
    }

    #[test]
    fn with_no_daemon_a_command_goes_nowhere_and_says_so() {
        assert!(!Link::new(Some("/nonexistent")).send("{}"));
    }
}
