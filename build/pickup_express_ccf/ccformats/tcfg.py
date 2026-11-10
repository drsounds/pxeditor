"""
Town configuration (.tcfg) - "PET0" - 102 bytes, read by Pickup.exe with plain fread calls.

    char[4]  "PET0"
    u8       available_express   (checked when game mode == 1)
    u8       available_bomb      (checked when game mode == 2)
    u8       available_stunt     (checked when game mode == 3)
    f32 x2   map_scale (x, y)    minimap (Images-<mode>/map.gti):  pixel_x = world.z * scale_x + offset_x
    f32 x2   map_offset (x, y)                                     pixel_y = world.x * scale_y + offset_y
                                 (verified by overlaying level geometry on the shipped maps, IoU 0.49 vs 0.24 next best)
    i32      count               shipped values 3..7  (laps / opponents, guess)
    u8 x3    flags               shipped: (0,1,1) or (0,0,0)
    f32      value_a             shipped: 150.0 (session time?, guess)
    f32      value_b             shipped: 2.0
    char[64] description         e.g. "Created by Tomas & Timo"
"""
from __future__ import annotations
import struct
from dataclasses import dataclass, field
from typing import Tuple

MAGIC = b"PET0"
SIZE = 102
_FMT = "<4s3B2f2fi3Bff64s"


@dataclass
class TownConfig:
    available_express: int = 1
    available_bomb: int = 1
    available_stunt: int = 0
    map_scale: Tuple[float, float] = (0.3677, 0.3677)
    map_offset: Tuple[float, float] = (35.0, 55.0)
    count: int = 5
    flags: Tuple[int, int, int] = (0, 0, 0)
    value_a: float = 150.0
    value_b: float = 2.0
    description: str = "Created with pickup-express-tools"
    desc_raw: bytes = b""          # original 64 bytes (the shipped files contain uninitialised bytes after the NUL)

    @staticmethod
    def from_bytes(d: bytes) -> "TownConfig":
        if len(d) < SIZE or d[:4] != MAGIC:
            raise ValueError("not a PET0 town config")
        f = struct.unpack(_FMT, d[:SIZE])
        return TownConfig(f[1], f[2], f[3], (f[4], f[5]), (f[6], f[7]), f[8], (f[9], f[10], f[11]), f[12], f[13],
                          f[14].split(b"\0", 1)[0].decode("latin-1"), f[14])

    def to_bytes(self) -> bytes:
        desc = self.description.encode("latin-1")[:63]
        if self.desc_raw and self.desc_raw.split(bytes(1), 1)[0] == desc:
            desc = self.desc_raw
        return struct.pack(_FMT, MAGIC, self.available_express, self.available_bomb, self.available_stunt,
                           *self.map_scale, *self.map_offset, self.count, *self.flags, self.value_a, self.value_b, desc)

    @staticmethod
    def load(path: str) -> "TownConfig":
        with open(path, "rb") as f:
            return TownConfig.from_bytes(f.read())

    def save(self, path: str):
        with open(path, "wb") as f:
            f.write(self.to_bytes())

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        print(p, TownConfig.load(p))
