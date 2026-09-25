//! gotg-killswitch — hold both shoulders and Start, and the game stops.
//!
//! Emulated games have no Quit menu that a controller can reach. A Switch
//! title wants the Home button, an N64 ROM wants Alt-F4 on a keyboard that is
//! not in the room, and a game that has hung wants neither — so the way out
//! of a session on the couch was "get up and find a keyboard". This is the
//! way out: one chord, the same on every pad, that no game uses for anything.
//!
//!   both shoulders (or both triggers) + Start, held for three seconds
//!
//! It watches rather than intercepts. SDL reads controllers through evdev,
//! and several processes can read one device at once, so this sees the pad
//! without taking anything from the emulator — which is what makes it work
//! with every emulator rather than with the ones we could patch. It asks SDL
//! because the emulators ask SDL: "the left shoulder" is a per-model fact.
//!
//! It also draws the bar over the game (see `painter`), for the exit hold and
//! for a pad holding to join padmap.

use std::ffi::CStr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use gotg_killswitch::bar::Bar;
use gotg_killswitch::frame::Frame;
use gotg_killswitch::killswitch::{Input, Pad};
use gotg_killswitch::padlink::Link;
use gotg_killswitch::painter::{self, Painter};
use gotg_killswitch::pairing::Pairing;
use gotg_killswitch::procstat;
use sdl3_sys::everything::*;

const DEFAULT_HOLD_MS: u64 = 3000;
/// How long the game gets to put itself away — write its save, flush its
/// config — before it is killed outright. A hung emulator is the reason
/// somebody reached for this, so the wait is bounded.
const DEFAULT_GRACE_MS: u64 = 5000;
/// Ten times a second. The hold is three seconds, so this is finer than the
/// feature can notice — and on a handheld the cost of this loop is not the
/// work but the wakeup, every one of which keeps a core out of deeper idle.
const DEFAULT_POLL_MS: u64 = 100;
/// The longest a quiet loop waits between looks at the pads.
const POLL_MS_MAX: u64 = 1000;
/// How long the finished ring stays up before the game goes, so the last
/// thing seen is the switch firing rather than the picture vanishing.
const LINGER_MS: u64 = 400;
/// Quick enough not to be in the way, slow enough to read as arriving.
const SLIDE_SECONDS: f64 = 0.22;
/// How often a frame is worked out while something on the bar moves. The
/// painter draws only what is new, so a bar standing still costs nothing.
const FRAME_MS: u64 = 16;
/// Past halfway is "pulled". A trigger's rest is not always a clean zero, and
/// nobody holds a trigger at 40% for three seconds by accident.
const TRIGGER_ON: i16 = 16384;

static STOP: AtomicBool = AtomicBool::new(false);

extern "C" fn on_signal(_: libc::c_int) {
    STOP.store(true, Ordering::Relaxed);
}

const USAGE: &str =
    "usage: gotg-killswitch --pid <pid> [--hold-ms N] [--grace-ms N] [--poll-ms N] [--quiet] [--no-overlay]

Watches every controller SDL can see. When both shoulders (or both
triggers) and Start are held together for the hold time, the process
is asked to stop, and killed if it will not. Exits on its own when
that process is gone.

A bar comes down over the game while the hold runs, and while a
controller is holding a button to join padmap -- unless --no-overlay
says otherwise.
";

#[derive(Debug)]
struct Options {
    pid: i32,
    hold_ms: u64,
    grace_ms: u64,
    poll_ms: u64,
    quiet: bool,
    draw: bool,
}

/// Digits only. A leading minus is refused rather than wrapped: "--hold-ms
/// -1" parsed as 584 million years would be a kill switch reported as armed
/// that can never fire.
fn number(text: Option<&String>) -> Option<u64> {
    let text = text?;
    if !text.starts_with(|c: char| c.is_ascii_digit()) {
        return None;
    }
    text.parse().ok()
}

