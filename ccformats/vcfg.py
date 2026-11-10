"""
Vehicle configuration (.vcfg) - "PEV7" - 193 bytes, read by Pickup.exe (sub_431520) with fread.

    char[4]   "PEV7"
    char[32]  name
    u8        flag                       (Vehicle+0x11)
    f32[3] x5 vectors   -> Vehicle+0x78, +0x84, +0x90, +0x9c, +0xa8   (mass centre / wheel offsets / box, guess)
    f32  x24  scalars   -> see SCALAR_FIELDS (engine offsets kept as names until proven)

The physics scalars are stored in file order; the engine offsets they land in are listed so that
future reverse engineering can give them proper names.  The last five "scalars" are integers in
the shipped files (3,1,1,3,3 / 2,1,1,3,0 ...) followed by two floats (0.05, 23.0).
"""
from __future__ import annotations
import struct
from dataclasses import dataclass, field
from typing import List, Tuple

MAGIC = b"PEV7"
SIZE = 193

SCALAR_FIELDS = [
    "s_5c", "s_60", "s_dc", "s_e0", "s_e4", "s_70", "s_74", "s_b4", "s_b8", "s_bc", "s_c0", "s_c4",
    "s_c8", "s_cc", "s_d0", "s_d4", "s_d8", "i_48", "i_4c", "i_50", "i_54", "i_58", "s_68", "s_6c",
]
VECTOR_FIELDS = ["v_78", "v_84", "v_90", "v_9c", "v_a8"]


@dataclass
class VehicleConfig:
    name: str = "Vehicle"
    flag: int = 0
    vectors: List[Tuple[float, float, float]] = field(default_factory=lambda: [(0.0, 0.0, 0.0)] * 5)
    scalars: List[float] = field(default_factory=lambda: [0.0] * 24)
    name_raw: bytes = b""          # original 32 bytes (shipped files keep stale bytes after the terminator)

    @staticmethod
    def from_bytes(d: bytes) -> "VehicleConfig":
        if len(d) < SIZE or d[:4] != MAGIC:
            raise ValueError("not a PEV7 vehicle config")
        name = d[4:36].split(b"\0", 1)[0].decode("latin-1")
        flag = d[36]
        vals = struct.unpack_from("<15f", d, 37)
        vecs = [tuple(vals[i * 3:i * 3 + 3]) for i in range(5)]
        raw = d[97:97 + 96]
        scal = list(struct.unpack("<17f", raw[:68])) + list(struct.unpack("<5i", raw[68:88])) + list(struct.unpack("<2f", raw[88:96]))
        return VehicleConfig(name, flag, vecs, scal, d[4:36])

    def to_bytes(self) -> bytes:
        nb = self.name.encode("latin-1")[:31].ljust(32, bytes(1))
        if self.name_raw and self.name_raw.split(bytes(1), 1)[0] == self.name.encode("latin-1"):
            nb = self.name_raw
        out = MAGIC + nb + bytes([self.flag & 0xFF])
        for v in self.vectors:
            out += struct.pack("<3f", *v)
        s = self.scalars
        out += struct.pack("<17f", *s[:17]) + struct.pack("<5i", *[int(x) for x in s[17:22]]) + struct.pack("<2f", *s[22:24])
        return out

    @staticmethod
    def load(path: str) -> "VehicleConfig":
        with open(path, "rb") as f:
            return VehicleConfig.from_bytes(f.read())

    def save(self, path: str):
        with open(path, "wb") as f:
            f.write(self.to_bytes())

    def as_dict(self) -> dict:
        d = {"name": self.name, "flag": self.flag}
        d.update({n: v for n, v in zip(VECTOR_FIELDS, self.vectors)})
        d.update({n: v for n, v in zip(SCALAR_FIELDS, self.scalars)})
        return d


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        print(p, VehicleConfig.load(p).as_dict())
