//! Everything that listens to danstick's socket, as one bundle: who is
//! joining (`pairing`), the rebind walk and the seat list (`rebind`), the
//! standing request for seating (`seating`), the menu owner's controls
//! (`focused`), every seated pad's own controls (`native`), and the pads that
//! went away (`departures`). The bundle is the seam between "the socket
//! said" and "the loop decides". What to do when the connection comes and
//! goes is here, taking the link as a closure so it is tested with none.

use crate::departures::Departures;
use crate::menu::Focused;
use crate::native::Native;
use crate::padlink::Link;
use crate::pairing::Pairing;
use crate::rebind::Rebind;
use crate::seating::Seating;

#[derive(Debug)]
pub struct Listeners {
    pub pairing: Pairing,
    pub rebind: Rebind,
    pub seating: Seating,
    pub focused: Focused,
    pub native: Native,
    pub departures: Departures,
}

impl Listeners {
    /// `pair_hold` is how long danstick was asked to make a hold take, in
    /// seconds: the pairing fill and the `seating` request both carry it.
    pub fn new(pair_hold: f64) -> Self {
        Self {
            pairing: Pairing::new(pair_hold),
            rebind: Rebind::default(),
            seating: Seating::new(pair_hold),
            focused: Focused::default(),
            native: Native::default(),
            departures: Departures::default(),
        }
    }

    /// Whatever danstick has said since the last look, applied to each.
    pub fn pump(&mut self, link: &mut Link, now: f64) {
        link.pump(
            &mut self.pairing,
            &mut self.rebind,
            &mut [
                &mut self.seating,
                &mut self.focused,
                &mut self.native,
                &mut self.departures,
            ],
            now,
        );
    }

    /// After a pump: with no connection, everything that remembered the last
    /// one forgets it; with one, `seating` is asked for if it needs asking,
    /// and a line that could not be sent is asked for again next time.
    pub fn settle(&mut self, linked: bool, send: impl FnOnce(&str) -> bool) {
        if !linked {
            self.pairing.room(None);
            self.seating.lost();
            self.native.lost();
        } else if let Some(line) = self.seating.wanted()
            && !send(&line)
        {
            self.seating.lost();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn seating_is_asked_once_and_again_when_the_line_did_not_go() {
        let mut listeners = Listeners::new(1.5);
        let mut sent = Vec::new();
        listeners.settle(true, |line| {
            sent.push(line.to_string());
            false
        });
        assert_eq!(sent.len(), 1, "asked");
        listeners.settle(true, |line| {
            sent.push(line.to_string());
            true
        });
        assert_eq!(sent.len(), 2, "asked again, the first never reached danstick");
        listeners.settle(true, |_| panic!("asked a third time"));
    }

    #[test]
    fn a_new_connection_is_asked_for_seating_again() {
        let mut listeners = Listeners::new(1.5);
        listeners.settle(true, |_| true);
        listeners.settle(false, |_| panic!("no connection to send on"));
        let mut asked = false;
        listeners.settle(true, |line| {
            asked = line.contains("\"seating\"");
            true
        });
        assert!(asked, "the lease is per connection");
    }
}