fn parse(args: &[String]) -> Result<Options, String> {
    let mut options = Options {
        pid: 0,
        hold_ms: DEFAULT_HOLD_MS,
        grace_ms: DEFAULT_GRACE_MS,
        poll_ms: DEFAULT_POLL_MS,
        quiet: false,
        draw: true,
    };
    let mut target = 0u64;
    let mut args = args.iter();
    while let Some(arg) = args.next() {
        let slot = match arg.as_str() {
            "--quiet" => {
                options.quiet = true;
                continue;
            }
            "--no-overlay" => {
                options.draw = false;
                continue;
            }
            "--pid" => &mut target,
            "--hold-ms" => &mut options.hold_ms,
            "--grace-ms" => &mut options.grace_ms,
            "--poll-ms" => &mut options.poll_ms,
            _ => return Err(format!("bad argument: {arg}")),
        };
        *slot = number(args.next()).ok_or_else(|| format!("bad argument: {arg}"))?;
    }
    // Not pid 1, and not 0: a group kill of either is a signal to everything
    // this user owns rather than to one game.
    options.pid = i32::try_from(target)
        .ok()
        .filter(|&pid| pid >= 2)
        .ok_or("--pid is required, and is a process to watch")?;
    if options.poll_ms == 0 {
        options.poll_ms = DEFAULT_POLL_MS;
    }
    options.poll_ms = options.poll_ms.min(POLL_MS_MAX);
    Ok(options)
}

/// A controller's name comes off its USB descriptor and lands in a terminal
/// or a log somebody later cats: nothing device-supplied writes live escape
/// sequences into whoever is reading.
fn printable(name: &[u8]) -> String {
    name.iter()
        .map(|&c| if (0x20..0x7f).contains(&c) { c as char } else { '?' })
        .collect()
}

/// The game, pinned by when it started. A pid on its own is a slot: the game
/// exits, the kernel hands the number on, and a watcher that has sat for three
/// hours signals a stranger.
#[derive(Debug)]
struct Game {
    pid: i32,
    started: Option<u64>,
}

impl Game {
    fn same(&self) -> bool {
        match (self.started, procstat::started(self.pid)) {
            (Some(then), Some(now)) => then == now,
            // Never learned it, or cannot read it now: the pid is all there is.
            _ => true,
        }
    }

    /// Everything the game started, not just the process we were handed:
    /// `gotg play` execs the wrapper that execs the emulator, so the pid is
    /// usually its process group's leader, and an emulator under a shim has
    /// children a signal to one pid would leave running.
    fn signal(&self, signal: libc::c_int) {
        if !self.same() {
            eprintln!(
                "gotg-killswitch: {} is not the game any more; signalling nobody",
                self.pid
            );
            return;
        }
        // SAFETY: getpgid and kill take plain integers.
        unsafe {
            let group = libc::getpgid(self.pid);
            // Never kill(-1): "every process this user may signal", which is
            // what a group kill becomes for pid 1.
            if group == self.pid && group > 1 {
                libc::kill(-group, signal);
            } else {
                libc::kill(self.pid, signal);
            }
        }
    }

    /// Politely first: an emulator that takes SIGTERM writes its save, which
    /// is the difference between quitting a game and losing an hour of it.
    fn stop(&self, grace_ms: u64, poll_ms: u64) {
        eprintln!("gotg-killswitch: kill switch held; stopping {}", self.pid);
        self.signal(libc::SIGTERM);
        let mut waited = 0;
        while waited < grace_ms && !STOP.load(Ordering::Relaxed) {
            if !procstat::alive(self.pid) {
                eprintln!("gotg-killswitch: stopped");
                return;
            }
            std::thread::sleep(Duration::from_millis(poll_ms));
            waited += poll_ms;
        }
        if !procstat::alive(self.pid) {
            eprintln!("gotg-killswitch: stopped");
            return;
        }
        // Asked to go ourselves -- a launcher tidying up, a session ending --
        // while the game is still writing its save: it keeps its SIGTERM, and
        // is not killed for our leaving.
        if STOP.load(Ordering::Relaxed) {
            eprintln!(
                "gotg-killswitch: asked to go mid-grace; leaving {} its SIGTERM",
                self.pid
            );
            return;
        }
        eprintln!("gotg-killswitch: it did not go; killing");
        self.signal(libc::SIGKILL);
    }
}

/// One controller being watched.
struct Watched {
    id: SDL_JoystickID,
    pad: *mut SDL_Gamepad,
    state: Pad,
    /// Whether this hold has been mentioned in the log.
    announced: bool,
}

