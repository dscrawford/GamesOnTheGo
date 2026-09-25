//! The painter: this same program, run as `gotg-killswitch --paint`, drawing
//! the frames it is sent until its pipe closes. See `frame` for why it is a
//! process of its own.
//!
//! The kill switch's side never waits on it. Frames go down a non-blocking
//! pipe and a full one drops them; a painter that dies is started again after
//! a pause that grows each time; one that is told to go is reaped on later
//! ticks and killed if it has not gone by then.

use std::io::{ErrorKind, Read, Write};
use std::os::fd::AsRawFd;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::time::Duration;

use crate::frame::{self, Frame};
use crate::overlay::Overlay;
use crate::scene::{self, Drawing, Scene};

/// After a painter that would not start or went by itself: two seconds, then
/// four, up to half a minute. A machine with no display would otherwise start
/// one every couple of seconds for as long as somebody was joining.
const RETRY_FIRST: f64 = 2.0;
const RETRY_MOST: f64 = 30.0;

/// How long a painter gets to go after its pipe closes, before it is killed.
const GRACE_SECONDS: f64 = 0.5;

fn nonblocking(fd: &impl AsRawFd) {
    let fd = fd.as_raw_fd();
    // SAFETY: fcntl on a descriptor we own, with flags it reported.
    unsafe {
        let flags = libc::fcntl(fd, libc::F_GETFL);
        if flags >= 0 {
            libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK);
        }
    }
}

/// The kill switch's handle on its painter.
#[derive(Debug, Default)]
pub struct Painter {
    child: Option<Child>,
    /// The write end of its pipe; None when told to go, or when there is none.
    pipe: Option<ChildStdin>,
    /// After a failed start, not before this.
    retry_at: f64,
    /// Told to go: killed if still here at this time.
    reap_by: Option<f64>,
    /// Painters in a row that went by themselves.
    failures: u32,
    /// So an unchanged picture is not sent again: a joined seat's badge sits
    /// still for a second and a half, and the painter draws only what is new.
    last_sent: Option<[u8; frame::SIZE]>,
}

impl Painter {
    /// Whether a painter has its pipe open.
    pub fn open(&self) -> bool {
        self.pipe.is_some()
    }

    /// Gone without being asked: wait before trying again, longer each time.
    fn lost(&mut self, now: f64) {
        self.pipe = None;
        if self.child.is_some() {
            self.reap_by = Some(now);
        }
        let wait = (RETRY_FIRST * 2f64.powi(self.failures.min(16) as i32)).min(RETRY_MOST);
        self.retry_at = now + wait;
        self.failures += 1;
    }

    /// Start one if there is none and one is due: this binary, again.
    pub fn ensure(&mut self, now: f64) {
        self.ensure_with(now, || {
            Command::new("/proc/self/exe")
                .arg("--paint")
                .stdin(Stdio::piped())
                .spawn()
        });
    }

    /// `ensure`, told how to spawn: where a test stands a cheap process in
    /// for the painter, with no display involved.
    fn ensure_with(&mut self, now: f64, spawn: impl FnOnce() -> std::io::Result<Child>) {
        // One painter at a time: a new one waits until the last is reaped.
        if self.child.is_some() || now < self.retry_at {
            return;
        }
        match spawn() {
            Ok(mut child) => {
                self.pipe = child.stdin.take();
                if let Some(pipe) = &self.pipe {
                    nonblocking(pipe);
                }
                self.child = Some(child);
                self.last_sent = None;
            }
            Err(_) => self.lost(now),
        }
    }

    /// Send a frame unless it is the one last sent. Never waits.
    pub fn send(&mut self, frame: &Frame, now: f64) {
        let Some(pipe) = &mut self.pipe else { return };
        let bytes = frame.encode();
        if self.last_sent == Some(bytes) {
            return;
        }
        // Smaller than PIPE_BUF, so it goes whole or not at all. A painter
        // that has exited shows up as EPIPE (Rust ignores SIGPIPE).
        match pipe.write(&bytes) {
            Ok(n) if n == bytes.len() => self.last_sent = Some(bytes),
            Err(error) if error.kind() == ErrorKind::WouldBlock => {} // behind: this one is dropped
            _ => self.lost(now),
        }
    }

    /// Tell it to go -- close its pipe -- without waiting. `tick` reaps it.
    pub fn release(&mut self, now: f64) {
        self.pipe = None;
        if self.child.is_some() && self.reap_by.is_none() {
            self.reap_by = Some(now + GRACE_SECONDS);
        }
        // It went because it was asked to: whatever failed before is behind us.
        self.failures = 0;
    }

