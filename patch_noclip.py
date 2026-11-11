"""Create a patched copy of Pickup.exe with typed "noclip", "fly" and "boost" cheats.

Usage:  python patch_noclip.py "<path>\\Pickup.exe" ["<output exe>"]

The words are typed in-game like the built-in cheat words and toggle a mode.

noclip -- the local player's vehicle:
  * skips the body-vs-world (wall/building) collision response, and
  * skips vehicle-vs-vehicle collisions (both directions).
  Wheel/ground contact and item pickups are left untouched.

fly -- while the mode is on, holding PageUp makes the local player's vehicle
  rise: its vertical momentum is set against gravity every physics tick.
  Releasing the key lets it fall again.

boost -- while the mode is on, the plus and minus keys (numpad, or the main-row
  keys of the layout selected by DIK_PLUS_MAIN/DIK_MINUS_MAIN) scale the local
  player's engine force and top speed up or down by 25% per press, between
  roughly 30% and 900%.  The current value is shown as a cheat message.

Only valid for the 282,624 byte Pickup.exe dated 2000-04-06; every patched
site is byte-verified before anything is written.
"""
import struct
import sys
from pathlib import Path

BASE = 0x400000          # image base; all sections have RVA == file offset
CAVE = 0x43BD00          # unused zero padding at the end of .text (0x300 bytes)
CAVE_END = 0x43C000

# Unused zero padding at the end of .data
NOCLIP_ON = 0x442A80     # byte: noclip mode
FLY_ON = 0x442A81        # byte: fly mode
FLY_KEY = 0x442A82       # byte: fly key currently held
FLY_GAIN = 0x442A84      # float: climb momentum as a multiple of gravity force
BOOST_ON = 0x442A83      # byte: boost mode
BOOST = 0x442B00         # float: engine force / top speed multiplier
BOOST_UP = 0x442B04      # float: step applied by the plus key
BOOST_DOWN = 0x442B08    # float: step applied by the minus key
BOOST_MAX = 0x442B0C     # float: plus is ignored at or above this
BOOST_MIN = 0x442B10     # float: minus is ignored at or below this
HUNDRED = 0x442B14       # float: 100.0, for the percentage message
FLOATS = {
    BOOST: 1.0, BOOST_UP: 1.25, BOOST_DOWN: 0.8,
    BOOST_MAX: 8.0, BOOST_MIN: 0.3, HUNDRED: 100.0,
}
STRINGS = {
    "noclip": (0x442A90, b"noclip"),
    "noclip_on": (0x442AA0, b"Noclip ON"),
    "noclip_off": (0x442AB0, b"Noclip OFF"),
    "fly": (0x442AC0, b"fly"),
    "fly_on": (0x442AD0, b"Fly mode ON (hold PageUp)"),
    "fly_off": (0x442AF0, b"Fly mode OFF"),
    "boost": (0x442B20, b"boost"),
    "boost_on": (0x442B30, b"Boost mode ON (+/- keys)"),
    "boost_off": (0x442B50, b"Boost mode OFF"),
    "boost_fmt": (0x442B60, b"Boost %d%%"),
}

FLY_GAIN_VALUE = -0.75   # negative: push against gravity
DIK_PRIOR = 0xC9         # PageUp, DirectInput scancode
VK_PRIOR = 0x21          # PageUp, Windows virtual key
DIK_ADD, DIK_SUBTRACT = 0x4E, 0x4A          # numpad + / -, DirectInput scancodes
DIK_PLUS_MAIN, DIK_MINUS_MAIN = 0x0C, 0x35  # main-row + / - on a Swedish layout
#                                             (US layout: 0x0D and 0x0C)
VK_ADD, VK_SUBTRACT = 0x6B, 0x6D            # numpad + / -, virtual keys
VK_OEM_PLUS, VK_OEM_MINUS = 0xBB, 0xBD      # main-row + / - on any layout