#[derive(Default)]
struct Pads(Vec<Watched>);

impl Pads {
    fn open(&mut self, id: SDL_JoystickID, hold_ms: u64, quiet: bool) {
        if self.0.iter().any(|watched| watched.id == id) {
            return;
        }
        // SAFETY: SDL is initialised; the id came from SDL.
        let pad = unsafe { SDL_OpenGamepad(id) };
        if pad.is_null() {
            return;
        }
        if !quiet {
            // SAFETY: the pad is open; SDL's name is null or a C string it owns.
            let name = unsafe { SDL_GetGamepadName(pad) };
            let name = if name.is_null() {
                String::new()
            } else {
                printable(unsafe { CStr::from_ptr(name) }.to_bytes())
            };
            eprintln!(
                "gotg-killswitch: watching {}",
                if name.is_empty() { "a controller" } else { &name }
            );
        }
        self.0.push(Watched {
            id,
            pad,
            state: Pad::new(hold_ms),
            announced: false,
        });
    }

    fn close(&mut self, id: SDL_JoystickID) {
        self.0.retain(|watched| {
            if watched.id == id {
                // SAFETY: opened by `open`, closed once.
                unsafe { SDL_CloseGamepad(watched.pad) };
            }
            watched.id != id
        });
    }
}

impl Drop for Pads {
    fn drop(&mut self) {
        for watched in &self.0 {
            // SAFETY: opened by `open`, closed once.
            unsafe { SDL_CloseGamepad(watched.pad) };
        }
    }
}

/// What one controller is doing. Button or axis, either counts.
///
/// # Safety
/// `pad` is an open gamepad.
unsafe fn read_pad(pad: *mut SDL_Gamepad) -> Input {
    // SAFETY: per this function's contract.
    unsafe {
        Input {
            left: SDL_GetGamepadButton(pad, SDL_GAMEPAD_BUTTON_LEFT_SHOULDER)
                || SDL_GetGamepadAxis(pad, SDL_GAMEPAD_AXIS_LEFT_TRIGGER) >= TRIGGER_ON,
            right: SDL_GetGamepadButton(pad, SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER)
                || SDL_GetGamepadAxis(pad, SDL_GAMEPAD_AXIS_RIGHT_TRIGGER) >= TRIGGER_ON,
            start: SDL_GetGamepadButton(pad, SDL_GAMEPAD_BUTTON_START),
            back: SDL_GetGamepadButton(pad, SDL_GAMEPAD_BUTTON_BACK),
        }
    }
}

/// How long padmap was asked to make a hold take, so a fill carries on at the
/// right rate between readings.
fn pair_hold_seconds() -> f64 {
    std::env::var("PADMAP_HOLD_SECONDS")
        .ok()
        .and_then(|text| text.parse::<f64>().ok())
        .filter(|&value| value > 0.0 && value < 60.0)
        .unwrap_or(1.5)
}

/// Seconds on SDL's monotonic clock: what the pairing and the bar are timed by.
fn seconds_now() -> f64 {
    // SAFETY: SDL_GetTicksNS is callable at any time.
    unsafe { SDL_GetTicksNS() as f64 / 1e9 }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    // The painter: this program again, drawing what it is sent.
    if args.len() == 1 && args[0] == "--paint" {
        std::process::exit(painter::paint());
    }
    if args.iter().any(|arg| arg == "--help" || arg == "-h") {
        print!("{USAGE}");
        return;
    }
    let options = match parse(&args) {
        Ok(options) => options,
        Err(why) => {
            eprintln!("gotg-killswitch: {why}");
            eprint!("{USAGE}");
            std::process::exit(2);
        }
    };
    std::process::exit(run(&options));
}