    /// Reap a painter that was told to go, killing it past its time. Cheap.
    pub fn tick(&mut self, now: f64) {
        if self.pipe.is_some() {
            return;
        }
        let Some(child) = &mut self.child else { return };
        if matches!(child.try_wait(), Ok(Some(_))) {
            self.child = None;
            self.reap_by = None;
        } else if self.reap_by.is_some_and(|by| now >= by) {
            // Stuck in a compositor call, most likely. Whatever it was waiting
            // on is not worth keeping a process for.
            let _ = child.kill();
            let _ = child.wait();
            self.child = None;
            self.reap_by = None;
        }
    }

    /// Tell it to go and wait for it: for the way out of the program, where
    /// there is no later pass to reap on.
    pub fn close(&mut self) {
        self.release(0.0);
        let Some(mut child) = self.child.take() else {
            return;
        };
        for _ in 0..50 {
            if matches!(child.try_wait(), Ok(Some(_))) {
                return;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        let _ = child.kill();
        let _ = child.wait();
    }
}

/// Read what has arrived and keep the newest whole, valid frame. False when
/// the pipe has closed: the bar is up and the painter's work is done.
fn newest(mut source: impl Read, pending: &mut Vec<u8>, latest: &mut Option<Frame>) -> bool {
    let mut chunk = [0u8; frame::SIZE * 8];
    loop {
        match source.read(&mut chunk) {
            Ok(0) => return false,
            Ok(got) => pending.extend_from_slice(&chunk[..got]),
            Err(error) if error.kind() == ErrorKind::Interrupted => continue,
            Err(error) => return error.kind() == ErrorKind::WouldBlock,
        }
        // Frames arrive whole, but a read can end mid-frame when several are
        // waiting; the part-frame stays for the next read. Only the newest
        // whole one matters -- an older one is a picture of the past.
        let whole = pending.len() / frame::SIZE;
        if whole == 0 {
            continue;
        }
        let at = (whole - 1) * frame::SIZE;
        if let Ok(bytes) = <[u8; frame::SIZE]>::try_from(&pending[at..at + frame::SIZE])
            && let Some(frame) = Frame::decode(&bytes)
        {
            *latest = Some(frame);
        }
        pending.drain(..whole * frame::SIZE);
    }
}

/// The painter's own side: open a window, draw every new frame that arrives
/// on stdin, exit when stdin closes. The process's exit status.
pub fn paint() -> i32 {
    let mut stdin = std::io::stdin();
    nonblocking(&stdin);
    let mut overlay = match Overlay::open() {
        Ok(overlay) => overlay,
        Err(why) => {
            eprintln!("gotg-killswitch: {why}");
            return 1;
        }
    };
    let (width, bar) = overlay.size();
    // What QA reads to find the bar in a recording: the size it actually drew.
    eprintln!(
        "gotg-killswitch: overlay up ({}, {}) width={width} bar={bar:.0}",
        overlay.kind(),
        overlay.renderer_name()
    );

    let mut drawing = Drawing::default();
    let mut pending = Vec::with_capacity(frame::SIZE * 8);
    loop {
        // Asleep until the kill switch sends something new: it sends only
        // when the picture changed, so an unchanged bar costs no redraws.
        let mut wait = libc::pollfd {
            fd: stdin.as_raw_fd(),
            events: libc::POLLIN,
            revents: 0,
        };
        // SAFETY: one pollfd, owned here, for the length of the call.
        unsafe { libc::poll(&mut wait, 1, -1) };
        let mut latest = None;
        if !newest(&mut stdin, &mut pending, &mut latest) {
            break;
        }
        let Some(frame) = latest else { continue };
        let (width, bar_height) = overlay.size();
        let scene = Scene {
            width,
            bar_height,
            position: f64::from(frame.position),
            fractions: frame.hold_fraction(),
            players: frame.hold_player(),
            hold_icons: frame.hold_icon(),
            joined: frame.joined(),
            joined_icons: frame.joined_icon(),
            exit_progress: f64::from(frame.exit_progress),
        };
        scene::build(&scene, &mut drawing);
        overlay.draw(&drawing);
        // Where presenting does not wait for the panel, a frame's worth, so a
        // flood of frames cannot turn into a spin.
        if !overlay.vsync() {
            std::thread::sleep(Duration::from_millis(16));
        }
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pairing::Hold;
    use std::cell::Cell;

    fn frame(position: f64) -> Frame {
        Frame::pack(position, 0.0, &[], &[])
    }

    /// A pipe with these bytes waiting and nothing more yet: it would block
    /// after them, where a slice would read as closed.
    struct Waiting(std::io::Cursor<Vec<u8>>);

    impl Read for Waiting {
        fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
            match self.0.read(buf)? {
                0 => Err(ErrorKind::WouldBlock.into()),
                got => Ok(got),
            }
        }
    }

    fn waiting(bytes: &[u8]) -> Waiting {
        Waiting(std::io::Cursor::new(bytes.to_vec()))
    }

    fn cat() -> std::io::Result<Child> {
        Command::new("cat")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
    }

    #[test]
    fn a_painter_never_started_is_harmless() {
        let mut p = Painter::default();
        p.send(&frame(0.5), 0.0);
        p.tick(0.0);
        p.release(0.0);
        p.close();
        assert!(!p.open());
    }

    #[test]
    fn one_painter_at_a_time() {
        let mut p = Painter::default();
        let spawns = Cell::new(0);
        let spawn = || {
            spawns.set(spawns.get() + 1);
            cat()
        };
        p.ensure_with(0.0, spawn);
        p.ensure_with(0.1, spawn);
        assert!(p.open());
        assert_eq!(spawns.get(), 1, "one already running is not started again");
        p.close();
    }

    #[test]
    fn a_painter_that_keeps_failing_is_asked_less_and_less_often() {
        let mut p = Painter::default();
        let waits: Vec<f64> = (0..6)
            .map(|_| {
                let before = p.retry_at;
                p.lost(before);
                p.retry_at - before
            })
            .collect();
        assert_eq!(waits, [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]);
    }

    #[test]
    fn a_spawn_that_fails_waits_before_trying_again() {
        let mut p = Painter::default();
        p.ensure_with(0.0, || Err(std::io::Error::other("no display here")));
        assert!(!p.open());
        let tried = Cell::new(false);
        p.ensure_with(1.0, || {
            tried.set(true);
            cat()
        });
        assert!(!tried.get(), "not again inside the back-off");
    }

    #[test]
    fn an_unchanged_frame_is_sent_once_and_a_change_goes_through() {
        let mut p = Painter::default();
        p.ensure_with(0.0, cat);
        let mut out = p
            .child
            .as_mut()
            .and_then(|child| child.stdout.take())
            .expect("cat's stdout");
        p.send(&frame(0.5), 0.0);
        p.send(&frame(0.5), 0.1);
        p.send(&frame(0.6), 0.2);
        p.close();
        let mut bytes = Vec::new();
        out.read_to_end(&mut bytes).expect("cat's output");
        assert_eq!(
            bytes.len(),
            frame::SIZE * 2,
            "the repeat cost nothing on the wire"
        );
    }

    #[test]
    fn a_painter_that_went_by_itself_is_noticed_on_the_next_send() {
        let mut p = Painter::default();
        p.ensure_with(0.0, || Command::new("true").stdin(Stdio::piped()).spawn());
        std::thread::sleep(Duration::from_millis(100));
        for i in 0..20 {
            p.send(&frame(f64::from(i) / 20.0), 1.0);
            if !p.open() {
                break;
            }
        }
        assert!(!p.open(), "EPIPE is a painter lost, not a frame dropped");
        p.tick(1.0);
        assert!(p.child.is_none(), "and it is reaped");
    }

    #[test]
    fn a_painter_that_will_not_go_is_killed_after_its_grace() {
        let mut p = Painter::default();
        p.ensure_with(0.0, || {
            Command::new("sleep").arg("30").stdin(Stdio::piped()).spawn()
        });
        p.release(0.0);
        p.tick(GRACE_SECONDS - 0.05);
        assert!(p.child.is_some(), "still inside its grace");
        p.tick(GRACE_SECONDS + 0.01);
        assert!(p.child.is_none(), "killed once it has passed");
    }

    #[test]
    fn close_does_not_hang_on_a_painter_that_will_not_go() {
        let mut p = Painter::default();
        p.ensure_with(0.0, || {
            Command::new("sleep").arg("30").stdin(Stdio::piped()).spawn()
        });
        let started = std::time::Instant::now();
        p.close();
        assert!(started.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn only_the_newest_whole_frame_is_drawn() {
        let hold = Hold {
            fraction: 0.5,
            player: 2,
            ..Hold::default()
        };
        let (old, new) = (
            Frame::pack(0.2, 0.0, &[], &[]),
            Frame::pack(0.9, 0.0, &[hold], &[(1, 0)]),
        );
        let mut bytes = old.encode().to_vec();
        bytes.extend_from_slice(&new.encode());
        bytes.extend_from_slice(&old.encode()[..10]); // the start of one still arriving
        let (mut pending, mut latest) = (Vec::new(), None);
        assert!(newest(waiting(&bytes), &mut pending, &mut latest), "an open pipe");
        assert_eq!(
            latest,
            Some(new),
            "the older whole frame is a picture of the past"
        );
        assert_eq!(pending.len(), 10, "the part-frame waits for the rest");
    }

    #[test]
    fn a_torn_or_foreign_frame_is_not_drawn() {
        let mut bytes = frame(0.5).encode();
        bytes[0] ^= 0xff;
        let (mut pending, mut latest) = (Vec::new(), None);
        assert!(newest(waiting(&bytes), &mut pending, &mut latest));
        assert_eq!(latest, None);
    }

    #[test]
    fn a_closed_pipe_ends_the_painter() {
        let (mut pending, mut latest) = (Vec::new(), None);
        assert!(!newest(std::io::empty(), &mut pending, &mut latest));
    }
}
