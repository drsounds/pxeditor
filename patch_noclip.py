"""Create a patched copy of Pickup.exe with typed "noclip", "fly", "boost" and "water" cheats.

Usage:  python patch_noclip.py "<path>\\Pickup.exe" ["<output exe>"]

The words are typed in-game like the built-in cheat words and toggle a mode.
Fly and boost start switched ON; noclip starts OFF.

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
  roughly 25% and 930%.  The current value is shown as a cheat message.

water -- toggles what water does, for every vehicle.  Off (the default, as in
  the original game): touching water paralyses the vehicle and respawns it,
  after WATER_DELAY seconds (1.5; the original game used 1.0).  On: water is
  a solid surface with full tyre friction (1.0, overriding the friction of
  the level's water material, which is 0 in some towns), so vehicles can
  drive and steer on it.

Keys for every other vehicle (always active, no cheat word):
  PageDown         hold to make all other vehicles rise, like PageUp does
  Insert / Delete  scale their engine force and top speed up / down
  Backspace        hold to drive them backwards: their engine pushes against
                   their driving direction with 10x force (REV_GAIN), so they
                   speed up in reverse for as long as the key is held
  Tab              press to turn them upside down (press again to turn them
                   back): each one is rotated half a turn about a horizontal
                   axis, in place

Only valid for the 282,624 byte Pickup.exe dated 2000-04-06; every patched
site is byte-verified before anything is written.
"""
import struct
import sys
from pathlib import Path

BASE = 0x400000          # image base; all sections have RVA == file offset
CAVE = 0x43BD00          # unused zero padding at the end of .text (0x300 bytes)
CAVE_END = 0x43C000
CAVE2 = 0x43F100         # unused zero padding at the end of .rdata, made
CAVE2_END = 0x440000     # executable by grow_sections(); holds the cheat words

# Unused zero padding at the end of .data
NOCLIP_ON = 0x442A80     # byte: noclip mode
FLY_ON = 0x442A81        # byte: fly mode
FLY_KEY = 0x442A82       # byte: fly key currently held
FLY_GAIN = 0x442A84      # float: climb momentum as a multiple of gravity force
BOOST_ON = 0x442A83      # byte: boost mode
NPC_FLY_KEY = 0x442A88   # byte: fly key for the other vehicles currently held
ALWAYS = 0x442A89        # byte: constant 1, gate for keys without a mode
WATER_SOLID = 0x442A8A   # byte: water behaves like a normal surface
NPC_REV_KEY = 0x442A8B   # byte: reverse key for the other vehicles currently held
NPC_FLIP = 0x442A8C      # byte: countdown 2 -> 1 -> 0 after the flip key; the
#                          other vehicles flip while it is 1 (see "lift")
#                          0x442A8D: flip key currently held (no auto-repeat)
REV_GAIN = 0x442B1C      # float: drive force while reversed, in engine forces
BOOST = 0x442B00         # float: engine force / top speed multiplier
BOOST_UP = 0x442B04      # float: step applied by the plus key
BOOST_DOWN = 0x442B08    # float: step applied by the minus key
BOOST_MAX = 0x442B0C     # float: a step that would go above this is ignored
BOOST_MIN = 0x442B10     # float: a step that would go below this is ignored
HUNDRED = 0x442B14       # float: 100.0, for the percentage message
NPC_BOOST = 0x442B18     # float: multiplier for every other vehicle
FLOATS = {
    BOOST: 1.0, BOOST_UP: 1.25, BOOST_DOWN: 0.8,
    BOOST_MAX: 9.5, BOOST_MIN: 0.25, HUNDRED: 100.0, NPC_BOOST: 1.0,
    REV_GAIN: -10.0,
}
# Initial values of the flag bytes at NOCLIP_ON.. (fly and boost start on).
FLAGS = {NOCLIP_ON: 0, FLY_ON: 1, FLY_KEY: 0, BOOST_ON: 1,
         NPC_FLY_KEY: 0, ALWAYS: 1, WATER_SOLID: 0, NPC_REV_KEY: 0,
         NPC_FLIP: 0, NPC_FLIP + 1: 0}
