//! While the game runs, Ctrl+C and the console window's close are the
//! game's: gotg.exe outlives them to push the saves. A handler routine, not
//! `SetConsoleCtrlHandler(NULL, TRUE)`, whose "ignore" children inherit.

/// Held for the game and the push after it.
#[derive(Debug)]
pub struct Shield(());

#[cfg(windows)]
mod imp {
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::time::{Duration, Instant};

    const CTRL_C_EVENT: u32 = 0;
    const CTRL_BREAK_EVENT: u32 = 1;
    const CTRL_CLOSE_EVENT: u32 = 2;
    const CTRL_LOGOFF_EVENT: u32 = 5;
    const CTRL_SHUTDOWN_EVENT: u32 = 6;

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn SetConsoleCtrlHandler(handler: Option<unsafe extern "system" fn(u32) -> i32>, add: i32) -> i32;
    }

    /// Set once the push after the game is done.
    pub static DONE: AtomicBool = AtomicBool::new(false);

    unsafe extern "system" fn handler(event: u32) -> i32 {
        match event {
            CTRL_C_EVENT | CTRL_BREAK_EVENT => 1,
            // Windows ends the process when this returns (or after ~5 s):
            // holding it is the push's only chance to finish.
            CTRL_CLOSE_EVENT | CTRL_LOGOFF_EVENT | CTRL_SHUTDOWN_EVENT => {
                let start = Instant::now();
                while !DONE.load(Ordering::SeqCst) && start.elapsed() < Duration::from_secs(20) {
                    std::thread::sleep(Duration::from_millis(50));
                }
                1
            }
            _ => 0,
        }
    }

    pub fn install(on: bool) {
        // SAFETY: `handler` is a 'static function with the HandlerRoutine signature.
        unsafe {
            SetConsoleCtrlHandler(Some(handler), i32::from(on));
        }
    }
}

impl Shield {
    pub fn up() -> Shield {
        #[cfg(windows)]
        imp::install(true);
        Shield(())
    }
}

impl Drop for Shield {
    fn drop(&mut self) {
        #[cfg(windows)]
        {
            imp::DONE.store(true, std::sync::atomic::Ordering::SeqCst);
            imp::install(false);
        }
    }
}
