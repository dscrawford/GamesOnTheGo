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
//! for a pad holding to join danstick.

use std::ffi::CStr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use gotg_killswitch::bar::Bar;
use gotg_killswitch::clones;
use gotg_killswitch::consoles::{self, CONSOLES};
use gotg_killswitch::frame::{EMPTY_SEAT, Frame, MenuFrame, ROWS_MAX, Rebinding, Saying};
use gotg_killswitch::killswitch::{Chord, Input, Pad};
use gotg_killswitch::menu::{self, Focused, Menu, Row};
use gotg_killswitch::native::Native;
use gotg_killswitch::padlink::Link;
use gotg_killswitch::painter::{self, Painter};
use gotg_killswitch::pairing::Pairing;
use gotg_killswitch::pressing::Held;
use gotg_killswitch::procstat;
use gotg_killswitch::rebind::{Rebind, View};
use gotg_killswitch::seating::Seating;
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
/// L + R + A held this long brings the menu down.
const MENU_HOLD_MS: u64 = 1000;
/// Closing the menu, the bar goes up over this long.
const MENU_CLOSE_SECONDS: f64 = 1.0;
/// The longest the saves are waited on after the menu's Exit.
const SAVE_SECONDS: f64 = 60.0;

static STOP: AtomicBool = AtomicBool::new(false);

extern "C" fn on_signal(_: libc::c_int) {
    STOP.store(true, Ordering::Relaxed);
}

const USAGE: &str =
    "usage: gotg-killswitch --pid <pid> [--platform P] [--hold-ms N] [--grace-ms N] [--poll-ms N]
                       [--quiet] [--no-overlay] [--overlay-only]
                       [--saves ENV --client PATH]

Watches every controller SDL can see. When both shoulders (or both
triggers) and Start are held together for the hold time, the process
is asked to stop, and killed if it will not. Exits on its own when
that process is gone.

Both shoulders and A, held a second, bring the menu down for that
controller's player: the seated controllers (A rebinds one, A held
moves it to another seat), and Exit (A held a second) -- which stops
the game and, with --saves and --client, runs `CLIENT saves push ENV`
on the way out. B held a second closes it.

A bar comes down over the game while the hold runs, and while a
controller is holding a button to join danstick -- unless --no-overlay
says otherwise.

--overlay-only is the bar and nothing else, for beside something that
is not a game (the picker): no chord stops it and none rebinds.
";

#[derive(Debug)]
struct Options {
    pid: i32,
    hold_ms: u64,
    grace_ms: u64,
    poll_ms: u64,
    quiet: bool,
    draw: bool,
    /// The game's platform, for which controller the rebind draws and which
    /// danstick layout it walks.
    platform: String,
    /// The bar alone: no exit, no rebind. Beside the picker, which has its
    /// own way out and no console to rebind for.
    overlay_only: bool,
    /// The environment whose saves go up when the menu's Exit stops the game,
    /// and the client that pushes them.
    saves: String,
    client: String,
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
        platform: String::new(),
        overlay_only: false,
        saves: String::new(),
        client: String::new(),
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
            "--overlay-only" => {
                options.overlay_only = true;
                continue;
            }
            "--saves" => {
                options.saves = args
                    .next()
                    .filter(|e| {
                        !e.is_empty()
                            && e.len() <= 200
                            && e.bytes()
                                .all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b))
                    })
                    .ok_or_else(|| format!("bad argument: {arg}"))?
                    .clone();
                continue;
            }
            "--client" => {
                options.client = args
                    .next()
                    .filter(|c| c.starts_with('/') && !c.contains('\n'))
                    .ok_or_else(|| format!("bad argument: {arg}"))?
                    .clone();
                continue;
            }
            "--platform" => {
                options.platform = args
                    .next()
                    .filter(|p| {
                        !p.is_empty()
                            && p.bytes()
                                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
                    })
                    .ok_or_else(|| format!("bad argument: {arg}"))?
                    .clone();
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
    /// The menu chord's hold, beside the exit's.
    menu: Pad,
    /// The seat this pad is danstick's clone for; None for a raw pad.
    player: Option<i32>,
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
        // SAFETY: SDL is initialised; the id came from SDL.
        let guid = unsafe { SDL_GetGamepadGUIDForID(id) };
        self.0.push(Watched {
            id,
            pad,
            state: Pad::new(hold_ms),
            menu: Pad::timing(Chord::Menu, MENU_HOLD_MS),
            player: clones::player_of_guid(&guid.data),
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
            a: SDL_GetGamepadButton(pad, SDL_GAMEPAD_BUTTON_SOUTH),
        }
    }
}