WATER_DELAY = 1.5        # seconds between touching water and the respawn
KEYTAB = 0x442B80        # key table read by the key event hook, see key_table()
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
    "npc_fmt": (0x442B70, b"NPC boost %d%%"),
    "water": (0x442E00, b"water"),
    "water_on": (0x442E10, b"Water is solid"),
    "water_off": (0x442E20, b"Water respawns you"),
}

FLY_GAIN_VALUE = -0.75   # negative: push against gravity
DIK_PRIOR = 0xC9         # PageUp, DirectInput scancode
VK_PRIOR = 0x21          # PageUp, Windows virtual key
DIK_ADD, DIK_SUBTRACT = 0x4E, 0x4A          # numpad + / -, DirectInput scancodes
DIK_PLUS_MAIN, DIK_MINUS_MAIN = 0x0C, 0x35  # main-row + / - on a Swedish layout
#                                             (US layout: 0x0D and 0x0C)
VK_ADD, VK_SUBTRACT = 0x6B, 0x6D            # numpad + / -, virtual keys
VK_OEM_PLUS, VK_OEM_MINUS = 0xBB, 0xBD      # main-row + / - on any layout
DIK_NEXT, VK_NEXT = 0xD1, 0x22              # PageDown: other vehicles fly
DIK_INSERT, VK_INSERT = 0xD2, 0x2D          # Insert: other vehicles faster
DIK_DELETE, VK_DELETE = 0xD3, 0x2E          # Delete: other vehicles slower
DIK_BACK, VK_BACK = 0x0E, 0x08              # Backspace: other vehicles reverse
DIK_TAB, VK_TAB = 0x0F, 0x09                # Tab: other vehicles upside down

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
VEH_DRIVE_FORCE = 0x144  # vehicle: engine force applied at the driven wheels
VEH_QUAT = 0x119C        # vehicle rigid body (+0x1150) orientation quaternion
#                          (+0x4c, 4 floats), renormalised by CcRigidBody::PostODE
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
        self.d32(self._target(target) - (self.here + 4))

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


def key_table():
    """Entries of 5 dwords: key, target, step, gate, message format.

    key     scancode for DirectInput events, 0x100 + virtual key for window
            message events
    target  step == 0: byte that is set while the key is held
            step == 1: byte that is set to 2 on press; the byte after it
                       tracks the key so that auto-repeat does not retrigger
            otherwise: float that is multiplied by the float at `step` on press
    gate    byte that must be non-zero for the key to do anything
    The table ends with a zero key.
    """
    def hold(dik, vk, flag):
        return [(k, flag, 0, ALWAYS, 0) for k in (dik, 0x100 + vk)]

    def scale(keys, target, step, gate, fmt):
        return [(k, target, step, gate, STRINGS[fmt][0]) for k in keys]

    rows = hold(DIK_PRIOR, VK_PRIOR, FLY_KEY) + hold(DIK_NEXT, VK_NEXT, NPC_FLY_KEY)
    rows += hold(DIK_BACK, VK_BACK, NPC_REV_KEY)
    rows += [(k, NPC_FLIP, 1, ALWAYS, 0) for k in (DIK_TAB, 0x100 + VK_TAB)]
    rows += scale((DIK_ADD, DIK_PLUS_MAIN, 0x100 + VK_ADD, 0x100 + VK_OEM_PLUS),
                  BOOST, BOOST_UP, BOOST_ON, "boost_fmt")
    rows += scale((DIK_SUBTRACT, DIK_MINUS_MAIN, 0x100 + VK_SUBTRACT,
                   0x100 + VK_OEM_MINUS), BOOST, BOOST_DOWN, BOOST_ON, "boost_fmt")
    rows += scale((DIK_INSERT, 0x100 + VK_INSERT), NPC_BOOST, BOOST_UP, ALWAYS,
                  "npc_fmt")
    rows += scale((DIK_DELETE, 0x100 + VK_DELETE), NPC_BOOST, BOOST_DOWN, ALWAYS,
                  "npc_fmt")
    return b"".join(struct.pack("<5I", *r) for r in rows) + bytes(4)