fn run(options: &Options) -> i32 {
    let game = Game {
        pid: options.pid,
        started: procstat::started(options.pid),
    };
    if !procstat::alive(game.pid) {
        // Nothing to guard. Not an error: the game has already exited, which
        // is the outcome this exists to produce.
        return 0;
    }
    if game.started.is_none() {
        // No /proc to pin it by: a recycled pid would not be told apart.
        eprintln!(
            "gotg-killswitch: cannot read when {} started; guarding it by pid alone",
            game.pid
        );
    }
    // SAFETY: process-wide setup before any thread exists; the handler only
    // stores to an atomic.
    unsafe {
        // A process group of its own: spawned from a non-interactive shell it
        // would share the game's, and the group kill would land on the
        // watcher mid-escalation.
        libc::setpgid(0, 0);
        libc::signal(libc::SIGTERM, on_signal as *const () as libc::sighandler_t);
        libc::signal(libc::SIGINT, on_signal as *const () as libc::sighandler_t);
        // No window, so tell SDL that input still counts; and see what the
        // emulators see, the hidapi-only Steam Controller included.
        SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, c"1".as_ptr());
        SDL_SetHint(SDL_HINT_JOYSTICK_HIDAPI_STEAM, c"1".as_ptr());
        if !SDL_Init(SDL_INIT_GAMEPAD) {
            // A machine with no input stack at all. The game is running and
            // must stay running: no kill switch is a worse session, not a
            // failed one.
            let why = CStr::from_ptr(SDL_GetError()).to_string_lossy();
            eprintln!("gotg-killswitch: no controller support here ({why}); carrying on without it");
            return 0;
        }
    }
    // Armed, and the log says so: a launch that promised a kill switch and a
    // launch whose watcher died on the first line must not look the same.
    if !options.quiet {
        eprintln!(
            "gotg-killswitch: watching {}; both shoulders and Start, held {}ms",
            game.pid, options.hold_ms
        );
    }
    let mut pads = Pads::default();
    // SAFETY: SDL is initialised; the id array is SDL's and freed once.
    unsafe {
        let mut count = 0;
        let ids = SDL_GetGamepads(&mut count);
        if !ids.is_null() {
            for i in 0..usize::try_from(count).unwrap_or(0) {
                pads.open(*ids.add(i), options.hold_ms, options.quiet);
            }
            SDL_free(ids.cast());
        }
    }
    watch(options, &game, &mut pads);
    drop(pads);
    // SAFETY: last SDL call, from the thread that initialised it.
    unsafe { SDL_Quit() };
    0
}

