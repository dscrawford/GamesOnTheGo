"""Which pads may drive the picker.

padmap grabs a physical pad and republishes it as "padmap Player N", and the
picker should be moved by the clone alone. What is tested here is the rule
itself, with no pygame in sight: given a device's name, may it move the cursor.
"""

from __future__ import annotations

from gotg_ui import clones


def test_a_clone_is_padmaps_by_name():
    assert clones.is_clone("padmap Player 1")
    assert clones.is_clone("padmap Player 4")


def test_a_clone_is_padmaps_by_phys_when_the_name_was_not_kept():
    # padmap sets phys best-effort and falls back to the name; a front-end
    # that had only the phys should still know its own.
    assert clones.is_clone("Xbox 360 Controller", "padmap/p2")


def test_a_physical_pad_is_not_a_clone():
    assert not clones.is_clone("Microsoft X-Box 360 pad")
    assert not clones.is_clone("Steam Controller", "usb-0000:00:14.0-3/input0")


def test_a_pad_with_no_name_at_all_is_not_a_clone():
    # SDL answers "" for a device it opened and could not name, and a picker
    # that crashed there would die of somebody plugging in an oddity.
    assert not clones.is_clone("")
    assert not clones.is_clone(None, None)


def test_only_a_clone_drives_the_picker():
    assert clones.drives("padmap Player 1")
    assert not clones.drives("Microsoft X-Box 360 pad")


def test_there_is_no_machine_on_which_a_raw_pad_drives_it(monkeypatch):
    # The rule has no "unless": not when padmap is missing, not when it is
    # down, not when it is too old. A shell that had not reloaded since
    # padmap joined its PATH looked like "no padmap here", and an unassigned
    # Xbox pad drove the library -- the one thing this exists to stop.
    monkeypatch.delenv("GOTG_ANY_PAD", raising=False)
    assert not clones.drives("Microsoft X-Box 360 pad")
    assert not clones.drives("Steam Controller")
    assert not clones.drives("")
    assert not clones.drives(None)


def test_the_way_back_in_is_off_unless_somebody_typed_it(monkeypatch):
    monkeypatch.setenv("GOTG_ANY_PAD", "0")
    assert not clones.drives("Microsoft X-Box 360 pad")
    monkeypatch.setenv("GOTG_ANY_PAD", "1")
    assert clones.drives("Microsoft X-Box 360 pad")


# --- the pads that are open, and which of them may move anything ------------


def opened(**pads):
    owners = clones.Owners()
    for instance, name in pads.items():
        owners.opened(int(instance.lstrip("p")), name)
    return owners


def test_a_published_pad_drives_and_the_pad_it_was_made_from_does_not():
    # Both are open at once, which is the whole difficulty: padmap grabs the
    # first and publishes the second, and only the second has a seat.
    owners = opened(p3="Microsoft X-Box 360 pad", p7="padmap Player 1")
    assert not owners.may_drive(3)
    assert owners.may_drive(7)


def test_a_pad_nobody_opened_drives_nothing():
    # A device that failed to open, or one whose events outlived it.
    assert not opened(p1="padmap Player 1").may_drive(9)
    assert not opened(p1="padmap Player 1").may_drive(None)


def test_an_unplugged_pad_is_forgotten_rather_than_left_behind():
    # The kernel reuses an instance id: a stale name against a number that now
    # belongs to somebody's raw pad is the rule inverted.
    owners = opened(p4="padmap Player 2")
    owners.closed(4)
    assert not owners.may_drive(4)


def test_a_pad_opened_before_padmap_was_anywhere_still_does_not_drive():
    # Nothing about the daemon's state reaches this: the pad is either one
    # padmap published, or it is not.
    owners = opened(p3="Microsoft X-Box 360 pad", p7="padmap Player 1")
    assert not owners.may_drive(3)
    assert owners.may_drive(7)


# --- the clone SDL renamed ---------------------------------------------------
#
# Every GUID below was read off a running daemon on 2026-09-20, with a synthetic
# pad seated through padmap's seating mode.