CHEAT_TAIL = 0x4143D2    # shared "show cheat message" tail (edi=msg, edx=dest)
CHEAT_NONE = 0x4144ED    # "no cheat matched" exit
CHEAT_BUF_END = 0x2C9    # game+0x2c9: one past the last typed character;
#                          also where the cheat message text is stored
GAME_MSG_TIME = 0x1108   # game: message timer, set to 300000 / 0 by the cheats
GAME_MSG_SOUND = 0x165C  # game: object passed to MSG_SOUND_FN by the cheats
MSG_SOUND_FN = 0x409410
SPRINTF = 0x43C39C       # import: MSVCRT sprintf
LOCAL_VEHICLE = 0x12D4   # game->localVehicle ; vehicle->game is at +0x10
VEH_TYPE = 0xF0          # vehicle->type ; type+0x60 = gravity
VEH_GRAV_SCALE = 0xE0    # vehicle: gravity force = type.gravity * this
TYPE_TOP_SPEED = 0x6C    # type: speed above which the engine stops pushing
TYPE_ENGINE_FORCE = 0xDC # type: forward engine force per unit of throttle
VEH_MOMENTUM_Y = 0x11B0  # vehicle rigid body (+0x1150) linear momentum (+0x5c), Y


class Asm:
    def __init__(self, org, labels=None, short=frozenset()):
        self.org = org
        self.buf = bytearray()
        self.labels = {} if labels is None else labels
        self.short = short          # indices of branches to encode as rel8
        self.fits_short = set()     # indices of branches that could be rel8
        self.branches = 0
        self.out_of_range = False

    @property
    def here(self):
        return self.org + len(self.buf)

    def label(self, name):
        self.labels[name] = self.here

    def _target(self, t):
        return self.labels.get(t, 0) if isinstance(t, str) else t

    def raw(self, *b):
        self.buf += bytes(b)

    def d32(self, v):
        self.buf += struct.pack("<I", v & 0xFFFFFFFF)

    def _branch(self, near, short_op, target):
        index = self.branches
        self.branches += 1
        dest = self._target(target)
        rel8 = dest - (self.here + 2)
        if index in self.short:
            # Can be out of range while labels are still settling.
            self.out_of_range |= not -128 <= rel8 <= 127
            self.raw(short_op, rel8 & 0xFF)
            return
        if dest and -128 <= rel8 <= 127:
            self.fits_short.add(index)
        self.raw(*near)
        self.d32(dest - (self.here + 4))

    def jmp(self, target):
        self._branch((0xE9,), 0xEB, target)

    def jcc(self, cc, target):          # cc: 0x84 je, 0x85 jne, 0x87 ja
        self._branch((0x0F, cc), 0x70 | (cc & 0x0F), target)

    def call(self, target):
        self.raw(0xE8)
        self.d32(target - (self.here + 4))

    def cmp_ebp(self, value):           # cmp ebp, imm32
        self.raw(0x81, 0xFD)
        self.d32(value)

    def if_boosted_local(self, skip):
        """Fall through only for the local vehicle (ebp) with boost mode on."""
        self.cmp_byte_zero(BOOST_ON)
        self.jcc(0x84, skip)
        self.raw(0x8B, 0x45, 0x10)                      # mov eax, [ebp+0x10]
        self.raw(0x3B, 0xA8); self.d32(LOCAL_VEHICLE)   # cmp ebp, [eax+0x12d4]
        self.jcc(0x85, skip)

    def cmp_byte_zero(self, addr):      # cmp byte ptr [addr], 0
        self.raw(0x80, 0x3D)
        self.d32(addr)
        self.raw(0x00)

    def cheat_word(self, word, flag, on, off, no_match):
        """Match a typed word, toggle a flag byte, show the on/off message."""
        addr, text = STRINGS[word]
        self.raw(0x6A, len(text))                       # push len
        self.raw(0x8D, 0x85)                            # lea eax, [ebp+buf]
        self.d32(CHEAT_BUF_END - len(text))
        self.raw(0x68); self.d32(addr)                  # push word
        self.raw(0x50)                                  # push eax
        self.raw(0xFF, 0xD6)                            # call esi (strncmp)
        self.raw(0x83, 0xC4, 0x0C)                      # add esp, 0xc
        self.raw(0x85, 0xC0)                            # test eax, eax
        self.jcc(0x85, no_match)
        self.raw(0x80, 0x35); self.d32(flag)            # xor byte [flag], 1
        self.raw(0x01)
        self.raw(0x8D, 0x95); self.d32(CHEAT_BUF_END)   # lea edx, [ebp+0x2c9]
        self.raw(0xBF); self.d32(STRINGS[on][0])        # mov edi, on message
        self.jcc(0x85, CHEAT_TAIL)                      # flag is now 1
        self.raw(0xBF); self.d32(STRINGS[off][0])       # mov edi, off message
        self.jmp(CHEAT_TAIL)


