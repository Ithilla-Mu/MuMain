#!/usr/bin/env python3
"""Read and edit Data/Local/ServerList.bmd, the client's server-group table.

The server list labels in the login screen do NOT come from the server or from
the localization resources -- the ConnectServer only sends numeric server IDs,
and the Valhalla/Helheim/Midgard entries in src/Localization/Game.*.resx
(legacy_id 540-542) are dead: nothing calls I18N::Game::Lookup on them.

CServerListManager::LoadServerList (src/source/Network/Server/ServerListManager.cpp)
reads this file, and the visible string is assembled there as

    <group name>-<index> <status>

where only the trailing status comes from i18n. So the group name is editable
only here.

On-disk format -- a bare sequence of records, no header, no record count:

    WORD  index                     group id the ConnectServer refers to
    char  name[32]                  NUL-padded UTF-8, what the player sees
    BYTE  pos
    BYTE  sequence                  display order in the list
    BYTE  nonPvp[15]                per-server flag: 0 PvP, 1 Non-PvP,
                                    2 Gold PvP, 3 Gold (see the switch at
                                    ServerListManager.cpp:184)
    short descriptionLength
    char  description[descriptionLength]

Every field is obfuscated with the engine's 3-byte "BUX" XOR
(bBuxCode = FC CF AB, src/source/Core/Globals/_crypt.h). The one thing worth
knowing: BuxConvert is called once per fread, so the key phase RESTARTS at the
record boundary and again at the description -- it does not run continuously
across the file. Decoding with a single continuous XOR yields garbage after
the first record.

Editing a name changes no offsets: the field is fixed at 32 bytes, so the file
length is unchanged and names may be up to 31 characters.

Usage:

    serverlist_bmd.py list Data/Local/ServerList.bmd
    serverlist_bmd.py rename Data/Local/ServerList.bmd --from Valhalla --to Ithilla
    serverlist_bmd.py rename Data/Local/ServerList.bmd --index 0 --to Ithilla

Remember there are several copies of Data/. The tracked master is
src/bin/Data/Local/ServerList.bmd, which CMake copies beside Main on every
build when MU_COPY_RUNTIME_ASSETS is ON; editing only a runtime copy is
reverted by the next asset refresh.
"""

import argparse
import struct
import sys
from pathlib import Path

BUX_CODE = bytes([0xFC, 0xCF, 0xAB])

NAME_LENGTH = 32
SERVER_COUNT = 15
# WORD + char[32] + BYTE + BYTE + BYTE[15] + short
RECORD_FORMAT = "<H32sBB15sh"
RECORD_SIZE = struct.calcsize(RECORD_FORMAT)


def bux(data: bytes) -> bytes:
    """Apply the BUX XOR. It is an involution, so this both decodes and encodes.

    The caller is responsible for starting a fresh call at every boundary the
    client's freads create, because the key phase restarts with each one.
    """
    return bytes(byte ^ BUX_CODE[i % 3] for i, byte in enumerate(data))


def parse(path: Path) -> list[dict]:
    raw = path.read_bytes()
    offset = 0
    groups = []

    while offset + RECORD_SIZE <= len(raw):
        record = bux(raw[offset:offset + RECORD_SIZE])
        offset += RECORD_SIZE
        index, name, pos, sequence, non_pvp, length = struct.unpack(RECORD_FORMAT, record)

        if length < 0 or offset + length > len(raw):
            raise ValueError(
                f"{path}: record {len(groups)} declares a {length}-byte description "
                f"but only {len(raw) - offset} bytes remain -- wrong key or corrupt file")

        description = bux(raw[offset:offset + length]) if length else b""
        offset += length

        groups.append({
            "index": index,
            "name": name.split(b"\0")[0].decode("utf-8"),
            "pos": pos,
            "sequence": sequence,
            "non_pvp": non_pvp,
            "description": description,
        })

    if offset != len(raw):
        raise ValueError(
            f"{path}: {len(raw) - offset} trailing byte(s) after the last record -- "
            "the file did not parse cleanly")

    return groups


def serialize(groups: list[dict]) -> bytes:
    blob = b""

    for group in groups:
        encoded = group["name"].encode("utf-8")
        if len(encoded) >= NAME_LENGTH:
            raise ValueError(
                f"name {group['name']!r} is {len(encoded)} bytes; the field holds "
                f"{NAME_LENGTH - 1} plus a NUL terminator")

        description = group["description"]
        record = struct.pack(
            RECORD_FORMAT, group["index"], encoded, group["pos"],
            group["sequence"], group["non_pvp"], len(description))
        blob += bux(record) + (bux(description) if description else b"")

    return blob


def command_list(args) -> int:
    for group in parse(args.file):
        flags = ",".join(str(flag) for flag in group["non_pvp"])
        print(f"index={group['index']:<4} sequence={group['sequence']:<4} "
              f"name={group['name']!r}")
        print(f"    pos={group['pos']} non_pvp=[{flags}] "
              f"description={group['description']!r}")
    return 0


def command_rename(args) -> int:
    groups = parse(args.file)

    if args.index is not None:
        matches = [g for g in groups if g["index"] == args.index]
        described = f"index {args.index}"
    else:
        matches = [g for g in groups if g["name"] == args.old]
        described = repr(args.old)

    if not matches:
        print(f"error: no group matching {described} in {args.file}", file=sys.stderr)
        print(f"       present: {[g['name'] for g in groups]}", file=sys.stderr)
        return 1

    for group in matches:
        print(f"{group['name']!r} -> {args.new!r} (index {group['index']})")
        group["name"] = args.new

    blob = serialize(groups)
    destination = args.output or args.file
    destination.write_bytes(blob)

    # Re-read rather than trust the buffer: this is the only cheap proof that
    # what landed on disk is still parseable by the same reader the client uses.
    reparsed = parse(destination)
    if [g["name"] for g in reparsed] != [g["name"] for g in groups]:
        print("error: re-read of the written file does not match", file=sys.stderr)
        return 1

    print(f"wrote {len(blob)} bytes to {destination}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    lister = subparsers.add_parser("list", help="print every server group")
    lister.add_argument("file", type=Path)
    lister.set_defaults(handler=command_list)

    renamer = subparsers.add_parser("rename", help="change a group's display name")
    renamer.add_argument("file", type=Path)
    selector = renamer.add_mutually_exclusive_group(required=True)
    selector.add_argument("--from", dest="old", help="current name to replace")
    selector.add_argument("--index", type=int, help="group index to replace")
    renamer.add_argument("--to", dest="new", required=True, help="new name")
    renamer.add_argument("--output", type=Path,
                         help="write here instead of editing in place")
    renamer.set_defaults(handler=command_rename)

    args = parser.parse_args()
    try:
        return args.handler(args)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