CLONE_OF_AN_UNKNOWN_PAD = "030089a6aa2a0000bb5b000002000000"   # "padmap Player 2"
CLONE_OF_AN_XBOX_PAD = "030048665e0400008e02000003000000"      # "padmap Player 3"
THE_PAD_IT_WAS_MADE_FROM = "03004df75e0400008e02000010010000"  # "GOTG Fake Pad"
A_REAL_STEAM_CONTROLLER = "03002854de2800000413000002006800"
A_REAL_XBOX_PAD = "050018dc5e0400008e02000030110000"


def test_sdl_renames_a_clone_of_a_pad_it_knows_and_the_guid_still_says():
    # The bug this exists for: padmap's clone mirrors its source's vendor and
    # product by default, so SDL looks 045e:028e up in its own database and
    # calls the clone "Xbox 360 Controller" -- the kernel's "padmap Player 3"
    # is gone by the time a front-end asks. The GUID carries a CRC of the real
    # name, which SDL takes before it renames anything.
    assert clones.is_clone("Xbox 360 Controller", guid=CLONE_OF_AN_XBOX_PAD)


def test_a_clone_of_a_pad_sdl_does_not_know_keeps_its_name_and_agrees():
    assert clones.is_clone("padmap Player 2", guid=CLONE_OF_AN_UNKNOWN_PAD)


def test_the_pad_the_clone_was_made_from_is_still_not_a_clone():
    # Same vendor, same product, one letter of difference in the GUID: the
    # source's CRC is of its own name, and its version word is not a player.
    assert not clones.is_clone("Xbox 360 Controller", guid=THE_PAD_IT_WAS_MADE_FROM)


def test_real_pads_are_not_clones_whatever_they_are_called():
    assert not clones.is_clone("Steam Controller", guid=A_REAL_STEAM_CONTROLLER)
    assert not clones.is_clone("Xbox 360 Controller", guid=A_REAL_XBOX_PAD)


def test_a_guid_that_is_not_one_decides_nothing():
    assert not clones.is_clone("Xbox 360 Controller", guid="")
    assert not clones.is_clone("Xbox 360 Controller", guid="nonsense")
    assert clones.is_clone("padmap Player 1", guid="nonsense")


def test_a_renamed_clone_drives_the_picker():
    # The failure this would otherwise be: a player seats themselves, padmap
    # publishes their pad, and the picker ignores them for ever.
    owners = clones.Owners()
    owners.opened(3, "Xbox 360 Controller", CLONE_OF_AN_XBOX_PAD)
    owners.opened(2, "Xbox 360 Controller", THE_PAD_IT_WAS_MADE_FROM)
    assert owners.may_drive(3)
    assert not owners.may_drive(2)


def test_the_picker_turns_sdls_hidapi_off_so_a_steam_controllers_clone_is_seen():
    # Seen in a trace: HIDAPI on, the raw Steam Controller refused, its seat
    # taken, and the clone that should have driven the picker never listed.
    import os

    import gotg_ui  # noqa: F401 - importing is the act

    assert os.environ.get("SDL_JOYSTICK_HIDAPI") == "0"


def test_a_clone_says_which_seat_it_is():
    """Who pressed, not only whether somebody did.

    Three spellings of the same fact, because each survives a different thing:
    the kernel name is renamed by SDL, the phys is best-effort (UI_SET_PHYS
    can fail), and the GUID's CRC is what is left when both are gone.
    """
    assert clones.player_of("padmap Player 3") == 3
    assert clones.player_of("Xbox 360 Controller", "padmap/p2") == 2
    assert clones.player_of("Xbox 360 Controller", guid=CLONE_OF_AN_XBOX_PAD) == 3
    assert clones.player_of("Xbox 360 Controller", guid=THE_PAD_IT_WAS_MADE_FROM) is None
    assert clones.player_of("") is None


def test_a_seat_is_only_claimed_for_a_pad_this_has_opened():
    owners = clones.Owners()
    owners.opened(7, "Xbox 360 Controller", CLONE_OF_AN_XBOX_PAD)
    assert owners.player(7) == 3
    assert owners.player(9) is None, "an instance nobody opened belongs to nobody"