def assemble_cave(labels, short=frozenset()):
    """Assemble both caves; returns (code, rel8-capable branches, bad, code2)."""
    a = Asm(CAVE, labels, short)

    # --- extra cheat words, reached when "atlantis" did not match ----------
    # (second cave; only near branches, so it needs no relaxation)
    c = Asm(CAVE2, labels)
    c.label("cheat")
    c.cheat_word("noclip", NOCLIP_ON, "noclip_on", "noclip_off", "cheat_fly")
    c.label("cheat_fly")
    c.cheat_word("fly", FLY_ON, "fly_on", "fly_off", "cheat_boost")
    c.label("cheat_boost")
    c.cheat_word("boost", BOOST_ON, "boost_on", "boost_off", "cheat_water")
    c.label("cheat_water")
    c.cheat_word("water", WATER_SOLID, "water_on", "water_off", CHEAT_NONE)

    # --- water: the two places that flag a touched polygon as water ---------
    # (flags = material type compared with 15; body collision, then wheels)
    # For the wheels, solid water also skips "grip *= material friction"
    # (0x4384c2..0x4384cc), i.e. water gets friction 1.0 whatever the level
    # says, so the vehicle can accelerate, brake and steer on it.
    for name, flag_slot, back, solid in (("water_body", 0x13, 0x43794F, 0x43794F),
                                         ("water_wheel", 0x23, 0x4384C2, 0x4384CD)):
        a.label(name)
        a.jcc(0x85, back)                           # jne: not water (original)
        a.cmp_byte_zero(WATER_SOLID)
        a.jcc(0x85, solid)                          # solid water: do not flag it
        a.raw(0xC6, 0x44, 0x24, flag_slot, 0x01)    # mov byte [esp+slot], 1 (orig)
        a.jmp(back)

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

    # --- keys: look the event up in KEYTAB (esi = game) ----------------------
    # event type: 1/2 = DirectInput down/up, 3/4 = window message down/up
    a.label("keys")
    a.raw(0x8B, 0x44, 0x24, 0x20)                   # mov eax, [esp+0x20] (orig)
    a.raw(0x8B, 0x6C, 0x24, 0x24)                   # mov ebp, [esp+0x24] (orig)
    a.raw(0x8D, 0x48, 0xFF)                         # lea ecx, [eax-1]
    a.raw(0x83, 0xF9, 0x03)                         # cmp ecx, 3
    a.jcc(0x87, "keys_out")                         # ja
    a.raw(0xD1, 0xE9)                               # shr ecx, 1
    a.raw(0xC1, 0xE1, 0x08)                         # shl ecx, 8
    a.raw(0x01, 0xE9)                               # add ecx, ebp
    a.raw(0xBA); a.d32(KEYTAB - 20)                 # mov edx, KEYTAB - 20
    a.label("keys_next")
    a.raw(0x83, 0xC2, 0x14)                         # add edx, 20
    a.raw(0x83, 0x3A, 0x00)                         # cmp dword [edx], 0
    a.jcc(0x84, "keys_out")
    a.raw(0x39, 0x0A)                               # cmp [edx], ecx
    a.jcc(0x85, "keys_next")
    a.raw(0x8B, 0x4A, 0x0C)                         # mov ecx, [edx+12] (gate)
    a.raw(0x80, 0x39, 0x00)                         # cmp byte [ecx], 0
    a.jcc(0x84, "keys_out")
    a.raw(0x8B, 0x4A, 0x04)                         # mov ecx, [edx+4] (target)
    a.raw(0x83, 0x7A, 0x08, 0x01)                   # cmp dword [edx+8], 1 (step)
    a.jcc(0x84, "keys_trigger")
    a.jcc(0x87, "keys_scale")                       # ja: step is a pointer
    a.raw(0xA8, 0x01)                               # test al, 1 (odd = down)
    a.raw(0x0F, 0x95, 0x01)                         # setnz byte [ecx]
    a.jmp("keys_out")

    a.label("keys_trigger")
    a.raw(0xA8, 0x01)                               # test al, 1
    a.jcc(0x84, "keys_trigger_up")
    a.raw(0x80, 0x79, 0x01, 0x00)                   # cmp byte [ecx+1], 0
    a.jcc(0x85, "keys_out")                         # still held: auto-repeat
    a.raw(0x66, 0xC7, 0x01, 0x02, 0x01)             # mov word [ecx], 0x0102
    a.jmp("keys_out")
    a.label("keys_trigger_up")
    a.raw(0xC6, 0x41, 0x01, 0x00)                   # mov byte [ecx+1], 0
    a.jmp("keys_out")

    a.label("keys_scale")
    a.raw(0xA8, 0x01)                               # test al, 1
    a.jcc(0x84, "keys_out")                         # key up -> ignore
    a.raw(0x50)                                     # push eax (event type)
    a.raw(0xD9, 0x01)                               # fld  dword [ecx]
    a.raw(0x8B, 0x42, 0x08)                         # mov eax, [edx+8]
    a.raw(0xD8, 0x08)                               # fmul dword [eax]
    a.raw(0xD8, 0x15); a.d32(BOOST_MAX)             # fcom [BOOST_MAX]
    a.raw(0xDF, 0xE0)                               # fnstsw ax
    a.raw(0xF6, 0xC4, 0x41)                         # test ah, 0x41
    a.jcc(0x84, "keys_drop")                        # new > MAX
    a.raw(0xD8, 0x15); a.d32(BOOST_MIN)             # fcom [BOOST_MIN]
    a.raw(0xDF, 0xE0)                               # fnstsw ax
    a.raw(0xF6, 0xC4, 0x01)                         # test ah, 1
    a.jcc(0x85, "keys_drop")                        # new < MIN
    a.raw(0xD9, 0x11)                               # fst  dword [ecx]
    a.raw(0xD8, 0x0D); a.d32(HUNDRED)               # fmul [HUNDRED]
    a.raw(0x50)                                     # push eax (slot for %d)
    a.raw(0xDB, 0x1C, 0x24)                         # fistp dword [esp]
    a.raw(0xFF, 0x72, 0x10)                         # push dword [edx+16] (format)
    a.raw(0x8D, 0x86); a.d32(CHEAT_BUF_END)         # lea eax, [esi+0x2c9]
    a.raw(0x50)                                     # push eax
    a.raw(0xFF, 0x15); a.d32(SPRINTF)               # call [sprintf]
    a.raw(0x83, 0xC4, 0x0C)                         # add esp, 0xc
    a.raw(0xC7, 0x86); a.d32(GAME_MSG_TIME); a.d32(0x493E0)   # show message,
    a.raw(0xC7, 0x86); a.d32(GAME_MSG_TIME + 4); a.d32(0)     # as the cheats do
    a.raw(0x6A, 0x00, 0x6A, 0x00)                   # push 0 ; push 0
    a.raw(0x8B, 0x8E); a.d32(GAME_MSG_SOUND)        # mov ecx, [esi+0x165c]
    a.call(MSG_SOUND_FN)
    a.jmp("keys_done")
    a.label("keys_drop")
    a.raw(0xDD, 0xD8)                               # fstp st(0)
    a.label("keys_done")
    a.raw(0x58)                                     # pop eax
    a.label("keys_out")
    a.jmp(0x41E0A6)

    # --- boost: st(0) *= multiplier of the vehicle in ebp (clobbers eax) ----
    a.label("scale")
    a.raw(0x8B, 0x45, 0x10)                         # mov eax, [ebp+0x10]
    a.raw(0x3B, 0xA8); a.d32(LOCAL_VEHICLE)         # cmp ebp, [eax+0x12d4]
    a.raw(0xB8); a.d32(NPC_BOOST)                   # mov eax, &NPC_BOOST
    a.jcc(0x85, "scale_mul")                        # not the local vehicle
    a.raw(0xB8); a.d32(BOOST)                       # mov eax, &BOOST
    a.cmp_byte_zero(BOOST_ON)
    a.jcc(0x84, "scale_ret")
    a.label("scale_mul")
    a.raw(0xD8, 0x08)                               # fmul dword [eax]
    a.label("scale_ret")
    a.raw(0xC3)                                     # ret

    # --- boost: top speed (ebp = vehicle, ecx = type, flags from cmp) --------
    a.label("topspeed")
    a.raw(0xD9, 0x41, TYPE_TOP_SPEED)               # fld dword [ecx+0x6c] (orig)
    a.jcc(0x84, "topspeed_mult")                    # je            (original)
    a.raw(0xD8, 0x0D); a.d32(0x43C768)              # fmul [1.2]    (original)
    a.label("topspeed_mult")
    a.call("scale")
    a.jmp(0x434298)

    # --- boost: forward engine force (ebp = vehicle, ecx = type) ------------
    a.label("accel")
    a.raw(0xD8, 0x89); a.d32(TYPE_ENGINE_FORCE)     # fmul [ecx+0xdc] (orig)
    a.call("scale")
    a.jmp(0x43433C)

    # --- reverse: after the engine force is computed (ebp = vehicle) ---------
    # While the key is held every other vehicle gets a drive force of
    # REV_GAIN * its engine force, whatever its driver is doing.
    a.label("reverse")
    a.raw(0x8B, 0x45, 0x10)                         # mov eax, [ebp+0x10]
    a.raw(0x3B, 0xA8); a.d32(LOCAL_VEHICLE)         # cmp ebp, [eax+0x12d4]
    a.jcc(0x84, "reverse_out")                      # local player: untouched
    a.cmp_byte_zero(NPC_REV_KEY)
    a.jcc(0x84, "reverse_out")
    a.raw(0x8B, 0x85); a.d32(VEH_TYPE)              # mov eax, [ebp+0xf0]
    a.raw(0xD9, 0x80); a.d32(TYPE_ENGINE_FORCE)     # fld  dword [eax+0xdc]
    a.raw(0xD8, 0x0D); a.d32(REV_GAIN)              # fmul dword [REV_GAIN]
    a.raw(0xD9, 0x9D); a.d32(VEH_DRIVE_FORCE)       # fstp dword [ebp+0x144]
    a.label("reverse_out")
    a.raw(0xD9, 0x85); a.d32(0x104)                 # fld dword [ebp+0x104] (orig)
    a.jmp(0x4343BF)

    # --- fly: per-tick lift in the vehicle update (ebp = vehicle) -----------
    a.label("lift")
    a.raw(0x8B, 0xCD)                               # mov ecx, ebp      (orig)
    a.raw(0xC6, 0x85); a.d32(0x15D); a.raw(0x00)    # mov byte [ebp+0x15d], 0
    a.raw(0x8B, 0x45, 0x10)                         # mov eax, [ebp+0x10]
    a.raw(0x3B, 0xA8); a.d32(LOCAL_VEHICLE)         # cmp ebp, [eax+0x12d4]
    a.jcc(0x85, "lift_npc")
    # The local vehicle counts NPC_FLIP down once per tick, so every other
    # vehicle sees the value 1 in exactly one of its own ticks.
    a.cmp_byte_zero(NPC_FLIP)
    a.jcc(0x84, "lift_local")
    a.raw(0xFE, 0x0D); a.d32(NPC_FLIP)              # dec byte [NPC_FLIP]
    a.label("lift_local")
    a.cmp_byte_zero(FLY_ON)
    a.jcc(0x84, "lift_out")
    a.cmp_byte_zero(FLY_KEY)
    a.jcc(0x84, "lift_out")
    a.jmp("lift_up")
    a.label("lift_npc")
    a.raw(0x80, 0x3D); a.d32(NPC_FLIP); a.raw(0x01) # cmp byte [NPC_FLIP], 1
    a.jcc(0x85, "lift_npc_fly")
    # Half a turn about a horizontal axis: (q0, q1, q2, q3) -> (-q1, q0, -q3, q2)
    for q in (VEH_QUAT, VEH_QUAT + 8):
        a.raw(0x8B, 0x85); a.d32(q)                 # mov  eax, [ebp+q]
        a.raw(0x87, 0x85); a.d32(q + 4)             # xchg eax, [ebp+q+4]
        a.raw(0x35); a.d32(0x80000000)              # xor  eax, sign bit
        a.raw(0x89, 0x85); a.d32(q)                 # mov  [ebp+q], eax
    a.label("lift_npc_fly")
    a.cmp_byte_zero(NPC_FLY_KEY)
    a.jcc(0x84, "lift_out")
    a.label("lift_up")
    a.raw(0x8B, 0x85); a.d32(VEH_TYPE)              # mov eax, [ebp+0xf0]
    a.raw(0xD9, 0x40, 0x60)                         # fld  dword [eax+0x60]
    a.raw(0xD8, 0x8D); a.d32(VEH_GRAV_SCALE)        # fmul dword [ebp+0xe0]
    a.raw(0xD8, 0x0D); a.d32(FLY_GAIN)              # fmul dword [FLY_GAIN]
    a.raw(0xD9, 0x9D); a.d32(VEH_MOMENTUM_Y)        # fstp dword [ebp+0x11b0]
    a.label("lift_out")
    a.jmp(0x4348CC)

    return bytes(a.buf), a.fits_short, a.out_of_range, bytes(c.buf)