fn watch(options: &Options, game: &Game, pads: &mut Pads) {
    // Who is joining, from padmap's own socket: one more client beside the
    // picker. Nothing is asked of the daemon; the overlay only listens.
    let mut pairing = Pairing::new(pair_hold_seconds());
    let mut link = Link::new(std::env::var("GOTG_OVERLAY_PADMAP_SOCKET").ok().as_deref());
    let mut bar = Bar::new(SLIDE_SECONDS);
    let mut painter = Painter::default();
    let mut next_alive_ms = 0;
    while !STOP.load(Ordering::Relaxed) {
        // SAFETY: SDL is initialised; the event is plain data SDL fills.
        unsafe {
            let mut event: SDL_Event = std::mem::zeroed();
            while SDL_PollEvent(&mut event) {
                let kind = SDL_EventType(event.r#type);
                if kind == SDL_EVENT_GAMEPAD_ADDED {
                    pads.open(event.gdevice.which, options.hold_ms, options.quiet);
                } else if kind == SDL_EVENT_GAMEPAD_REMOVED {
                    pads.close(event.gdevice.which);
                }
            }
        }
        // SAFETY: as above.
        let now = unsafe { SDL_GetTicks() };
        let clock = seconds_now();
        if options.draw {
            link.tick(clock);
            link.pump(&mut pairing, clock);
        }
        // The furthest along any one pad is, since the picture is of a hold
        // rather than of a controller.
        let mut progress = 0.0f64;
        let mut fire = false;
        for watched in &mut pads.0 {
            // SAFETY: every watched pad is open.
            let input = unsafe { read_pad(watched.pad) };
            fire |= watched.state.step(input, now);
            // One line when a hold starts, so the log of a session that ended
            // this way says why.
            if !options.quiet && watched.state.holding() && !watched.announced {
                watched.announced = true;
                eprintln!("gotg-killswitch: kill switch held; {}ms to go", options.hold_ms);
            }
            if !watched.state.holding() {
                watched.announced = false;
            }
            let held = if options.hold_ms > 0 {
                watched.state.held_ms(now) as f64 / options.hold_ms as f64
            } else if watched.state.holding() {
                1.0
            } else {
                0.0
            };
            progress = progress.max(held);
        }
        let exit_progress = if fire { 1.0 } else { progress };

        // Down while there is something to show, up when not. The painter
        // exists only while the bar is anywhere on screen, so a session
        // nobody joins or leaves never has one -- and a machine with no
        // display is one whose painter exits at once and is asked again less
        // and less often.
        bar.want(
            options.draw && (exit_progress > 0.0 || pairing.busy(clock)),
            clock,
        );
        let showing = !bar.gone(clock);
        let mut moving = false;
        if showing {
            let holds = pairing.now(clock);
            let joined = pairing.joined(clock);
            moving = bar.moving(clock) || !holds.is_empty() || exit_progress > 0.0;
            let frame = Frame::pack(bar.position(clock), exit_progress, &holds, &joined);
            painter.ensure(clock);
            painter.send(&frame, clock);
        } else if painter.open() {
            painter.release(clock);
        }
        painter.tick(clock);

        if fire {
            // Let the closed ring be seen: a picture that vanished with the
            // game would leave nothing to have understood.
            if showing {
                std::thread::sleep(Duration::from_millis(LINGER_MS));
            }
            game.stop(options.grace_ms, options.poll_ms);
            break;
        }
        // Is the game still there? At the idle rate, not the frame rate.
        if now >= next_alive_ms {
            next_alive_ms = now + options.poll_ms;
            if !procstat::alive(game.pid) {
                break;
            }
        }
        if moving {
            std::thread::sleep(Duration::from_millis(FRAME_MS));
        } else if let Some(fd) = link.fd() {
            // A join has to show the moment padmap says so: sleep on its
            // socket rather than for a fixed tenth of a second.
            let mut wait = libc::pollfd {
                fd,
                events: libc::POLLIN,
                revents: 0,
            };
            // SAFETY: one pollfd, owned here, for the length of the call.
            unsafe { libc::poll(&mut wait, 1, options.poll_ms as libc::c_int) };
        } else {
            std::thread::sleep(Duration::from_millis(options.poll_ms));
        }
    }
    painter.close();
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn a_pid_is_required_and_is_not_one_or_zero() {
        assert!(parse(&args(&[])).is_err());
        assert!(
            parse(&args(&["--pid", "1"])).is_err(),
            "a group kill of pid 1 is everything"
        );
        assert!(parse(&args(&["--pid", "0"])).is_err());
        assert_eq!(parse(&args(&["--pid", "42"])).map(|o| o.pid).ok(), Some(42));
    }

    #[test]
    fn a_negative_or_odd_number_is_refused_not_wrapped() {
        assert!(parse(&args(&["--pid", "42", "--hold-ms", "-1"])).is_err());
        assert!(parse(&args(&["--pid", "42", "--hold-ms", "+5"])).is_err());
        assert!(
            parse(&args(&["--pid", "42", "--hold-ms"])).is_err(),
            "a flag with no value"
        );
        assert!(parse(&args(&["--pid", "99999999999"])).is_err(), "past any pid");
    }

    #[test]
    fn the_poll_is_kept_between_useful_bounds() {
        let poll = |ms: &str| {
            parse(&args(&["--pid", "42", "--poll-ms", ms]))
                .map(|o| o.poll_ms)
                .ok()
        };
        assert_eq!(poll("0"), Some(DEFAULT_POLL_MS));
        assert_eq!(poll("60000"), Some(POLL_MS_MAX));
    }

    #[test]
    fn flags_are_read() {
        let options = parse(&args(&[
            "--quiet",
            "--no-overlay",
            "--pid",
            "7",
            "--grace-ms",
            "10",
        ]));
        assert!(options.is_ok_and(|o| o.quiet && !o.draw && o.grace_ms == 10));
        assert!(parse(&args(&["--pid", "7", "--bogus"])).is_err());
    }

    #[test]
    fn a_device_name_cannot_write_escapes() {
        assert_eq!(printable(b"Pad\x1b[2J\xff"), "Pad?[2J?");
    }

    #[test]
    fn pid_two_is_the_smallest_accepted() {
        assert_eq!(parse(&args(&["--pid", "2"])).map(|o| o.pid).ok(), Some(2));
    }
}
