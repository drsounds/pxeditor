"""Create a patched copy of Pickup.exe with a typed "noclip" cheat.

Usage:  python patch_noclip.py "<path>\\Pickup.exe" ["<output exe>"]

Typing ``noclip`` in-game (like the built-in cheat words) toggles a flag.
While it is on, the local player's vehicle:
  * skips the body-vs-world (wall/building) collision response, and
  * skips vehicle-vs-vehicle collisions (both directions).
Wheel/ground contact and item pickups are left untouched.

Only valid for the 282,624 byte Pickup.exe dated 2000-04-06; every patched
site is byte-verified before anything is written.
"""
import struct
import sys
from pathlib import Path

BASE = 0x400000          # image base; all sections have RVA == file offset
CAVE = 0x43BD00          # unused zero padding at the end of .text
FLAG = 0x442A80          # unused zero padding at the end of .data
STR_WORD = 0x442A90      # "noclip"
STR_ON = 0x442AA0        # "Noclip ON"
STR_OFF = 0x442AB0       # "Noclip OFF"

CHEAT_TAIL = 0x4143D2    # shared "show cheat message" tail (edi=msg, edx=dest)
CHEAT_NONE = 0x4144ED    # "no cheat matched" exit
LOCAL_VEHICLE = 0x12D4   # game->localVehicle ; vehicle->game is at +0x10


class Asm:
    def __init__(self, org):
        self.org = org
        self.buf = bytearray()

    @property
    def here(self):
        return self.org + len(self.buf)

    def raw(self, *b):
        self.buf += bytes(b)

    def d32(self, v):
        self.buf += struct.pack("<I", v & 0xFFFFFFFF)

    def jmp(self, target):
        self.raw(0xE9)
        self.d32(target - (self.here + 4))

    def jcc(self, cc, target):          # cc: 0x84 = je, 0x85 = jne
        self.raw(0x0F, cc)
        self.d32(target - (self.here + 4))

    def cmp_flag_zero(self):            # cmp byte ptr [FLAG], 0
        self.raw(0x80, 0x3D)
        self.d32(FLAG)
        self.raw(0x00)

    def load_game(self):                # mov eax, [edi+0x10]
        self.raw(0x8B, 0x47, 0x10)

    def cmp_reg_local(self, modrm):     # cmp <reg>, [eax+0x12d4]
        self.raw(0x3B, modrm)
        self.d32(LOCAL_VEHICLE)


def build():
    a = Asm(CAVE)

    # --- A: extra cheat word, reached when "atlantis" did not match -------
    cheat = a.here
    a.raw(0x6A, 0x06)                               # push 6
    a.raw(0x8D, 0x85); a.d32(0x2C3)                 # lea eax, [ebp+0x2c3]
    a.raw(0x68); a.d32(STR_WORD)                    # push "noclip"
    a.raw(0x50)                                     # push eax
    a.raw(0xFF, 0xD6)                               # call esi (strncmp)
    a.raw(0x83, 0xC4, 0x0C)                         # add esp, 0xc
    a.raw(0x85, 0xC0)                               # test eax, eax
    a.jcc(0x85, CHEAT_NONE)                         # jne -> no match
    a.raw(0x80, 0x35); a.d32(FLAG); a.raw(0x01)     # xor byte [FLAG], 1
    a.raw(0x8D, 0x95); a.d32(0x2C9)                 # lea edx, [ebp+0x2c9]
    a.raw(0xBF); a.d32(STR_ON)                      # mov edi, "Noclip ON"
    a.jcc(0x85, CHEAT_TAIL)                         # jnz (flag now 1)
    a.raw(0xBF); a.d32(STR_OFF)                     # mov edi, "Noclip OFF"
    a.jmp(CHEAT_TAIL)

    # --- B: body-vs-world collision in Vehicle::StaticCollision ------------
    world = a.here
    a.raw(0x84, 0xC0)                               # test al, al   (original)
    a.jcc(0x84, 0x437B2F)                           # je  exit      (original)
    a.cmp_flag_zero()
    a.jcc(0x84, 0x4376DC)                           # noclip off -> continue
    a.load_game()
    a.cmp_reg_local(0xB8)                           # cmp edi, [eax+0x12d4]
    a.jcc(0x84, 0x437B2F)                           # local player -> skip
    a.jmp(0x4376DC)

    # --- C: vehicle-vs-vehicle collision loop ------------------------------
    cars = a.here
    a.raw(0x85, 0xED)                               # test ebp, ebp (original)
    a.jcc(0x84, 0x43755D)                           # je  skip      (original)
    a.cmp_flag_zero()
    a.jcc(0x84, 0x437238)                           # noclip off -> continue
    a.load_game()
    a.cmp_reg_local(0xB8)                           # this  == local vehicle?
    a.jcc(0x84, 0x43755D)
    a.cmp_reg_local(0xB0)                           # other == local vehicle?
    a.jcc(0x84, 0x43755D)
    a.jmp(0x437238)

    def hook(site, target, size):
        h = Asm(site)
        h.jmp(target)
        h.buf += b"\x90" * (size - len(h.buf))
        return bytes(h.buf)

    jne = Asm(0x4143BA)
    jne.jcc(0x85, cheat)

    # (address, expected original bytes, replacement)
    return [
        (0x4143BA, bytes.fromhex("0f852d010000"), bytes(jne.buf)),
        (0x4376D4, bytes.fromhex("84c00f8453040000"), hook(0x4376D4, world, 8)),
        (0x437230, bytes.fromhex("85ed0f8425030000"), hook(0x437230, cars, 8)),
        (CAVE, bytes(len(a.buf)), bytes(a.buf)),
        (FLAG, bytes(1), bytes(1)),
        (STR_WORD, bytes(7), b"noclip\0"),
        (STR_ON, bytes(10), b"Noclip ON\0"),
        (STR_OFF, bytes(11), b"Noclip OFF\0"),
    ]


def grow_sections(data):
    """Make the padding used above part of the mapped .text/.data sections."""
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    opt = struct.unpack_from("<H", data, pe + 20)[0]
    for i in range(nsec):
        o = pe + 24 + opt + 40 * i
        name = bytes(data[o:o + 8]).rstrip(b"\0")
        vsize, _va, rsize, _raw = struct.unpack_from("<IIII", data, o + 8)
        if name in (b".text", b".data") and vsize < rsize:
            struct.pack_into("<I", data, o + 8, rsize)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    src = Path(argv[1])
    dst = Path(argv[2]) if len(argv) > 2 else src.with_name("Pickup (Patched).exe")
    data = bytearray(src.read_bytes())
    if len(data) != 282624:
        print(f"unexpected file size {len(data)}; refusing to patch")
        return 1
    for va, old, new in build():
        off = va - BASE
        if bytes(data[off:off + len(old)]) != old:
            print(f"byte mismatch at {va:#x}; refusing to patch")
            return 1
        data[off:off + len(new)] = new
    grow_sections(data)
    dst.write_bytes(data)
    print(f"wrote {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
