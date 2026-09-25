# Download progress for a game of several files

*Researched 2026-09-25. What `progress` lines say while `gotg install` runs
under the picker (src/client/lib/download.sh), and why.*

A game can be many files -- a Wii U dump, a Switch title with its update and
DLC, a scene set. Each file used to report itself, so the loader's bar filled
and emptied once per file with that file's figures. What established tools and
the platform guidance agree on:

- **One bar, the whole operation, total known before the first byte.** apt
  prints "Need to get X/Y" before it starts
  ([private-install.cc](https://raw.githubusercontent.com/Debian/apt/main/apt-private/private-install.cc));
  rsync's `--info=progress2` rebases each file onto the whole transfer
  ([progress.c](https://raw.githubusercontent.com/RsyncProject/rsync/master/progress.c));
  Material says to "indicate overall progress rather than the progress of each
  activity" ([MDC](https://github.com/material-components/material-components-android/blob/master/docs/components/ProgressIndicator.md));
  Docker's bar per layer is the standing complaint
  ([moby#4022](https://github.com/moby/moby/issues/4022)).
- **Never restart, never go backwards.** "Don't restart progress", "always
  increase progress monotonically"
  ([Windows UX guide](https://learn.microsoft.com/en-us/windows/win32/uxguide/progress-bars)).
- **Bytes already on disk count from the start.** wget draws resumed bytes as
  part of the bar ([progress.c](https://raw.githubusercontent.com/mirror/wget/master/src/progress.c));
  apt counts what is already present.
- **Rate from this run's bytes only, over a window.** rsync keeps five seconds
  of samples, wget at least three and clears them after a five-second stall;
  time left is what remains over that rate. Hold the estimate back until it
  has settled (Windows guide).
- **Which file is secondary, and a count, not a name**: "file 3 of 7"
  ([NN/g](https://www.nngroup.com/articles/progress-indicators/)).
- **Unknown size: an indeterminate bar**, determinate once it is known
  ([Apple HIG](https://developer.apple.com/design/human-interface-guidelines/progress-indicators)).

## What GOTG does

`_fetch_members` sums the catalog's `size_bytes` for every file it is about to
fetch and keeps a running account (`_AGG_*`):

    progress <done> <total> <rate> <what>

- `total`: the game's bytes, the same on every line.
- `done`: bytes of finished files + bytes of the current file on disk
  (a resumed partial included), clamped to `total`, never lower than before.
- `rate`: bytes this run moved, over the last 5 s; `0` for the first 3 s,
  which the loader shows as no speed and no time left.
- `what`: `Title — file 3 of 7`, or just the title for one file.

A file copied from a mounted library counts as done and moved nothing.
