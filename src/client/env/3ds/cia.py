"""What a CIA installs: its title ID and boot content, read from its TMD.

Azahar will not boot a .cia, only the .app it installs, and names that file
by these two numbers. Stdlib only; the layout is 3dbrew's CIA and TMD pages.
"""

import struct
import sys

ALIGN = 64
# Signature type -> bytes of signature plus padding before the TMD header.
SIGNATURE = {
    0x10003: 0x200 + 0x3C,  # RSA-4096 SHA-256
    0x10004: 0x100 + 0x3C,  # RSA-2048 SHA-256
    0x10005: 0x3C + 0x40,  # ECDSA SHA-256
}
HEADER = 0xC4
CONTENT_INFO = 64 * 0x24
CHUNK = 0x30


def _align(n: int) -> int:
    return (n + ALIGN - 1) // ALIGN * ALIGN


def read(data: bytes) -> tuple[int, int]:
    """(title id, content id of index 0) for the CIA in ``data``."""
    header_size, _type, _version, cert_size, ticket_size, tmd_size = struct.unpack_from("<IHHIII", data, 0)
    tmd = _align(_align(_align(header_size) + cert_size) + ticket_size)
    if tmd_size < 4 or tmd + tmd_size > len(data):
        raise ValueError("no TMD where the header puts it")
    (signature,) = struct.unpack_from(">I", data, tmd)
    if signature not in SIGNATURE:
        raise ValueError(f"unknown TMD signature type {signature:#x}")
    body = tmd + 4 + SIGNATURE[signature]
    (title_id,) = struct.unpack_from(">Q", data, body + 0x4C)
    (count,) = struct.unpack_from(">H", data, body + 0x9E)
    chunks = body + HEADER + CONTENT_INFO
    for i in range(count):
        content_id, index = struct.unpack_from(">IH", data, chunks + i * CHUNK)
        if index == 0:
            return title_id, content_id
    raise ValueError("TMD lists no content at index 0")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: cia.py <file.cia>", file=sys.stderr)
        return 2
    # The TMD sits before the contents, inside the first megabyte of any real CIA.
    with open(argv[1], "rb") as f:
        head = f.read(1 << 20)
    try:
        title_id, content_id = read(head)
    except (ValueError, struct.error) as e:
        print(f"{argv[1]}: {e}", file=sys.stderr)
        return 1
    print(f"{title_id:016x} {content_id:08x}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