def assemble_cave(labels, short=frozenset()):
    """Assemble the code cave; returns (code, rel8-capable branches, bad)."""
    a = Asm(CAVE, labels, short)

    # --- extra cheat words, reached when "atlantis" did not match ----------
    a.label("cheat")
    a.cheat_word("noclip", NOCLIP_ON, "noclip_on", "noclip_off", "cheat_fly")
    a.label("cheat_fly")
    a.cheat_word("fly", FLY_ON, "fly_on", "fly_off", "cheat_boost")
    a.label("cheat_boost")
    a.cheat_word("boost", BOOST_ON, "boost_on", "boost_off", CHEAT_NONE)

    # --- noclip: body-vs-world collision in Vehicle::StaticCollision -------
    a.label("world")
    a.raw(0x84, 0xC0)                               # test al, al   (original)
    a.jcc(0x84, 0x437B2F)                           # je  exit      (original)
    a.cmp_byte_zero(NOCLIP_ON)
    a.jcc(0x84, 0x4376DC)                           # noclip off -> continue
    a.raw(0x8B, 0x47, 0x10)                         # mov eax, [edi+0x10]
    a.raw(0x3B, 0xB8); a.d32(LOCAL_VEHICLE)         # cmp edi, [eax+0x12d4]
    a.jcc(0x84, 0x437B2F)                           # local player -> skip
    a.jmp(0x4376DC)

    # --- noclip: vehicle-vs-vehicle collision loop --------------------------
    a.label("cars")
    a.raw(0x85, 0xED)                               # test ebp, ebp (original)
    a.jcc(0x84, 0x43755D)                           # je  skip      (original)
    a.cmp_byte_zero(NOCLIP_ON)
    a.jcc(0x84, 0x437238)                           # noclip off -> continue
    a.raw(0x8B, 0x47, 0x10)                         # mov eax, [edi+0x10]
    a.raw(0x3B, 0xB8); a.d32(LOCAL_VEHICLE)         # this  == local vehicle?
    a.jcc(0x84, 0x43755D)
    a.raw(0x3B, 0xB0); a.d32(LOCAL_VEHICLE)         # other == local vehicle?
    a.jcc(0x84, 0x43755D)
    a.jmp(0x437238)

    # --- fly: track PageUp in the key event loop ----------------------------
    # event type: 1/2 = DirectInput down/up, 3/4 = window message down/up
    a.label("keys")
    a.raw(0x8B, 0x44, 0x24, 0x20)                   # mov eax, [esp+0x20] (orig)
    a.raw(0x8B, 0x6C, 0x24, 0x24)                   # mov ebp, [esp+0x24] (orig)
    a.raw(0x83, 0xF8, 0x02)                         # cmp eax, 2
    a.jcc(0x87, "keys_vk")                          # ja
    a.raw(0x81, 0xFD); a.d32(DIK_PRIOR)             # cmp ebp, DIK_PRIOR
    a.jcc(0x85, "boost_keys")
    a.jmp("keys_set")
    a.label("keys_vk")
    a.raw(0x83, 0xF8, 0x04)                         # cmp eax, 4
    a.jcc(0x87, "keys_out")                         # ja
    a.raw(0x83, 0xFD, VK_PRIOR)                     # cmp ebp, VK_PRIOR
    a.jcc(0x85, "boost_keys")
    a.label("keys_set")
    a.raw(0xA8, 0x01)                               # test al, 1 (odd = down)
    a.raw(0x0F, 0x95, 0x05); a.d32(FLY_KEY)         # setnz byte [FLY_KEY]
    a.jmp("keys_out")

    # --- boost: plus / minus key presses (esi = game) ------------------------
    a.label("boost_keys")
    a.cmp_byte_zero(BOOST_ON)
    a.jcc(0x84, "keys_out")
    a.raw(0x83, 0xF8, 0x01)                         # cmp eax, 1 (DI key down)
    a.jcc(0x84, "boost_di")
    a.raw(0x83, 0xF8, 0x03)                         # cmp eax, 3 (WM key down)
    a.jcc(0x85, "keys_out")
    for vk, dest in ((VK_ADD, "boost_plus"), (VK_OEM_PLUS, "boost_plus"),
                     (VK_SUBTRACT, "boost_minus"), (VK_OEM_MINUS, "boost_minus")):
        a.cmp_ebp(vk)
        a.jcc(0x84, dest)
    a.jmp("keys_out")
    a.label("boost_di")
    for dik, dest in ((DIK_ADD, "boost_plus"), (DIK_PLUS_MAIN, "boost_plus"),
                      (DIK_SUBTRACT, "boost_minus"), (DIK_MINUS_MAIN, "boost_minus")):
        a.cmp_ebp(dik)
        a.jcc(0x84, dest)
    a.jmp("keys_out")

    a.label("boost_plus")
    a.raw(0x50)                                     # push eax (event type)
    a.raw(0xBA); a.d32(BOOST_UP)                    # mov edx, &BOOST_UP
    a.raw(0xD9, 0x05); a.d32(BOOST)                 # fld  [BOOST]
    a.raw(0xD8, 0x1D); a.d32(BOOST_MAX)             # fcomp [BOOST_MAX]
    a.raw(0xDF, 0xE0)                               # fnstsw ax
    a.raw(0xF6, 0xC4, 0x01)                         # test ah, 1 (BOOST < MAX)
    a.jcc(0x84, "boost_done")
    a.jmp("boost_apply")
    a.label("boost_minus")
    a.raw(0x50)                                     # push eax (event type)
    a.raw(0xBA); a.d32(BOOST_DOWN)                  # mov edx, &BOOST_DOWN
    a.raw(0xD9, 0x05); a.d32(BOOST_MIN)             # fld  [BOOST_MIN]
    a.raw(0xD8, 0x1D); a.d32(BOOST)                 # fcomp [BOOST]
    a.raw(0xDF, 0xE0)                               # fnstsw ax
    a.raw(0xF6, 0xC4, 0x01)                         # test ah, 1 (MIN < BOOST)
    a.jcc(0x84, "boost_done")
    a.label("boost_apply")
    a.raw(0xD9, 0x05); a.d32(BOOST)                 # fld  [BOOST]
    a.raw(0xD8, 0x0A)                               # fmul dword [edx]
    a.raw(0xD9, 0x15); a.d32(BOOST)                 # fst  [BOOST]
    a.raw(0xD8, 0x0D); a.d32(HUNDRED)               # fmul [HUNDRED]
    a.raw(0x50)                                     # push eax (slot for %d)
    a.raw(0xDB, 0x1C, 0x24)                         # fistp dword [esp]
    a.raw(0x68); a.d32(STRINGS["boost_fmt"][0])     # push "Boost %d%%"
    a.raw(0x8D, 0x86); a.d32(CHEAT_BUF_END)         # lea eax, [esi+0x2c9]
    a.raw(0x50)                                     # push eax
    a.raw(0xFF, 0x15); a.d32(SPRINTF)               # call [sprintf]
    a.raw(0x83, 0xC4, 0x0C)                         # add esp, 0xc
    a.raw(0xC7, 0x86); a.d32(GAME_MSG_TIME); a.d32(0x493E0)   # show message,
    a.raw(0xC7, 0x86); a.d32(GAME_MSG_TIME + 4); a.d32(0)     # as the cheats do
    a.raw(0x6A, 0x00, 0x6A, 0x00)                   # push 0 ; push 0
    a.raw(0x8B, 0x8E); a.d32(GAME_MSG_SOUND)        # mov ecx, [esi+0x165c]
    a.call(MSG_SOUND_FN)
    a.label("boost_done")
    a.raw(0x58)                                     # pop eax
    a.label("keys_out")
    a.jmp(0x41E0A6)

    # --- boost: top speed (ebp = vehicle, ecx = type, flags from cmp) --------
    a.label("topspeed")
    a.raw(0xD9, 0x41, TYPE_TOP_SPEED)               # fld dword [ecx+0x6c] (orig)
    a.jcc(0x84, "topspeed_mult")                    # je            (original)
    a.raw(0xD8, 0x0D); a.d32(0x43C768)              # fmul [1.2]    (original)
    a.label("topspeed_mult")
    a.if_boosted_local("topspeed_out")
    a.raw(0xD8, 0x0D); a.d32(BOOST)                 # fmul [BOOST]
    a.label("topspeed_out")
    a.jmp(0x434298)

    # --- boost: forward engine force (ebp = vehicle, ecx = type) ------------
    a.label("accel")
    a.raw(0xD8, 0x89); a.d32(TYPE_ENGINE_FORCE)     # fmul [ecx+0xdc] (orig)
    a.if_boosted_local("accel_out")
    a.raw(0xD8, 0x0D); a.d32(BOOST)                 # fmul [BOOST]
    a.label("accel_out")
    a.jmp(0x43433C)

    # --- fly: per-tick lift in the vehicle update (ebp = vehicle) -----------
    a.label("lift")
    a.raw(0x8B, 0xCD)                               # mov ecx, ebp      (orig)
    a.raw(0xC6, 0x85); a.d32(0x15D); a.raw(0x00)    # mov byte [ebp+0x15d], 0
    a.cmp_byte_zero(FLY_ON)
    a.jcc(0x84, "lift_out")
    a.cmp_byte_zero(FLY_KEY)
    a.jcc(0x84, "lift_out")
    a.raw(0x8B, 0x45, 0x10)                         # mov eax, [ebp+0x10]
    a.raw(0x3B, 0xA8); a.d32(LOCAL_VEHICLE)         # cmp ebp, [eax+0x12d4]
    a.jcc(0x85, "lift_out")
    a.raw(0x8B, 0x85); a.d32(VEH_TYPE)              # mov eax, [ebp+0xf0]
    a.raw(0xD9, 0x40, 0x60)                         # fld  dword [eax+0x60]
    a.raw(0xD8, 0x8D); a.d32(VEH_GRAV_SCALE)        # fmul dword [ebp+0xe0]
    a.raw(0xD8, 0x0D); a.d32(FLY_GAIN)              # fmul dword [FLY_GAIN]
    a.raw(0xD9, 0x9D); a.d32(VEH_MOMENTUM_Y)        # fstp dword [ebp+0x11b0]
    a.label("lift_out")
    a.jmp(0x4348CC)

    return bytes(a.buf), a.fits_short, a.out_of_range


