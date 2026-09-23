//! Asking SDL: every call in this program that is not a plain decision.

use std::ffi::c_int;

use sdl3_sys::everything::*;
use serde_json::{Map, Value, json};

use crate::{Pad, guid_text, is_steam_virtual, owned};

/// Every joystick SDL can see, the way an emulator started now would see it.
pub fn enumerate() -> Result<Vec<Pad>, String> {
    // SAFETY: every SDL call below is made from this one thread, between
    // SDL_Init and SDL_Quit, with pointers SDL returned or buffers we own.
    unsafe {
        // See what the emulators see. The current Steam Controller is a hidapi
        // device whose SDL3 driver falls back to SDL_HINT_JOYSTICK_HIDAPI, so
        // without this it is invisible here while being visible in a game —
        // the most confusing possible disagreement.
        SDL_SetHint(SDL_HINT_JOYSTICK_HIDAPI_STEAM, c"1".as_ptr());
        if !SDL_Init(SDL_INIT_JOYSTICK | SDL_INIT_GAMEPAD) {
            return Err(format!("SDL_Init: {}", error()));
        }
        // hidapi devices are opened on a background thread, so a bare
        // enumeration straight after init can miss one that is plugged in.
        for _ in 0..10 {
            SDL_UpdateJoysticks();
            SDL_PumpEvents();
            SDL_Delay(50);
        }

        let mut count: c_int = 0;
        let ids = SDL_GetJoysticks(&mut count);
        if ids.is_null() {
            let message = format!("SDL_GetJoysticks: {}", error());
            SDL_Quit();
            return Err(message);
        }
        let pads = (0..usize::try_from(count).unwrap_or(0))
            .map(|i| describe(*ids.add(i)))
            .collect();
        SDL_free(ids.cast());
        SDL_Quit();
        Ok(pads)
    }
}

/// SDL_GetError, copied.
///
/// # Safety
/// SDL must be loaded; any thread may ask.
unsafe fn error() -> String {
    // SAFETY: SDL_GetError never returns null, and the caller is on SDL.
    unsafe { owned(SDL_GetError()) }.unwrap_or_default()
}

/// One joystick, and its gamepad answers when it is one.
///
/// # Safety
/// SDL is initialised, and `id` came from this enumeration.
unsafe fn describe(id: SDL_JoystickID) -> Pad {
    // SAFETY: per this function's contract; every pointer is SDL's own.
    unsafe {
        let vid = SDL_GetJoystickVendorForID(id);
        let pid = SDL_GetJoystickProductForID(id);
        let gamepad = SDL_IsGamepad(id);
        // Opened once for both answers: a hidapi pad's open is a device
        // transaction, and doing it twice per pad raced the background thread
        // that brings those devices up.
        let pad = if gamepad {
            SDL_OpenGamepad(id)
        } else {
            std::ptr::null_mut()
        };
        let motion = !pad.is_null() && has_motion(pad);
        let map = if pad.is_null() { None } else { bindings(pad) };
        if !pad.is_null() {
            SDL_CloseGamepad(pad);
        }
        Pad {
            instance: id.0,
            name: owned(SDL_GetJoystickNameForID(id)),
            guid: guid_text(SDL_GetJoystickGUIDForID(id)),
            vid,
            pid,
            gamepad,
            path: owned(SDL_GetJoystickPathForID(id)),
            steam_slot: is_steam_virtual(vid, pid)
                .then(|| SDL_GetJoystickPlayerIndexForID(id))
                .filter(|slot| *slot >= 0),
            motion,
            map,
        }
    }
}

/// Whether this pad can drive an emulator's motion controls.
///
/// Both sensors, not just the gyro: Ryujinx and Cemu each require the pair
/// before they will offer motion at all, so asking their question is what
/// keeps this from promising motion the emulator then declines to use.
///
/// # Safety
/// `pad` is an open gamepad.
unsafe fn has_motion(pad: *mut SDL_Gamepad) -> bool {
    // SAFETY: per this function's contract.
    unsafe { SDL_GamepadHasSensor(pad, SDL_SENSOR_GYRO) && SDL_GamepadHasSensor(pad, SDL_SENSOR_ACCEL) }
}

/// Which raw joystick element drives each standard gamepad element.
///
/// This is the piece that makes generated bindings possible at all. An
/// emulator binds raw indices — "button 6" — while a person thinks in "Start",
/// and the two only line up per controller model. SDL already knows the
/// correspondence for anything in its mapping database, so ask it rather than
/// asking somebody to press every button in an emulator's settings screen.
///
/// # Safety
/// `pad` is an open gamepad.
unsafe fn bindings(pad: *mut SDL_Gamepad) -> Option<Map<String, Value>> {
    // SAFETY: per this function's contract; the array is SDL's until freed,
    // and each binding's union is read as the member its type tag names.
    unsafe {
        let mut count: c_int = 0;
        let binds = SDL_GetGamepadBindings(pad, &mut count);
        if binds.is_null() {
            return None;
        }
        let mut map = Map::new();
        for i in 0..usize::try_from(count).unwrap_or(0) {
            let bind = &**binds.add(i);
            // Only the standard elements a person names: an unmapped output is
            // of no use to a generator.
            let out = match bind.output_type {
                SDL_GAMEPAD_BINDTYPE_BUTTON => owned(SDL_GetGamepadStringForButton(bind.output.button)),
                SDL_GAMEPAD_BINDTYPE_AXIS => owned(SDL_GetGamepadStringForAxis(bind.output.axis.axis)),
                _ => None,
            };
            let Some(out) = out else { continue };
            let input = match bind.input_type {
                SDL_GAMEPAD_BINDTYPE_BUTTON => json!({"type": "button", "index": bind.input.button}),
                // min/max carry the direction: a trigger runs one way, a stick both.
                SDL_GAMEPAD_BINDTYPE_AXIS => json!({
                    "type": "axis",
                    "index": bind.input.axis.axis,
                    "min": bind.input.axis.axis_min,
                    "max": bind.input.axis.axis_max,
                }),
                SDL_GAMEPAD_BINDTYPE_HAT => json!({
                    "type": "hat",
                    "index": bind.input.hat.hat,
                    "mask": bind.input.hat.hat_mask,
                }),
                _ => Value::Null,
            };
            map.insert(out, input);
        }
        SDL_free(binds.cast());
        Some(map)
    }
}
