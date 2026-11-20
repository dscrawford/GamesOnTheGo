"""The CIA reader, against CIAs laid out in the test."""

import struct

import pytest
from cia import SIGNATURE, read


def _pad(b: bytes) -> bytes:
    return b + b"\0" * (-len(b) % 64)


def make_cia(title_id: int, contents: list[tuple[int, int]], signature: int = 0x10004) -> bytes:
    """A CIA header, made-up cert and ticket, and a TMD listing (content id, index) pairs."""
    body = bytearray(0xC4 + 64 * 0x24)
    struct.pack_into(">Q", body, 0x4C, title_id)
    struct.pack_into(">H", body, 0x9E, len(contents))
    for content_id, index in contents:
        body += struct.pack(">IHHQ", content_id, index, 0, 0x1000) + b"\0" * 32
    tmd = struct.pack(">I", signature) + b"\xaa" * SIGNATURE[signature] + bytes(body)
    cert, ticket = b"c" * 0xA00, b"t" * 0x350
    header = struct.pack("<IHHIIIIQ", 0x2020, 0, 0, len(cert), len(ticket), len(tmd), 0, 0).ljust(0x2020, b"\0")
    return _pad(header) + _pad(cert) + _pad(ticket) + _pad(tmd) + b"content"


def test_reads_the_title_and_its_boot_content():
    data = make_cia(0x0004000000055D00, [(0x0000000A, 1), (0x00000007, 0)])
    assert read(data) == (0x0004000000055D00, 7)


@pytest.mark.parametrize("signature", sorted(SIGNATURE))
def test_every_signature_type_is_skipped_by_its_own_size(signature):
    assert read(make_cia(0x0004000E00055D00, [(3, 0)], signature)) == (0x0004000E00055D00, 3)


def test_an_unknown_signature_is_refused():
    data = bytearray(make_cia(1, [(0, 0)]))
    tmd = 0x2040 + 0xA00 + 0x380
    struct.pack_into(">I", data, tmd, 0x99)
    with pytest.raises(ValueError, match="signature"):
        read(bytes(data))


def test_a_truncated_file_is_refused():
    with pytest.raises(ValueError):
        read(make_cia(1, [(0, 0)])[:0x2100])


def test_no_index_zero_is_refused():
    with pytest.raises(ValueError, match="index 0"):
        read(make_cia(1, [(5, 1)]))
