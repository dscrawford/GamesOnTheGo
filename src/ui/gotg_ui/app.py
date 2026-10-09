"""The picker: the one loop that draws the library and reads the controllers.

Kept thin on purpose. Which games exist, where the tiles go, what a menu
offers, what a press means, which screen owns the input (screens.py) and what
a pick, the chip, a restart or a finished loader should do (plans.py) are all
answerable without a display, and live in the modules imported below as pure
models. Drawing is draw_grid.py, draw_screens.py and saves_draw.py; what is
left here is `run()`: read the events, hand each to the handler of the screen
that owns input, apply what the models decided, paint that screen, present.

The screens, all drawn over one scaled window (display.py): the grid and the
shelf (browser.py) with cover art decoded off the loop; the per-game menu;
the filters panel; the storage, saves and prepare screens; and the "Update
available" chip at the top right. Input is the controller requirement from
CLAUDE.md -- pads danstick has published, the keyboard once it has a seat,
nothing else -- with the mouse as the way out of an unpaired window.

`run()` returns the choice rather than launching it; `__main__` execs it once
the display is given back.
"""

from __future__ import annotations

import gc
import os
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor

import pygame

from . import beside, config, display, filters, intents, keys, meter, pads, plans, remembered, trace, updates
from .art import ArtStore
from .badges import Asker, Badges, land
from .browser import INSTALLED, SHELF, Browser
from .buttons import step_for
from .catalog import Game, Library
from .chip import ChipState
from .danstick import DaemonWatch, Danstick, ensure_daemon
from .decode import PENDING, Decoder
from .draw_grid import draw, draw_menu, draw_shelf, hovering, menu_rects, view_rects
from .draw_keyboard import draw_keyboard, key_at
from .draw_screens import draw_filters, draw_prepare, draw_saves_choice, draw_storage
from .fetch import Loader
from .filters import Filters
from .gridview import GridView
from .hush import Hush
from .installed import installed_games
from .installs import Installs
from .intents import BACK, Intent, keyboard_heard
from .keyboard import Keyboard
from .keys import KeyHold
from .menu import Menu
from .nav import Nav
from .prepare import Preparer, is_ready
from .restart import Restart, usable
from .saves_choice import Choice
from .saves_choice import check as check_saves
from .saves_draw import draw_saves
from .saves_list import Saves, has_own_saves
from .saves_list import fetch as fetch_saves
from .screens import Screen, grid_press, screen_of
from .storage import Storage
from .texting import CANCEL, COMMIT, EDITED, edit
from .theme import WINDOW
from .variants import variants_for
from .versions import forget as forget_versions
from .versions import names as version_names
from .versions import versions_for

# intents.py writes SDL's keycodes down so that it needs no pygame; this is
# where the two are made to agree, as pads.py does for the button numbering.
assert intents.Key.ESCAPE == pygame.K_ESCAPE and intents.Key.B == pygame.K_b
assert intents.Key.RETURN == pygame.K_RETURN and intents.Key.KP_ENTER == pygame.K_KP_ENTER
assert intents.Key.SPACE == pygame.K_SPACE and intents.Key.BACKSPACE == pygame.K_BACKSPACE
assert intents.Key.UP == pygame.K_UP and intents.Key.DOWN == pygame.K_DOWN
assert intents.Key.LEFT == pygame.K_LEFT and intents.Key.RIGHT == pygame.K_RIGHT


def _intent(event) -> Intent:
    """What this event means to a screen that reads Back, OK and a step.

    The pad's button is read once and its direction taken from it, rather than
    asking `pads.direction` and `pads.button` in turn -- each traces the press,
    and the screens that asked both wrote it down twice. A pad danstick has not
    published gives None for both and so means nothing (`pads.py`).
    """
    return _heard(event)[0]


def _heard(event) -> tuple[Intent, str | None]:
    """`_intent`, and the pad button it was read from, for the screens that
    also act on a button the intent has no word for (storage's X and Y, the
    panel's Start) -- read once here, since `pads.button` traces each read
    and a press was landing in the trace twice."""
    if event.type == pygame.KEYDOWN:
        return intents.intent(key=event.key), None
    if event.type == pygame.JOYHATMOTION:
        return intents.intent(step=pads.direction(event)), None
    button = pads.button(event)
    return intents.intent(button=button, step=step_for(button)), button


def _steered(steer: Nav, event, now: float) -> Nav:
    """A held direction's start and end, from a pad danstick published. The
    press itself is the screens' to act on; a stick has no press, so its first
    step is posted here, as the d-pad press it stands for."""
    pad = pads.pad_of(event)
    if pad is None:
        return steer
    moved = pads.axis_move(event)
    if moved is not None:
        steer, step = steer.stick(pad, *moved, now)
        if step is not None:
            pads.repeat_press(pad, step)
        return steer
    down = pads.pushed(event)
    if down is not None:
        return steer.press(pad, down, now)
    up = pads.let_go(event)
    if up is not None:
        return steer.release(pad, up)
    return steer