/// SDL's buttons by danstick's name for each: they are SDL's element names.
const STANDARD_BUTTONS: [(SDL_GamepadButton, &str); 16] = [
    (SDL_GAMEPAD_BUTTON_SOUTH, "a"),
    (SDL_GAMEPAD_BUTTON_EAST, "b"),
    (SDL_GAMEPAD_BUTTON_WEST, "x"),
    (SDL_GAMEPAD_BUTTON_NORTH, "y"),
    (SDL_GAMEPAD_BUTTON_BACK, "back"),
    (SDL_GAMEPAD_BUTTON_GUIDE, "guide"),
    (SDL_GAMEPAD_BUTTON_START, "start"),
    (SDL_GAMEPAD_BUTTON_LEFT_STICK, "leftstick"),
    (SDL_GAMEPAD_BUTTON_RIGHT_STICK, "rightstick"),
    (SDL_GAMEPAD_BUTTON_LEFT_SHOULDER, "leftshoulder"),
    (SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER, "rightshoulder"),
    (SDL_GAMEPAD_BUTTON_DPAD_UP, "dpup"),
    (SDL_GAMEPAD_BUTTON_DPAD_DOWN, "dpdown"),
    (SDL_GAMEPAD_BUTTON_DPAD_LEFT, "dpleft"),
    (SDL_GAMEPAD_BUTTON_DPAD_RIGHT, "dpright"),
    (SDL_GAMEPAD_BUTTON_MISC1, "misc1"),
];

/// What a clone has down, in danstick's names, and where its sticks are.
///
/// # Safety
/// `pad` is an open gamepad.
unsafe fn read_held(pad: *mut SDL_Gamepad) -> Held {
    // SAFETY: per this function's contract.
    unsafe {
        let buttons: Vec<&str> = STANDARD_BUTTONS
            .iter()
            .filter(|(button, _)| SDL_GetGamepadButton(pad, *button))
            .map(|(_, name)| *name)
            .collect();
        let axis = |axis| f32::from(SDL_GetGamepadAxis(pad, axis)) / 32767.0;
        Held::standard(
            &buttons,
            [
                axis(SDL_GAMEPAD_AXIS_LEFTX),
                axis(SDL_GAMEPAD_AXIS_LEFTY),
                axis(SDL_GAMEPAD_AXIS_RIGHTX),
                axis(SDL_GAMEPAD_AXIS_RIGHTY),
                axis(SDL_GAMEPAD_AXIS_LEFT_TRIGGER),
                axis(SDL_GAMEPAD_AXIS_RIGHT_TRIGGER),
            ],
        )
    }
}

