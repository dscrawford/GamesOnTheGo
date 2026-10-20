# Forget one control's binding, or one scope's walk, and bind under a named layout

## What GOTG wants

The overlay's menu now has a cursor over the game's controller. On a button, A
walks that button alone (`bind`), Y adds a second input to it (`bind` with
`add`), and **X resets it**: the binding goes, and the pad falls through to
whatever it had before -- the console's walk, the default, its own layout. On
the whole drawing, X resets the whole walk under this scope the same way. The
menu stays down, the next step is whatever the player does next.

Scopes are the console's (`console:n64`) for a console's game, and the game's
own (`game:<key>`) for a decompiled port played as the generic pad. The
overlay leases both in `scope` now (`"console"` and `"game"`), so a bind filed
under the game is the walk the clone is driven from.

## Why the current shape does not reach it

`forget_pad` is the only forget there is. It drops **every** scope the pad has,
needs a session, and opens the layout picker -- a screen the overlay has no
room for, over a game that is still running. `bind` can replace a control's
input or add one; nothing takes one away. So the menu can offer "rebind" and
"add", and must either hide "reset" or lie about it.

One more seam, found on the way. `bind` picks its layout from the walk in play
(`mapping_in_play`, else the pad's icon's layout) and files the result under
that layout's id. A pad that has never walked N64, told to bind its `a` under
`console:n64`, gets the bind filed under its icon's layout -- where the N64
console walk is never looked up -- or an `error` that the icon's layout has no
such control. `map` takes `layout`; `bind` does not.

## What would be enough

```json
{"cmd": "forget", "player": 2, "scope": "console:n64", "control": "a"}
{"cmd": "forget", "player": 2, "scope": "game:env-n64-usa_donkey_kong_64"}
{"cmd": "bind", "player": 2, "control": "a", "layout": "n64", "scope": "console:n64"}
```

- **With `control`**: that control's stored binding under `scope` goes, second
  inputs included. The walk is otherwise untouched; the control resolves as it
  would with nothing stored there (the next scope down, then the pad's own).
- **Without it**: the pad's whole entry for `scope` goes.
- **Legal with no session open**, like `map` and `bind`: it touches one
  player's stored profile and no pad is grabbed.
- The clone is re-driven and the consumers' files rewritten, as after a
  `bind` that stores (`EVENTS.md`, "A `map` or `bind` that stores changes
  that player's clone").
- **No picker.** Answered by `state`; an `error` for an unseated player, a
  control the layout does not have, or nothing stored under that scope. A
  daemon too old to know it says `unknown command "forget"`, which the overlay
  already reads as "no reset here".
- **`bind` takes `layout`** the way `map` does, and files the bind under it.
  The overlay sends it from the start (`rebind.rs` `bind`); a daemon that
  ignores the field keeps today's behaviour.

## How GOTG will check it

A pad walked for N64: open the menu over an N64 game, move the cursor to A,
press X -- A on the pad is back to what the pad's own layout says, every other
button as walked. X on the whole drawing -- the pad is driven as it was before
the N64 walk. On a pad that has never walked N64, cursor to A, tap A, press a
button: `state` carries the N64 scope and the clone's A is that button.