def run(library: Library, installed_only: bool = False) -> tuple | Restart | None:
    """Run the picker until somebody chooses an action or quits, and say which.

    Returns (game, verb) -- play or configure -- both of which the caller
    execs; or a Restart, after `gotg update self`, which the caller execs too:
    this pid into the new picker (restart.py); or None for a quit.
    `installed_only` shows only what is on disk.

    The game is *returned* rather than launched here: exec has to happen after
    pygame has given the display back, or the emulator inherits a window and a
    grabbed GPU from a process that is about to stop existing.
    """
    pygame.init()
    # One window for the whole program: full screen when the launcher says so,
    # scaled from one layout size, presented on the panel's own beat -- see
    # display.py for why that last part is the one that mattered.
    shown = display.open(WINDOW, fullscreen=config.fullscreen())
    screen = shown.surface
    if meter.wanted():
        print(f"gotg-ui: display {shown.info}", file=sys.stderr)
    # Held open for as long as the picker runs: a pad that goes out of scope is
    # closed by SDL, and a closed one stops producing events. Opened through
    # the controller API, which is what makes "the right bumper" mean the same
    # button on every pad rather than index 5 on an Xbox one.
    sticks = pads.init()
    # Where a frame's time goes. Silent unless GOTG_UI_FPS=1 or a trace is
    # running: "it feels slow" is two different problems -- this program
    # drawing, and SDL presenting what it drew -- and they are fixed in
    # different places. See meter.py.
    fps = meter.Meter()

    # And every keyboard and mouse that is really a controller -- a Steam
    # Controller's lizard mode, a Bluetooth Xbox pad's extra collections --
    # held so the compositor never sees them. The joystick rule cannot reach
    # those: to SDL a lizard-mode d-pad *is* the arrow keys. See hush.py.
    hush = Hush()
    hush.refresh()

    fonts: dict[int, pygame.font.Font] = {}

    def font_at(size: int) -> pygame.font.Font:
        if size not in fonts:
            fonts[size] = pygame.font.Font(None, size)
        return fonts[size]

    # Asked once, up front, and again only after an uninstall: the answer is
    # a walk of the whole catalog against the disk, not a per-frame question.
    browser = Browser(library, installed=installed_games())
    # Where it was left last time (remembered.py), kept up to date below.
    kept = remembered.Remembered.open()
    remembered.apply(browser, kept.last)

    # What is out of date, from the client's cache -- asked on a worker, as
    # every later refresh is: the answer is two subprocesses, a quarter of a
    # second on a desk and more on a Deck, which is frames the grid would
    # otherwise drop (CLAUDE.md's budget is 16.7 ms). The first answer lands
    # a moment after the first frame; and once the client has answered (so
    # it knows the question), the check that refreshes its cache runs
    # behind the grid. See badges.py.
    learned = Badges()
    asker = Asker()
    asker.refresh()
    update_check: updates.Check | None = None
    # The chip: what the report says, or the loop's own word for a while
    # after a press (chip.py). It also remembers where it was drawn, for a click.
    chip = ChipState()
    restart: Restart | None = None

    def apply_badges() -> None:
        nonlocal learned, update_check
        answer = asker.poll()
        if answer is None:
            return
        landed = land(learned, *answer)
        learned = landed.badges
        browser.set_installed(landed.installed)
        browser.set_outdated(landed.outdated)
        if landed.start_check:
            update_check = updates.Check.start_after(learned.report)

    def say_chip(phase: str | None) -> None:
        nonlocal chip
        chip = chip.say(phase, time.monotonic())

    def self_update() -> None:
        """The chip pressed (plans.plan_self_update says what that means)."""
        nonlocal preparer, after_prepare, prepare_failed
        plan = plans.plan_self_update(
            learned.report,
            updates.self_root(),
            chip=chip,
            now=time.monotonic(),
            busy=bool(installs.keys) or preparer is not None,
        )
        if plan.action == plans.SAY:
            say_chip(plan.chip)
        elif plan.action == plans.RESTART:
            finish_restart(plan.root)
        elif plan.action == plans.UPDATING:
            trace.say("self-update")
            preparer = Preparer(None, ["update", "self"])
            after_prepare = "self-update"
            prepare_failed = False
            say_chip(plan.chip)

    def finish_restart(root: str) -> None:
        """The new picker is at `root`: become it (plans.plan_restart)."""
        nonlocal restart, running
        plan = plans.plan_restart(root, updates.self_root(), usable)
        if plan.action == plans.NOOP:
            say_chip(plan.chip)
            asker.refresh()
        elif plan.action == plans.REFUSE:
            trace.say("restart-refused", root=root)
            say_chip(plan.chip)
        else:
            trace.say("restart", root=root)
            restart = Restart(root)
            running = False

    if installed_only:
        browser.set_presence(INSTALLED)
    store = ArtStore()
    loader = Loader(store)
    # Decoded surfaces, keyed by (platform, id). Decoding is not free and the
    # same ten tiles are redrawn sixty times a second.
    art: dict[tuple[str, str], object] = {}
    # And decoded off the frame thread: ten new covers in the frame that
    # first drew them was 13.5 ms, a page scrolling into view with a hitch in
    # it. See decode.py.
    decoder = Decoder(display.image)

    def surface_for(game):
        """A decoded picture for one game, asking the loader if need be.

        None until it is decoded, which draws the title -- the same thing a
        tile shows while its picture is still downloading.
        """
        if game.key in art:
            return art[game.key]
        path = loader.want(game)
        if path is None:
            return None
        picture = decoder.want(game.key, path)
        if picture is PENDING:
            return None
        if picture is None:
            # A truncated or unreadable file: drop it so a refresh can
            # replace it, and draw the title this time round.
            store.forget(game)
        art[game.key] = picture
        return picture

    chosen: tuple[Game, str, str | None, str | None] | None = None
    # The keyboard on screen while a search is typed (keyboard.py).
    typing: Keyboard | None = None
    typing_from = ""  # the search before typing began, for Escape
    menu: Menu | None = None
    # The filter panel, while it is open. None is the grid.
    panel: Filters | None = None
    # The controller layer. Absent is a state rather than a failure: a machine
    # with no daemon running is what every machine looks like before anybody
    # has set a controller up.
    danstick = Danstick()
    # Started rather than waited for: the picker is usually the first thing
    # open on this machine, so if it does not start the daemon nothing will.
    # A failure is a line in the trace, not a reason to refuse to draw.
    # No session, ever, from the grid. danstick opens one by itself the first
    # time it meets a pad it has no mapping for -- which grabs every
    # controller, takes the screen, and is exactly the pairing detour this
    # picker is supposed to have stopped needing. A seat comes from a hold,
    # and what a button means is asked over the game, by the overlay. Set
    # before the daemon is started, since it is the daemon that reads it, and
    # left alone when somebody set it themselves.
    os.environ.setdefault("DANSTICK_NO_AUTOSETUP", "1")
    # And no seat but by a hold. danstick seats a pad it has a stored mapping
    # for the moment it sees it, which --fresh does not stop: the Xbox pad
    # was player one two seconds after the grid opened, nobody had held
    # anything. Seen in a trace, not reasoned about.
    os.environ.setdefault("DANSTICK_NO_AUTOATTACH", "1")
    # Unseated, and for as long as this process lives -- which, after a pick
    # execvp's into a game, is the game. Nobody is seated when the picker
    # opens; a hold seats them; the daemon goes when the session does.
    danstick_trouble = ensure_daemon(fresh=True, follow=os.getpid())
    trace.say(
        "start",
        pid=os.getpid(),
        danstick_on_path=bool(__import__("shutil").which("danstick")),
        daemon_trouble=danstick_trouble,
        any_pad=os.environ.get("GOTG_ANY_PAD"),
        pads_open=len(sticks),
        held=hush.refresh(),
    )
    danstick.connect()
    # The overlay's bar over this window, as over a game: see beside.py. It is
    # what keeps danstick listening for a hold, and what shows somebody joining.
    overlay = beside.start(os.getpid())
    # And asked after again whenever the connection is gone -- see DaemonWatch
    # for why reconnecting alone was not enough.
    danstick_watch = DaemonWatch()
    # Where that asking happens, so the grid keeps drawing while it does.
    restarter = ThreadPoolExecutor(max_workers=1, thread_name_prefix="danstick-start")
    # Before a game starts, which save it starts with: the client is asked on
    # a worker (it bundles and hashes the saves), and a conflict opens a
    # choice between the two. See saves_choice.py.
    saves_asker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="saves-check")
    checking: Future | None = None
    starting: tuple | None = None  # (game, verb, variant, version) waiting on the check
    choice: Choice | None = None
    # The Saves screen a game's menu opens, and its list on the same worker:
    # the client bundles and hashes to say which save is the one here.
    saves: Saves | None = None
    listing: Future | None = None
    restarting: Future | None = None
    # The space bar: tapped it opens the menu as it always did; held, danstick
    # seats the keyboard and the release is nothing. Decided on release.
    space = KeyHold()
    # The storage screen, and the path being typed to add to it.
    storage: Storage | None = None
    storage_typing: str | None = None
    # The loader phase: a verb that needs work spawns the client and the grid
    # gives way to its output until it finishes, fails, or is cancelled.
    preparer: Preparer | None = None
    installs = Installs()
    prepare_failed = False
    # What happens when the loader succeeds: exec the verb, or come back here.
    after_prepare: str | None = None
    # The game a restore was for, to start once it is done.
    restoring: tuple | None = None
    running = True

    def start(game: Game, verb: str, variant: str | None, version: str | None) -> None:
        """Hand the game to the client -- after asking which save it starts
        with, for a play."""
        nonlocal chosen, running, checking, starting
        if verb != "play":
            chosen = (game, verb, variant, version)
            running = False
            return
        starting = (game, verb, variant, version)
        checking = saves_asker.submit(check_saves, game, variant)

    def menu_for(game: Game) -> Menu:
        variants = variants_for(game)
        return Menu(
            game,
            state.selected,
            browser.is_installed(game),
            variants,
            version_names(versions_for(game)),
            columns=browser.columns,
            installing=installs.running(game.key),
            saves=frozenset(v for v in (None, *variants) if has_own_saves(game, v)),
            outdated=game.key in browser.outdated,
        )

    def pick(game: Game | None, verb: str = "play", variant: str | None = None, version: str | None = None) -> None:
        """A verb chosen in a game's menu: plans.plan_pick says what it means,
        this carries it out."""
        nonlocal preparer, after_prepare, storage, saves, listing
        plan = plans.plan_pick(
            game,
            verb,
            variant,
            version,
            installing=game is not None and installs.running(game.key),
            ready=lambda: is_ready(game, variant),
        )
        if plan.sets_after:
            after_prepare = plan.after
        if plan.action == plans.OPEN_SAVES:
            saves = Saves.open(game, variant, version)
            listing = saves_asker.submit(fetch_saves, game, variant)
        elif plan.action == plans.OPEN_STORAGE:
            storage = Storage()
            storage.refresh()
        elif plan.action == plans.PREPARE:
            args = list(plan.args) if plan.args is not None else None
            preparer = Preparer(game, args, plan.variant, plan.version)
        elif plan.action == plans.INSTALL:
            installs.start(game, plan.variant, plan.version)
        elif plan.action == plans.UPDATE:
            installs.start(game, verb="update")
        elif plan.action == plans.CANCEL_INSTALL:
            installs.cancel(game.key)
        elif plan.action == plans.ADOPT:
            preparer = installs.take(game.key)
        elif plan.action == plans.START:
            start(game, verb, variant, version)

    # `screen_of` says which owns the input (screens.py); each handler below is
    # that screen's.

    def current_screen() -> Screen:
        return screen_of(
            preparing=preparer is not None,
            saves_check=checking is not None or choice is not None,
            saves=saves is not None,
            storage=storage is not None,
            menu=menu is not None,
            panel=panel is not None,
            typing=typing is not None,
        )

    def on_prepare(event) -> None:
        # On the loader, the only input is the way out. Everything else
        # would be the grid moving invisibly behind the build.
        nonlocal preparer, prepare_failed
        if _intent(event) == BACK:
            if not prepare_failed:
                preparer.cancel()
            preparer = None
            prepare_failed = False

    def on_saves_check(event) -> None:
        # Checking the saves, or choosing between two: A, B and a direction,
        # nothing else.
        nonlocal checking, choice, starting, preparer, after_prepare
        said = _intent(event)
        if said == BACK:
            checking, choice, starting = None, None, None
            return
        if choice is None:
            return
        if said.dx:
            choice = choice.move(said.dx)
        elif said.confirms():
            # Through the loader, which shows what the client says as it
            # pulls or pushes; the game starts when it is done.
            preparer = Preparer(choice.game, ["saves", "keep", choice.selected], choice.variant)
            after_prepare = "kept"
            choice = None

    def on_saves(event) -> None:
        # The Saves screen: up and down, A twice to load, B to step back.
        nonlocal saves, listing, restoring, preparer, after_prepare
        said = _intent(event)
        if said == BACK:
            saves = saves.press_b()
            if saves is None:
                listing = None
            return
        if said.dy:
            saves = saves.move(said.dy)
        elif said.confirms():
            saves, entry = saves.press_a()
            if entry is not None:
                # Through the loader, which shows the client archiving and
                # restoring; the game starts after. No version: `restore`
                # takes none, and the play that follows gets it from
                # `restoring`.
                restoring = (saves.game, saves.variant, saves.version)
                preparer = Preparer(saves.game, ["saves", "restore", entry.id], saves.variant)
                after_prepare = "restored"
                saves, listing = None, None

    def on_storage(event) -> None:
        # The storage screen owns its input: a list to walk, three verbs, and
        # a path typed to add -- every key is text then.
        nonlocal storage, storage_typing
        if storage_typing is not None:
            if event.type != pygame.KEYDOWN:
                return
            text, outcome = edit(storage_typing, event.key, event.unicode)
            if outcome == CANCEL:
                storage_typing = None
            elif outcome == COMMIT:
                storage.add(text)
                storage_typing = None
            elif outcome == EDITED:
                storage_typing = text
            return
        said, pressed = _heard(event)
        if said == BACK:
            storage = None
        elif said.dy:
            storage.move(said.dy)
        elif said.confirms():
            storage.make_default()
        elif event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_DELETE, pygame.K_x):
                storage.remove()
            elif event.key in (pygame.K_PLUS, pygame.K_KP_PLUS, pygame.K_EQUALS, pygame.K_y):
                storage_typing = ""
        else:
            if pressed == pads.X:
                storage.remove()
            elif pressed == pads.Y:
                # A Deck raises the Steam keyboard over this.
                storage_typing = ""

    def confirm_menu() -> None:
        nonlocal menu
        verb = menu.confirm()
        if verb is not None:
            picked, menu = menu, None
            pick(picked.game, verb, picked.variant, picked.version)

    def menu_rows_at(pos) -> list[int]:
        rows = menu_rects(menu, view_rects(browser, screen.get_size()), font_at, screen.get_size())
        return [i for i, (rx, ry, rw, rh) in enumerate(rows) if rx <= pos[0] < rx + rw and ry <= pos[1] < ry + rh]

    def on_menu(event) -> None:
        # While the menu is open it owns the input: the grid must not move
        # invisibly underneath the panel.
        nonlocal menu
        said = _intent(event)
        if said == BACK:
            # Out of the variants first, then out of the menu: one button, one
            # step at a time.
            if menu.expanded:
                menu.close_list()
            else:
                menu = None
        elif said.dy:
            menu.move(said.dy)
        elif said.confirms(space=True):
            confirm_menu()
        elif event.type == pygame.MOUSEMOTION:
            for i in menu_rows_at(event.pos):
                menu.select(i)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            hits = menu_rows_at(event.pos)
            if not hits:
                menu = None  # a click that misses the panel closes it
            elif menu.select(hits[-1]):
                confirm_menu()

    def on_panel(event) -> None:
        # The filter panel, while it is up. Before the typing screen so that
        # opening the keyboard from it still works, and before the grid so the
        # cursor does not move behind it. An open list owns up, down, A and B;
        # the panel underneath owns them when there is none. One step back per
        # press of B: the list first, then the panel.
        nonlocal panel, typing, typing_from
        said, pressed = _heard(event)
        if said == BACK:
            if panel.open:
                panel.close()
            else:
                panel = None
        elif said.kind == "move":
            if said.dy:
                if panel.open:
                    panel.choice.move(said.dy)
                else:
                    panel.move(said.dy)
            if said.dx and not panel.open:
                panel.adjust(browser, said.dx)
        elif said.confirms(space=True):
            if panel.open:
                panel.choose(browser)
            elif panel.press(browser) == filters.TYPING:
                typing, typing_from = Keyboard(browser.search), browser.search
        elif pressed == pads.START:
            # Start closes it the way Start opened it.
            panel = None

    def press_key(board: Keyboard) -> None:
        nonlocal typing
        typing = None if board.closes else board.press()

    def on_typing(event) -> None:
        # The keyboard on screen owns the input: a pad walks its keys and a
        # real keyboard types straight into it. Nothing else runs, or the
        # letters of a search would also be moving the cursor.
        nonlocal typing
        if event.type == pygame.KEYDOWN:
            text, outcome = edit(typing.text, event.key, event.unicode)
            if outcome == CANCEL:
                # Cancelled: the grid goes back to the search it had.
                browser.set_search(typing_from)
                typing = None
            elif outcome == COMMIT:
                typing = None
            elif outcome == EDITED:
                typing = typing.with_text(text)
            elif (said := intents.intent(key=event.key)).kind == "move":
                typing = typing.move(said.dx, said.dy)
        elif event.type == pygame.MOUSEMOTION:
            over = key_at(typing, font_at, screen.get_size(), event.pos)
            if over is not None:
                typing = typing.at(*over)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            over = key_at(typing, font_at, screen.get_size(), event.pos)
            if over is not None:
                press_key(typing.at(*over))
        else:
            said, pressed = _heard(event)
            if said.kind == "move":
                typing = typing.move(said.dx, said.dy)
            elif said.confirms():
                press_key(typing)
            elif said == BACK or pressed == pads.START:
                typing = None
            elif pressed == pads.X:
                typing = typing.backspace()
            elif pressed == pads.Y:
                typing = typing.typed(" ")
        # Applied as it is typed: the grid narrowing under the letters is
        # the feedback that the letters went in.
        if typing is not None and typing.text != browser.search:
            browser.set_search(typing.text)

    def on_grid_key(event) -> None:
        nonlocal running, typing, typing_from, panel, storage, menu
        if event.key in (pygame.K_ESCAPE, pygame.K_q):
            running = False
        elif event.key in (pygame.K_SLASH, pygame.K_f):
            typing, typing_from = Keyboard(browser.search), browser.search
        elif event.key == pygame.K_TAB:
            # The panel: every filter in one place, both directions on each,
            # so a controller reaches all of them.
            panel = Filters()
        elif event.key == pygame.K_LEFT:
            state.move(-1, 0)
        elif event.key == pygame.K_RIGHT:
            state.move(1, 0)
        elif event.key == pygame.K_UP:
            state.move(0, -1)
        elif event.key == pygame.K_DOWN:
            state.move(0, 1)
        elif event.key == pygame.K_PAGEDOWN:
            state.turn(1)
        elif event.key == pygame.K_PAGEUP:
            state.turn(-1)
        elif event.key == pygame.K_SPACE:
            space.down(time.monotonic())
        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if state.game is not None:
                menu = menu_for(state.game)
        elif event.key == pygame.K_i:
            browser.toggle_installed()
        elif event.key == pygame.K_s:
            storage = Storage()
            storage.refresh()

    def on_grid_press(pressed: str) -> None:
        # By name, not by index (screens.grid_press).
        nonlocal running, typing, typing_from, panel, menu
        action = grid_press(pressed)
        if action == "menu":
            if state.game is not None:
                menu = menu_for(state.game)
        elif action == "quit":
            running = False
        elif action == "page-back":
            state.turn(-1)
        elif action == "page-forward":
            state.turn(1)
        elif action == "platform":
            browser.cycle_platform(1)
        elif action == "search":
            typing, typing_from = Keyboard(browser.search), browser.search
        elif action == "panel":
            panel = Filters()
        elif action == "update":
            self_update()

    def on_grid(event) -> None:
        nonlocal menu
        if event.type == pygame.KEYDOWN:
            on_grid_key(event)
        elif event.type == pygame.MOUSEMOTION:
            # Hover moves the cursor, so the pointer and the stick drive one
            # selection rather than two competing highlights. A gap leaves it
            # where it was.
            over = hovering(browser, event.pos, screen.get_size())
            if over is not None:
                if over != state.selected:
                    trace.say("hover", tile=over, was=state.selected, pos=list(event.pos))
                state.select(over)
        elif event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 1 and chip.hit(event.pos):
                self_update()
            elif event.button == 1:
                over = hovering(browser, event.pos, screen.get_size())
                # Only ever the tile actually under the pointer: hover has
                # already put the cursor there, so this cannot launch
                # something the click was not on.
                if over is not None and state.select(over):
                    menu = menu_for(state.game)
            # No button 4/5 here: SDL2 reports a wheel as MOUSEWHEEL *and* as
            # those two for compatibility, so handling both turns the page
            # twice for one scroll.
        elif event.type == pygame.KEYUP and event.key == pygame.K_SPACE:
            # A tap is the menu, as space always was; a hold that finished has
            # already seated the keyboard and this is just the key coming back
            # up.
            if space.up(time.monotonic()) and state.game is not None:
                menu = menu_for(state.game)
        elif event.type == pygame.MOUSEWHEEL:
            browser.grid.turn(-1 if event.y > 0 else 1)
        else:
            step = pads.direction(event)
            if step is not None:
                state.move(*step)
            else:
                pressed = pads.button(event)
                if pressed is not None:
                    on_grid_press(pressed)

    handlers = {
        Screen.PREPARE: on_prepare,
        Screen.SAVES_CHECK: on_saves_check,
        Screen.SAVES: on_saves,
        Screen.STORAGE: on_storage,
        Screen.MENU: on_menu,
        Screen.PANEL: on_panel,
        Screen.PANEL_TYPING: on_typing,
        Screen.TYPING: on_typing,
        Screen.GRID: on_grid,
    }

    def grid_view() -> GridView:
        words = chip.words(learned.report, updates.self_root(), time.monotonic())
        return GridView(state, art, browser.status, browser.installed, installs.rings(), browser.outdated, words)

    def paint_prepare() -> None:
        draw_prepare(
            screen, font_at, preparer.game, preparer.tail(28), prepare_failed,
            progress=preparer.progress if preparer.running else None,
            stage=preparer.stage if preparer.running else None,
            elapsed=preparer.elapsed,
        )

    def paint_saves_check() -> None:
        draw_saves_choice(screen, font_at, choice, starting[0].title if starting else "")

    def paint_saves() -> None:
        draw_saves(screen, font_at, saves)

    def paint_storage() -> None:
        draw_storage(screen, font_at, storage, storage_typing)

    def paint_panel() -> None:
        # The grid behind it, so changing a filter is visibly changing the
        # thing underneath rather than a number on a form.
        nonlocal chip
        view = grid_view()
        chip = chip.drawn(draw_shelf(screen, font_at, view) if browser.view == SHELF else draw(screen, font_at, view))
        draw_filters(screen, font_at, browser, panel, typing.text if typing is not None else None)
        if typing is not None:
            draw_keyboard(screen, font_at, typing)

    def paint_grid() -> None:
        nonlocal chip
        # Whatever the workers finished since the last frame stops being a
        # placeholder now. Only the page on screen is ever asked for.
        for game in loader.done():
            art.pop(game.key, None)
            shown.pace.busy(time.monotonic())
        if decoder.arrived():
            shown.pace.busy(time.monotonic())
        for game in state.page:
            surface_for(game)
        view = grid_view()
        if browser.view == SHELF and typing is None:
            # The menu is drawn over the list rather than swapping the screen
            # back to the grid underneath it, which is what happened before and
            # moved every game on screen.
            chip = chip.drawn(draw_shelf(screen, font_at, view))
        else:
            # Typing still belongs to the grid: the search box is drawn there,
            # and a shelf with its own copy would be two to keep in step.
            chip = chip.drawn(draw(screen, font_at, view, typing.text if typing is not None else None, menu))
        if menu is not None:
            draw_menu(screen, menu, view_rects(browser, screen.get_size()), font_at)
        if typing is not None:
            draw_keyboard(screen, font_at, typing)

    painters = {
        Screen.PREPARE: paint_prepare,
        Screen.SAVES_CHECK: paint_saves_check,
        Screen.SAVES: paint_saves,
        Screen.STORAGE: paint_storage,
        Screen.PANEL: paint_panel,
        Screen.PANEL_TYPING: paint_panel,
        Screen.MENU: paint_grid,
        Screen.TYPING: paint_grid,
        Screen.GRID: paint_grid,
    }

    def apply_prepare_done(done: plans.PrepareDone) -> None:
        """Carry out what a finished loader leaves to do (plans.on_prepare_done)."""
        nonlocal preparer, prepare_failed, after_prepare, restoring, chosen, running
        if done.failed:
            prepare_failed = True
        if done.clear:
            preparer = None
        if done.clear_after:
            after_prepare = None
        if done.clear_restoring:
            restoring = None
        if done.refresh:
            asker.refresh()
        if done.forget_versions:
            forget_versions()
        if done.restart_root:
            finish_restart(done.restart_root)
        if done.chip:
            say_chip(done.chip)
        if done.begin is not None:
            start(*done.begin)
        if done.launch is not None:
            chosen, running = done.launch, False

    # Everything built so far -- the library of nine thousand games, the
    # config, the caches -- lives until the picker exits, and the collector
    # walked all of it on every full pass: 1.4 ms, measured, landing in
    # whichever frame it chose. Frozen, a full pass costs nothing it can see.
    gc.collect()
    gc.freeze()

    # Held directions, from the d-pad or the left stick, as repeated steps.
    steer = Nav()

    try:
        while running:
            state = browser.grid
            heard = danstick.heard
            # Repeats due now, posted as d-pad presses before this frame's
            # events are read, so every screen takes them as it takes a press.
            # A held direction keeps the frames coming, or the repeat would
            # wait for the idle rate.
            now = time.monotonic()
            steer, due = steer.due(now)
            for pad, step in due:
                pads.repeat_press(pad, step)
            if steer.holding():
                shown.pace.busy(now)
            for event in pygame.event.get():
                # Anything at all: a key, a button, the pointer, a pad
                # arriving. The screen draws at full rate for a moment after.
                shown.pace.busy(time.monotonic())
                shown.mouse(event)
                if event.type == pygame.QUIT:
                    if preparer is not None:
                        preparer.cancel()
                    running = False
                    continue

                # Before any screen, because every screen needs it. Seating a
                # controller *replaces* it: danstick publishes `danstick Player
                # N` in its place. A picker holding only the handles it opened
                # at startup goes dead at exactly the moment somebody joins.
                if event.type == pygame.JOYDEVICEADDED:
                    sticks.add(event.device_index)
                    hush.refresh()
                    continue
                if event.type == pygame.JOYDEVICEREMOVED:
                    steer = steer.forget(event.instance_id)
                    sticks.remove(event.instance_id)
                    hush.refresh()
                    continue

                if not pads.is_repeat(event):
                    steer = _steered(steer, event, time.monotonic())

                # The keyboard takes a seat like everything else.
                #
                # It used to be the fallback underneath the pad rule -- no
                # daemon, no seat, no problem -- and that was the hole the
                # rule exists to close: a controller is a keyboard in
                # hardware, so "anything that types" included pads nobody had
                # assigned. Until danstick has seated it, the only key heard is
                # the space bar that asks for the seat. See keys.py.
                if event.type in (pygame.KEYDOWN, pygame.KEYUP, pygame.TEXTINPUT):
                    # The space bar is always heard: a keyboard that cannot
                    # ask for a seat cannot be given one.
                    drives = keys.drives(danstick.players, danstick.connected)
                    if not keyboard_heard(getattr(event, "key", None), drives):
                        trace.say("key-refused", key=getattr(event, "key", None))
                        continue

                handlers[current_screen()](event)

            # Written when they change, not on the way out: a picker gamescope
            # closes has no way out to write on.
            kept = kept.keep(remembered.snapshot(browser))
            # Installs running behind the grid: one landing is a badge, and
            # the versions the client listed were about a game not yet there.
            if installs.poll():
                asker.refresh()
                forget_versions()
            if update_check is not None and update_check.done():
                update_check = None
                asker.refresh()
            apply_badges()
            # The saves answer: a conflict is a choice, anything else starts
            # the game. No answer at all (no client, no service, too slow)
            # starts it too -- a launch is never held on a question.
            if checking is not None and checking.done():
                try:
                    found = checking.result()
                except Exception as error:  # noqa: BLE001 - a check must never stop a launch
                    trace.say("saves-check-failed", why=str(error))
                    found = None
                checking = None
                verdict = plans.plan_saves_answer(found, starting is not None)
                if verdict == plans.CHOOSE:
                    _, _, variant, version = starting
                    choice = Choice.open(starting[0], variant, version, found)
                elif verdict == plans.LAUNCH:
                    chosen, running = starting, False
            if listing is not None and listing.done():
                try:
                    found_saves = listing.result()
                except Exception as error:  # noqa: BLE001 - a list that fails says so on screen
                    trace.say("saves-list-failed", why=str(error))
                    found_saves = None
                listing = None
                if saves is not None:
                    saves = saves.loaded(found_saves)
            # Completion first, drawing second: a finished steam-add clears
            # the preparer, and this same frame must already be the grid's.
            if preparer is not None and not prepare_failed and not preparer.running:
                apply_prepare_done(
                    plans.on_prepare_done(
                        after_prepare,
                        preparer.ok,
                        preparer.tail(40),
                        picker=learned.report.picker if learned.report is not None else None,
                        restoring=restoring,
                        starting=starting,
                        game=preparer.game,
                        variant=preparer.variant,
                        version=preparer.version,
                    )
                )
            # Reconnected here rather than on a timer: connect() on an absent
            # socket fails at once with ENOENT, and a daemon started while the
            # picker is open should be picked up without restarting it.
            if not danstick.connected:
                if restarting is None and danstick_watch.due(time.monotonic()):
                    danstick_watch.mark(time.monotonic())
                    # Not fresh: a daemon that died mid-session restores the
                    # seats it had, which is what somebody halfway through an
                    # evening wants back.
                    #
                    # And not here: starting a daemon is a subprocess waited
                    # on for up to ten seconds, and it used to be waited on
                    # inside a frame -- the grid froze for as long as danstick
                    # took to come back. A thread starts it; the frame only
                    # looks to see whether it has finished.
                    restarting = restarter.submit(ensure_daemon, force=True, follow=os.getpid())
                if restarting is not None and restarting.done():
                    trace.say("daemon-restarted", trouble=restarting.result())
                    restarting = None
                danstick.connect()
            # Keeping up with danstick, once a frame: what it said is who is
            # seated, which is who the keyboard rule and pads.py ask about.
            # Keeping it listening for a hold is the overlay's (beside.py).
            for said in danstick.poll():
                if trace.on() and said.get("event") != "progress":
                    trace.say("danstick", **{k: v for k, v in said.items() if k not in ("lines", "build")})


            painting = time.perf_counter()
            painters[current_screen()]()

            drawn = time.perf_counter()
            now = time.monotonic()
            # Moving on its own clock, or the daemon talking: full rate. Idle
            # is only ever a screen with nothing on it that changes by itself.
            if (
                danstick.heard != heard
                or space.since is not None
                or preparer is not None
                # A failed install's ring is a mark that stays, not a motion.
                or any(not failed for _, failed in installs.rings().values())
            ):
                shown.pace.busy(now)
            shown.present()
            presented = time.perf_counter()
            # The loader only mirrors streamed text; 30fps halves the redundant
            # re-render of a mostly-unchanged tail across a minutes-long build.
            shown.rest(cap=30 if preparer is not None else None, woken=danstick.pending)
            ticked = time.perf_counter()
            said = fps.frame(drawn - painting, presented - drawn, ticked - presented, ticked)
            if said is not None:
                trace.say("frame", **said)
                if meter.wanted():
                    print(
                        "gotg-ui: {fps} fps   draw {draw_ms} ms   present {present_ms} ms   "
                        "idle {idle_ms} ms   worst {worst_ms} ms".format(**said),
                        file=sys.stderr,
                    )
        # Whatever repeat a held direction had queued is the grid's, not the
        # next program's.
        pads.drop_repeats()

    finally:
        # However the loop ends — quit, exec handoff, Ctrl-C, a crash — the
        # install must not be orphaned: its process group is detached from the
        # terminal, so nothing else will ever stop it.
        if preparer is not None and (chosen is None or preparer.game != chosen[0]):
            preparer.cancel()
        if update_check is not None:
            update_check.stop()
        asker.shutdown()
        # danstick goes on listening for a hold. It used to be told to stop
        # here, on the theory that a game has its own idea of what a button
        # does -- but the daemon seats only pads that hold no seat, so a
        # button in a game reseats nobody, and a second player arriving
        # mid-level is exactly who this is for. Seating outlives this client
        # in the daemon, which is what lets it.

    # Before the caller execs: the emulator must not inherit a window and a
    # grabbed GPU from a process that is about to stop existing -- nor the
    # controllers' keyboards, which are danstick's to hold from here on.
    hush.release()
    # And the bar: the game gets its own, which knows its console.
    beside.stop(overlay)
    pygame.quit()
    return restart if restart is not None else chosen