/// How long danstick was asked to make a hold take, so a fill carries on at the
/// right rate between readings.
fn pair_hold_seconds() -> f64 {
    std::env::var("DANSTICK_HOLD_SECONDS")
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
    if options.overlay_only {
        if !options.quiet {
            eprintln!("gotg-killswitch: the bar alone, beside {}", game.pid);
        }
    } else if !options.quiet {
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
    // Who is joining, from danstick's own socket: one more client beside the
    // picker. What it asks is that danstick go on listening for a hold
    // (`seating`), and a rebind when somebody wants one.
    let mut pairing = Pairing::new(pair_hold_seconds());
    let mut link = Link::new(std::env::var("GOTG_OVERLAY_DANSTICK_SOCKET").ok().as_deref());
    let mut bar = Bar::new(SLIDE_SECONDS);
    let mut painter = Painter::default();
    let mut rebind = Rebind::default();
    let mut seating = Seating::new(pair_hold_seconds());
    let mut focused = Focused::default();
    // The overlay's own controls, per seated player, and each player's two
    // holds timed on them -- the exit's and the menu's.
    let mut native = Native::default();
    let mut native_holds: std::collections::HashMap<i32, (Pad, Pad, bool)> = std::collections::HashMap::new();
    let mut menu: Option<Menu> = None;
    let mut exiting = false;
    let mut seat_icons: std::collections::HashMap<String, u8> = std::collections::HashMap::new();
    let console_index = consoles::for_platform(&options.platform);
    let console = &CONSOLES[console_index];
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
        // Linked whether or not anything is drawn: somebody joining is not
        // a picture, and without the overlay nothing else keeps seating open.
        let was_linked = link.fd().is_some();
        link.tick(clock);
        // Beside a game, on each new connection: what is being played, so
        // danstick drives every clone from this console's walk. Leased to this
        // connection, so it is sent again whenever the connection is new.
        if !was_linked && link.fd().is_some() && !options.overlay_only && !options.platform.is_empty() {
            link.send(&console.playing());
        }
        // And the watch on every seated pad's own controls, for the chords --
        // leased to this connection too.
        if link.fd().is_some()
            && !options.overlay_only
            && let Some(line) = native.wanted()
        {
            link.send(&line);
        }
        link.pump(
            &mut pairing,
            &mut rebind,
            &mut [&mut seating, &mut focused, &mut native],
            clock,
        );
        if link.fd().is_none() {
            pairing.room(None);
            seating.lost();
            native.lost();
        } else if let Some(line) = seating.wanted()
            && !link.send(&line)
        {
            seating.lost();
        }
        if let Some(player) = rebind
            .due(clock)
            .filter(|_| options.draw && !options.overlay_only)
        {
            if !options.quiet {
                eprintln!("gotg-killswitch: player {player}'s pad has no buttons yet; walking them");
            }
            ask_for_rebind(
                Some(player),
                console,
                &mut rebind,
                &mut link,
                clock,
                options.quiet,
            );
        }
        // The furthest along any one pad is, since the picture is of a hold
        // rather than of a controller.
        let mut progress = 0.0f64;
        let mut fire = false;
        // Who asked for the menu this frame, and what they had down (the
        // chord's own buttons, which the menu ignores until let go).
        let mut asked: Option<(Option<i32>, std::collections::BTreeSet<String>)> = None;
        for watched in &mut pads.0 {
            // SAFETY: every watched pad is open.
            let input = unsafe { read_pad(watched.pad) };
            if options.overlay_only {
                continue;
            }
            // A seated pad's chords are its own controls, which danstick says
            // (`native`); its clone carries the game's walk and is not read
            // for them. A pad nobody seated is not held, and SDL reads it.
            if native.active() && watched.player.is_some() {
                continue;
            }
            let (fired, held, menu_fired) = chords(
                input,
                &mut watched.state,
                &mut watched.menu,
                &mut watched.announced,
                now,
                options,
            );
            fire |= fired;
            progress = progress.max(held);
            if menu_fired {
                // SAFETY: every watched pad is open.
                asked = Some((watched.player, unsafe { read_held(watched.pad) }.controls));
            }
        }
        if native.active() && !options.overlay_only {
            let seated: Vec<i32> = rebind.seated().iter().map(|seat| seat.player).collect();
            native_holds.retain(|player, _| seated.contains(player));
            for player in seated {
                let (exit, menu_hold, announced) = native_holds.entry(player).or_insert_with(|| {
                    (
                        Pad::new(options.hold_ms),
                        Pad::timing(Chord::Menu, MENU_HOLD_MS),
                        false,
                    )
                });
                let (fired, held, menu_fired) =
                    chords(native.input(player), exit, menu_hold, announced, now, options);
                fire |= fired;
                progress = progress.max(held);
                if menu_fired {
                    asked = Some((Some(player), native.held(player)));
                }
            }
        }
        if let Some((who, down)) = asked
            && options.draw
            && menu.is_none()
            && rebind.view(clock).is_none()
        {
            match who {
                Some(owner) => {
                    let rows = seat_rows(&rebind, &seating, &mut seat_icons);
                    menu = Some(Menu::open(owner, rows, &down));
                    focused.clear();
                    send_focus(&mut link, &focused, owner, true);
                    if !options.quiet {
                        eprintln!("gotg-killswitch: menu down for player {owner}");
                    }
                }
                None if !options.quiet => {
                    eprintln!("gotg-killswitch: menu held on a pad danstick has not seated; nothing to show");
                }
                None => {}
            }
        }
        let exit_progress = if fire { 1.0 } else { progress };

        // The menu, driven by its owner alone: danstick's word for what that
        // player has down while it holds them (`focus`), and the clone's
        // through SDL besides -- which is all there is from a danstick that
        // cannot hold them, and nothing once one does.
        if let Some(open) = menu.as_mut() {
            open.seats(seat_rows(&rebind, &seating, &mut seat_icons));
            let owner = open.owner;
            let mut down = focused.of(owner);
            if let Some(watched) = pads.0.iter().find(|w| w.player == Some(owner)) {
                // SAFETY: every watched pad is open.
                down.extend(unsafe { read_held(watched.pad) }.controls);
            }
            match open.tick(&down, clock) {
                Some(menu::Action::Close) => {
                    send_focus(&mut link, &focused, owner, false);
                    menu = None;
                    bar.want_over(false, clock, MENU_CLOSE_SECONDS);
                }
                Some(menu::Action::Exit) => {
                    send_focus(&mut link, &focused, owner, false);
                    menu = None;
                    exiting = true;
                }
                Some(menu::Action::Rebind(player)) => {
                    send_focus(&mut link, &focused, owner, false);
                    menu = None;
                    ask_for_rebind(
                        Some(player),
                        console,
                        &mut rebind,
                        &mut link,
                        clock,
                        options.quiet,
                    );
                }
                Some(menu::Action::Move { player, to }) => {
                    link.send(&serde_json::json!({"cmd": "move", "player": player, "to": to}).to_string());
                }
                None => {}
            }
        }

        // Down while there is something to show, up when not. The painter
        // exists only while the bar is anywhere on screen, so a session
        // nobody joins or leaves never has one -- and a machine with no
        // display is one whose painter exits at once and is asked again less
        // and less often.
        let rebinding = rebind.view(clock);
        bar.want(
            options.draw
                && (exit_progress > 0.0
                    || pairing.busy(clock)
                    || pairing.nobody(clock)
                    || rebinding.is_some()
                    || menu.is_some()
                    || exiting),
            clock,
        );
        let showing = !bar.gone(clock);
        let mut moving = false;
        if showing {
            let holds = pairing.now(clock);
            let joined = pairing.joined(clock);
            // A rebind panel shows what is pressed, so it is redrawn at the
            // frame rate for as long as it is down.
            moving = bar.moving(clock)
                || !holds.is_empty()
                || exit_progress > 0.0
                || rebinding.is_some()
                || menu.is_some();
            let frame = Frame::pack(bar.position(clock), exit_progress, &holds, &joined)
                .with_nobody(pairing.nobody(clock))
                .with_menu(menu.as_ref().map(|open| menu_frame(&open.view(clock))))
                .with_saying(if exiting { Saying::Saving } else { Saying::Nothing })
                .with_rebind(rebinding.map(|view| {
                    // danstick's word while it walks; the seat's clone after.
                    let held = view.held.clone().unwrap_or_else(|| {
                        pads.0
                            .iter()
                            .find(|watched| watched.player == Some(view.player))
                            // SAFETY: every watched pad is open.
                            .map(|watched| unsafe { read_held(watched.pad) })
                            .unwrap_or_default()
                    });
                    drawn(&view, &held, console, console_index)
                }));
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
        if exiting {
            // The menu's Exit: the game asked to go (its own saves written in
            // the grace it gets), then this machine's saves sent up, with the
            // bar saying so until they are.
            game.stop(options.grace_ms, options.poll_ms);
            push_saves(options, &mut painter, &bar);
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
            // A join has to show the moment danstick says so: sleep on its
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

/// One pad's two holds for one sample: whether the exit fired, how far through
/// it is (0..1), and whether the menu's fired. The same for a pad read through
/// SDL and a seated player read through danstick's `native`.
fn chords(
    input: Input,
    exit: &mut Pad,
    menu_hold: &mut Pad,
    announced: &mut bool,
    now: u64,
    options: &Options,
) -> (bool, f64, bool) {
    let fired = exit.step(input, now);
    let menu_fired = menu_hold.step(input, now);
    // One line when a hold starts, so the log of a session that ended this
    // way says why.
    if !options.quiet && exit.holding() && !*announced {
        *announced = true;
        eprintln!("gotg-killswitch: kill switch held; {}ms to go", options.hold_ms);
    }
    if !exit.holding() {
        *announced = false;
    }
    let held = if options.hold_ms > 0 {
        exit.held_ms(now) as f64 / options.hold_ms as f64
    } else if exit.holding() {
        1.0
    } else {
        0.0
    };
    (fired, held, menu_fired)
}

/// `focus`, unless danstick has said it does not know it.
fn send_focus(link: &mut Link, focused: &Focused, player: i32, open: bool) {
    if !focused.refused {
        link.send(&focus_line(player, open));
    }
}

/// danstick's `focus`: hold `player`'s pad back from the game while the menu
/// has it, and report its controls -- or let go of it.
fn focus_line(player: i32, open: bool) -> String {
    serde_json::json!({"cmd": "focus", "player": player, "open": open}).to_string()
}

/// The seats as the menu lists them: every slot danstick has, each with the
/// drawing of the pad in it, looked up once per node.
fn seat_rows(
    rebind: &Rebind,
    seating: &Seating,
    icons: &mut std::collections::HashMap<String, u8>,
) -> Vec<Row> {
    let seated = rebind.seated();
    let slots = seated
        .iter()
        .map(|seat| seat.player)
        .max()
        .unwrap_or(0)
        .max(seating.slots())
        .clamp(1, menu::SEATS_MAX as i32);
    (1..=slots)
        .map(|player| Row {
            icon: seated.iter().find(|seat| seat.player == player).map(|seat| {
                let key = format!("{}|{}", seat.node, seat.name);
                *icons
                    .entry(key)
                    .or_insert_with(|| gotg_killswitch::icons::resolve(&seat.node, &seat.name))
            }),
        })
        .collect()
}

/// The menu as a frame carries it.
fn menu_frame(view: &menu::View) -> MenuFrame {
    let mut icons = [EMPTY_SEAT; ROWS_MAX];
    for (at, row) in view.rows.iter().take(ROWS_MAX).enumerate() {
        // A drawing numbered like the empty marker would read as empty.
        icons[at] = row.icon.map_or(EMPTY_SEAT, |icon| icon.min(EMPTY_SEAT - 1));
    }
    MenuFrame {
        owner: view.owner,
        rows: view.rows.len().min(ROWS_MAX) as u32,
        icons,
        focus: view.focus as u32,
        carried: view.carried.unwrap_or(0),
        a_fill: view.a_fill,
        b_fill: view.b_fill,
    }
}

/// After the menu's Exit: `CLIENT saves push ENV`, waited on (up to a minute)
/// with the bar down saying so. Nothing to push with, nothing is waited on.
fn push_saves(options: &Options, painter: &mut Painter, bar: &Bar) {
    if options.saves.is_empty() || options.client.is_empty() {
        return;
    }
    if !options.quiet {
        eprintln!("gotg-killswitch: pushing the saves for {}", options.saves);
    }
    let child = std::process::Command::new(&options.client)
        .args(["saves", "push", &options.saves])
        .stdin(std::process::Stdio::null())
        .spawn();
    let mut child = match child {
        Ok(child) => child,
        Err(error) => {
            eprintln!("gotg-killswitch: could not push the saves: {error}");
            return;
        }
    };
    let started = seconds_now();
    loop {
        let clock = seconds_now();
        if options.draw {
            let frame = Frame::pack(bar.position(clock), 0.0, &[], &[]).with_saying(Saying::Saving);
            painter.ensure(clock);
            painter.send(&frame, clock);
            painter.tick(clock);
        }
        match child.try_wait() {
            Ok(Some(status)) => {
                if !status.success() {
                    eprintln!("gotg-killswitch: the saves push said {status}");
                }
                break;
            }
            Ok(None) if clock - started < SAVE_SECONDS => std::thread::sleep(Duration::from_millis(FRAME_MS)),
            _ => {
                eprintln!("gotg-killswitch: the saves push took too long; leaving it to finish on its own");
                break;
            }
        }
    }
}

/// The rebind chord fired on a pad: ask danstick to walk that seat's buttons.
fn ask_for_rebind(
    player: Option<i32>,
    console: &consoles::Console,
    rebind: &mut Rebind,
    link: &mut Link,
    clock: f64,
    quiet: bool,
) {
    let Some(player) = player else {
        // A raw pad: danstick has published no clone for it, so there is no
        // seat of danstick's to rebind. Seat it first, then rebind it.
        if !quiet {
            eprintln!("gotg-killswitch: rebind held on a pad danstick has not seated; nothing to rebind");
        }
        return;
    };
    let Some(line) = rebind.start(player, console.layout, &console.scope(), clock) else {
        return;
    };
    if link.send(&line) {
        if !quiet {
            eprintln!(
                "gotg-killswitch: rebinding player {player} ({} layout)",
                console.layout
            );
        }
    } else {
        eprintln!("gotg-killswitch: danstick is not listening; cannot rebind player {player}");
        rebind.unsent(clock);
    }
}

/// A rebind as the painter draws it.
fn drawn(view: &View, held: &Held, console: &consoles::Console, console_index: usize) -> Rebinding {
    Rebinding {
        player: view.player,
        console: console_index as u32,
        control: console
            .control(&view.control)
            .and_then(|at| i32::try_from(at).ok())
            .unwrap_or(-1),
        index: view.index,
        total: view.total,
        finish: view.finish as f32,
        ended: match view.stored {
            None => 0,
            Some(true) => 1,
            Some(false) => 2,
        },
        pressed: held.bits(console.controls.iter().map(|control| control.id)),
        sticks: held.sticks,
    }
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
        assert!(options.is_ok_and(|o| o.quiet && !o.draw && o.grace_ms == 10 && !o.overlay_only));
        assert!(parse(&args(&["--pid", "7", "--overlay-only"])).is_ok_and(|o| o.overlay_only && o.draw));
        assert!(parse(&args(&["--pid", "7", "--bogus"])).is_err());
    }

    #[test]
    fn a_device_name_cannot_write_escapes() {
        assert_eq!(printable(b"Pad\x1b[2J\xff"), "Pad?[2J?");
    }

    #[test]
    fn the_platform_is_read_and_nothing_odd_gets_through() {
        let platform = |p: &str| parse(&args(&["--pid", "7", "--platform", p])).map(|o| o.platform);
        assert_eq!(platform("n64").as_deref(), Ok("n64"));
        assert!(platform("../etc").is_err());
        assert!(platform("").is_err());
        assert_eq!(
            parse(&args(&["--pid", "7"])).map(|o| o.platform).as_deref(),
            Ok("")
        );
    }

    #[test]
    fn the_saves_to_push_and_the_client_are_read_and_nothing_odd_gets_through() {
        let parsed = parse(&args(&[
            "--pid",
            "7",
            "--saves",
            "env-n64-usa_donkey_kong_64",
            "--client",
            "/nix/store/x-gotg/share/gotg/bin/gotg",
        ]));
        assert!(parsed.is_ok_and(|o| o.saves == "env-n64-usa_donkey_kong_64" && o.client.ends_with("/gotg")));
        assert!(parse(&args(&["--pid", "7", "--saves", "env;rm -rf"])).is_err());
        assert!(
            parse(&args(&["--pid", "7", "--client", "gotg"])).is_err(),
            "a path, not a name"
        );
    }

    #[test]
    fn pid_two_is_the_smallest_accepted() {
        assert_eq!(parse(&args(&["--pid", "2"])).map(|o| o.pid).ok(), Some(2));
    }
}