def build():
    # Assemble repeatedly: each pass resolves labels from the previous one and
    # shortens branches that turned out to be in rel8 range, until stable.
    labels, short, cave = {}, set(), b""
    while True:
        before = dict(labels)
        cave, fits, bad = assemble_cave(labels, frozenset(short))
        if labels == before and fits <= short:
            assert not bad, "short branch out of range"
            break
        short |= fits
    assert CAVE + len(cave) <= CAVE_END, "code cave overflow"

    def hook(site, label, size):
        h = Asm(site)
        h.jmp(labels[label])
        h.buf += b"\x90" * (size - len(h.buf))
        return bytes(h.buf)

    jne = Asm(0x4143BA)
    jne.jcc(0x85, labels["cheat"])

    # (address, expected original bytes, replacement)
    patches = [
        (0x4143BA, bytes.fromhex("0f852d010000"), bytes(jne.buf)),
        (0x4376D4, bytes.fromhex("84c00f8453040000"), hook(0x4376D4, "world", 8)),
        (0x437230, bytes.fromhex("85ed0f8425030000"), hook(0x437230, "cars", 8)),
        (0x41E09E, bytes.fromhex("8b4424208b6c2424"), hook(0x41E09E, "keys", 8)),
        (0x4348C3, bytes.fromhex("8bcdc6855d01000000"), hook(0x4348C3, "lift", 9)),
        (0x43428D, bytes.fromhex("d9416c7406"), hook(0x43428D, "topspeed", 5)),
        (0x434336, bytes.fromhex("d889dc000000"), hook(0x434336, "accel", 6)),
        (CAVE, bytes(len(cave)), cave),
        (NOCLIP_ON, bytes(4), bytes(4)),
        (FLY_GAIN, bytes(4), struct.pack("<f", FLY_GAIN_VALUE)),
    ]
    for addr, value in FLOATS.items():
        patches.append((addr, bytes(4), struct.pack("<f", value)))
    for addr, text in STRINGS.values():
        patches.append((addr, bytes(len(text) + 1), text + b"\0"))
    return patches


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
