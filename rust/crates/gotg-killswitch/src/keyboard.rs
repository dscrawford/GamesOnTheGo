//! A uinput keyboard for the emulator's hotkeys: under Wayland nothing else
//! can press a key into another client's window.

use std::fs::{File, OpenOptions};
use std::io;
use std::os::fd::AsRawFd;
use std::time::Duration;

const EV_SYN: u16 = 0;
const EV_KEY: u16 = 1;
const UI_SET_EVBIT: libc::c_ulong = 0x4004_5564;
const UI_SET_KEYBIT: libc::c_ulong = 0x4004_5565;
const UI_DEV_SETUP: libc::c_ulong = 0x405c_5503;
const UI_DEV_CREATE: libc::c_ulong = 0x5501;
const UI_DEV_DESTROY: libc::c_ulong = 0x5502;
/// An emulator polls its keyboard once a frame.
const HELD_MS: u64 = 40;

#[repr(C)]
struct InputId {
    bustype: u16,
    vendor: u16,
    product: u16,
    version: u16,
}

#[repr(C)]
struct UinputSetup {
    id: InputId,
    name: [u8; 80],
    ff_effects_max: u32,
}

#[derive(Debug)]
pub struct Keyboard {
    file: File,
}

impl Keyboard {
    /// The compositor takes a moment to notice a new device, so this is made
    /// at start, not at the first press.
    pub fn create(codes: &[u16]) -> io::Result<Self> {
        let file = OpenOptions::new().write(true).open("/dev/uinput")?;
        let fd = file.as_raw_fd();
        ioctl(fd, UI_SET_EVBIT, libc::c_int::from(EV_KEY))?;
        ioctl(fd, UI_SET_EVBIT, libc::c_int::from(EV_SYN))?;
        for &code in codes {
            ioctl(fd, UI_SET_KEYBIT, libc::c_int::from(code))?;
        }
        let mut setup = UinputSetup {
            id: InputId {
                bustype: 0x06,
                vendor: 0x6744,
                product: 0x5354,
                version: 1,
            },
            name: [0; 80],
            ff_effects_max: 0,
        };
        let name = b"gotg overlay keyboard";
        setup.name[..name.len()].copy_from_slice(name);
        // SAFETY: the setup struct is laid out as uinput's, and lives through the call.
        let rc = unsafe { libc::ioctl(fd, UI_DEV_SETUP, &setup as *const UinputSetup) };
        if rc < 0 {
            return Err(io::Error::last_os_error());
        }
        ioctl(fd, UI_DEV_CREATE, 0)?;
        Ok(Self { file })
    }

    /// Down in order, up in reverse. Whatever went down comes up again even
    /// when a later write fails: a Shift left held would reach everything
    /// focused after the game.
    pub fn press(&self, chord: &[u16]) -> io::Result<()> {
        let mut result = Ok(());
        let mut pressed = 0;
        for &code in chord {
            if let Err(error) = self.key(code, 1) {
                result = Err(error);
                break;
            }
            pressed += 1;
        }
        std::thread::sleep(Duration::from_millis(HELD_MS));
        for &code in chord[..pressed].iter().rev() {
            let up = self.key(code, 0);
            if result.is_ok() {
                result = up;
            }
        }
        result
    }

    fn key(&self, code: u16, value: i32) -> io::Result<()> {
        self.event(EV_KEY, code, value)?;
        self.event(EV_SYN, 0, 0)
    }

    fn event(&self, kind: u16, code: u16, value: i32) -> io::Result<()> {
        let event = libc::input_event {
            time: libc::timeval {
                tv_sec: 0,
                tv_usec: 0,
            },
            type_: kind,
            code,
            value,
        };
        // SAFETY: a plain-data struct viewed as its bytes, for the write.
        let bytes = unsafe {
            std::slice::from_raw_parts(
                (&event as *const libc::input_event).cast::<u8>(),
                std::mem::size_of::<libc::input_event>(),
            )
        };
        let written = {
            use std::io::Write;
            (&self.file).write(bytes)?
        };
        if written != bytes.len() {
            return Err(io::Error::other("short write to uinput"));
        }
        Ok(())
    }
}

impl Drop for Keyboard {
    fn drop(&mut self) {
        let _ = ioctl(self.file.as_raw_fd(), UI_DEV_DESTROY, 0);
    }
}

fn ioctl(fd: libc::c_int, request: libc::c_ulong, arg: libc::c_int) -> io::Result<()> {
    // SAFETY: an ioctl on a file we hold open, with an integer argument.
    let rc = unsafe { libc::ioctl(fd, request as _, arg) };
    if rc < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}
