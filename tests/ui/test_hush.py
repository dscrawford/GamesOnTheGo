"""Which input nodes are a controller's keyboard or mouse, from the file itself.

The fixture is /proc/bus/input/devices as this machine wrote it with a Steam
Controller Puck and an Xbox pad over Bluetooth attached, Steam running, and
two real keyboards and a mouse on the desk. Every name in here was seen.
"""

from __future__ import annotations

import pathlib

from gotg_ui import hush

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "input-devices-two-pads.txt"


def nodes():
    return hush.parse(FIXTURE.read_text())


def held():
    return hush.a_controllers(nodes())


def test_the_file_parses_to_what_the_kernel_listed():
    found = nodes()
    assert len(found) == 37
    puck = [n for n in found if n.name == "Valve Software Steam Controller Puck Keyboard"]
    assert len(puck) == 4
    assert puck[0].vendor == 0x28DE and puck[0].uniq == "FXB996050187C"
    assert "kbd" in puck[0].handlers and puck[0].event.startswith("event")


def test_the_pucks_lizard_keyboards_and_mice_are_held():
    # Eight of them: four wireless slots, a keyboard and a mouse each, in
    # hardware, until something opens the hidraw and says otherwise.
    names = [n.name for n in held()]
    assert names.count("Valve Software Steam Controller Puck Keyboard") == 4
    assert names.count("Valve Software Steam Controller Puck Mouse") == 4


def test_the_bluetooth_xbox_pads_extra_collections_are_held():
    # Same radio address as its js0, and no less a keyboard for being one.
    names = {n.name for n in held()}
    assert "Xbox Wireless Controller Keyboard" in names
    assert "Xbox Wireless Controller Mouse" in names


def test_a_real_keyboard_is_never_held():
    # The one thing that would make this worse than the bug.
    names = {n.name for n in held()}
    for real in (
        "Logitech USB Receiver Keyboard",
        "Logitech USB Receiver",
        "SINO WEALTH Bluetooth Keyboard",
        "SINO WEALTH Bluetooth Keyboard Consumer Control",
        "BlueZ 5.87 (MCS)",
        "nixos #1 (MCS)",
    ):
        assert real not in names, f"{real} would have been grabbed"


def test_the_phones_media_keys_on_the_same_radio_are_not_a_controllers():
    # BlueZ's MCS keyboard carries the adapter's address as its phys, and so
    # does the Xbox pad. Over Bluetooth a phys is the radio, not the device;
    # only the uniq says whose a node is.
    names = {n.name for n in held()}
    assert "nixos #1 (MCS)" not in names
    assert "BlueZ 5.87 (MCS)" not in names


def test_the_joysticks_themselves_are_not_held():
    # Those are SDL's to read, and danstick's to grab. Holding one here would
    # take it from both.
    names = {n.name for n in held()}
    assert "Xbox Wireless Controller" not in names
    assert "Microsoft X-Box 360 pad 1" not in names


def test_nothing_is_held_for_nothing():
    assert hush.a_controllers([]) == []
    assert hush.parse("") == []


def test_a_keyboard_that_merely_shares_a_phys_with_a_mouse_is_not_a_controllers():
    text = (
        'I: Bus=0003 Vendor=046d Product=c52b Version=0111\n'
        'N: Name="Logitech USB Receiver"\nP: Phys=usb-0000:08:00.3-1/input1\nU: Uniq=\n'
        'H: Handlers=mouse0 event9\n\n'
        'I: Bus=0003 Vendor=046d Product=c52b Version=0111\n'
        'N: Name="Logitech USB Receiver Keyboard"\nP: Phys=usb-0000:08:00.3-1/input1\nU: Uniq=\n'
        'H: Handlers=kbd event10\n'
    )
    assert hush.a_controllers(hush.parse(text)) == []


def test_a_keyboard_on_a_joysticks_phys_is_a_controllers():
    text = (
        'I: Bus=0003 Vendor=1234 Product=5678 Version=0001\n'
        'N: Name="Some Pad"\nP: Phys=usb-1/input0\nU: Uniq=\nH: Handlers=event20 js3\n\n'
        'I: Bus=0003 Vendor=1234 Product=5678 Version=0001\n'
        'N: Name="Some Pad Keyboard"\nP: Phys=usb-1/input0\nU: Uniq=\nH: Handlers=kbd event21\n'
    )
    assert [n.event for n in hush.a_controllers(hush.parse(text))] == ["event21"]


def test_refresh_lets_go_of_what_is_gone(monkeypatch):
    # No kernel here: the open and the grab are stubbed, and the release is
    # what is watched.
    opened, closed = [], []
    monkeypatch.setattr(hush.os, "open", lambda path, flags: opened.append(path) or 100 + len(opened))
    monkeypatch.setattr(hush.fcntl, "ioctl", lambda fd, req, arg=0: None)
    monkeypatch.setattr(hush.os, "close", lambda fd: closed.append(fd))
    h = hush.Hush()
    pad = (
        'I: Bus=0003 Vendor=1234 Product=5678 Version=0001\n'
        'N: Name="Some Pad"\nP: Phys=usb-1/input0\nU: Uniq=\nH: Handlers=event20 js3\n\n'
        'I: Bus=0003 Vendor=1234 Product=5678 Version=0001\n'
        'N: Name="Some Pad Keyboard"\nP: Phys=usb-1/input0\nU: Uniq=\nH: Handlers=kbd event21\n'
    )
    assert h.refresh(pad) == ["event21"]
    assert opened == ["/dev/input/event21"]
    assert h.refresh("") == []
    assert closed == [101]


DECK = pathlib.Path(__file__).parent / "fixtures" / "input-devices-steam-deck-xbox.txt"


def test_a_pad_whose_own_node_is_also_kbd_is_not_held():
    # The Deck, with an Xbox pad over Bluetooth: the kernel gives the pad's
    # own joystick node a `kbd` handler (it has keys), and it shares a uniq
    # with itself -- so it was held as its own keyboard. Grabbed, danstick never
    # heard it held, and nothing anybody pressed on it took a seat.
    names = {n.name for n in hush.a_controllers(hush.parse(DECK.read_text()))}
    assert "Xbox Wireless Controller" not in names
    assert "Valve Software Steam Controller" in names, "the Deck's lizard nodes still are"