def build():
    # Assemble repeatedly: each pass resolves labels from the previous one and
    # shortens branches that turned out to be in rel8 range, until stable.
    labels, short, cave, cave2 = {}, set(), b"", b""
    while True:
        before = dict(labels)
        cave, fits, bad, cave2 = assemble_cave(labels, frozenset(short))
        if labels == before and fits <= short:
            assert not bad, "short branch out of range"
            break
        short |= fits
    assert CAVE + len(cave) <= CAVE_END, "code cave overflow"
    assert CAVE2 + len(cave2) <= CAVE2_END, "second code cave overflow"

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
        (0x437948, bytes.fromhex("7505c644241301"), hook(0x437948, "water_body", 7)),
        (0x4384BB, bytes.fromhex("7505c644242301"), hook(0x4384BB, "water_wheel", 7)),
        # push 1.0 -> push WATER_DELAY: paralysis time before the respawn
        (0x437AFD, bytes.fromhex("680000803f"), b"h" + struct.pack("<f", WATER_DELAY)),
        (0x439079, bytes.fromhex("680000803f"), b"h" + struct.pack("<f", WATER_DELAY)),
        (0x4343B9, bytes.fromhex("d98504010000"), hook(0x4343B9, "reverse", 6)),
        (CAVE, bytes(len(cave)), cave),
        (CAVE2, bytes(len(cave2)), cave2),
        (KEYTAB, bytes(len(key_table())), key_table()),
        (FLY_GAIN, bytes(4), struct.pack("<f", FLY_GAIN_VALUE)),
    ]
    for addr, value in FLAGS.items():
        patches.append((addr, b"\0", bytes([value])))
    for addr, value in FLOATS.items():
        patches.append((addr, bytes(4), struct.pack("<f", value)))
    for addr, text in STRINGS.values():
        patches.append((addr, bytes(len(text) + 1), text + b"\0"))
    spans = sorted((addr, addr + len(new)) for addr, _old, new in patches)
    for (_, end), (start, _) in zip(spans, spans[1:]):
        assert end <= start, f"patches overlap at {start:#x}"
    return patches


def grow_sections(data):
    """Map the padding used above; .rdata also becomes executable (cave 2)."""
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    opt = struct.unpack_from("<H", data, pe + 20)[0]
    for i in range(nsec):
        o = pe + 24 + opt + 40 * i
        name = bytes(data[o:o + 8]).rstrip(b"\0")
        vsize, _va, rsize, _raw = struct.unpack_from("<IIII", data, o + 8)
        if name in (b".text", b".rdata", b".data") and vsize < rsize:
            struct.pack_into("<I", data, o + 8, rsize)
        if name == b".rdata":
            flags = struct.unpack_from("<I", data, o + 36)[0]
            struct.pack_into("<I", data, o + 36, flags | 0x20000000)   # MEM_EXECUTE


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
