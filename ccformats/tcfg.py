"""
Town configuration (.tcfg) - "PET0" - 102 bytes, read by Pickup.exe (0x42c3a8) with plain fread calls.
One file per town and game mode: Towns/<Town>/<town>-<mode>.tcfg.

    char[4]  "PET0"
    u8       available_express   Town+0x145, checked when game mode == 1
    u8       available_stunt     Town+0x146, checked when game mode == 2 (set only in the shipped *-stunt.tcfg)
    u8       available_bomb      Town+0x147, checked when game mode == 3 (set only in the shipped *-bomb.tcfg)
    f32 x2   map_scale (x, y)    Town+0x12c  minimap, see map_point()
    f32 x2   map_offset (x, y)   Town+0x124
    i32      music_track         Town+0x134  CD audio track played in this town (0 = none); shipped 3..7
    u8       map_flip_x          Town+0x138  negate map_scale x
    u8       map_flip_y          Town+0x139  negate map_scale y
    u8       map_swap_axes       Town+0x13a  0: map x follows world z, map y follows world x;  1: the other way round
    f32      view_distance       Town+0x13c  far clipping distance of the camera (CcCamera::SetClippingDist(0.01, d))
    f32      fuel_price          Town+0x140  money per unit of fuel at gas stations (inferred from the refuel code)
    char[64] description         e.g. "Created by Tomas & Timo"

Minimap (Images-<mode>/map.gti, anchored to the bottom right corner of the screen); x, y are pixels in the image:

    a, b = (world.x, world.z) if map_swap_axes else (world.z, world.x)
    x = a * (-scale_x if map_flip_x else scale_x) + offset_x
    y = b * (-scale_y if map_flip_y else scale_y) + offset_y
"""
from __future__ import annotations
import struct
from dataclasses import dataclass
from typing import Tuple

MAGIC = b"PET0"
SIZE = 102
_FMT = "<4s3B2f2fi3Bff64s"


@dataclass
class TownConfig:
    available_express: int = 1
    available_stunt: int = 0
    available_bomb: int = 0
    map_scale: Tuple[float, float] = (0.3677, 0.3677)
    map_offset: Tuple[float, float] = (35.0, 55.0)
    music_track: int = 5
    map_flip_x: int = 0
    map_flip_y: int = 0
    map_swap_axes: int = 0
    view_distance: float = 150.0
    fuel_price: float = 2.0
    description: str = "Created with pickup-express-tools"
    desc_raw: bytes = b""          # original 64 bytes (the shipped files contain uninitialised bytes after the NUL)

    @staticmethod
    def from_bytes(d: bytes) -> "TownConfig":
        if len(d) < SIZE or d[:4] != MAGIC:
            raise ValueError("not a PET0 town config")
        f = struct.unpack(_FMT, d[:SIZE])
        return TownConfig(f[1], f[2], f[3], (f[4], f[5]), (f[6], f[7]), f[8], f[9], f[10], f[11], f[12], f[13],
                          f[14].split(b"\0", 1)[0].decode("latin-1"), f[14])

    def to_bytes(self) -> bytes:
        desc = self.description.encode("latin-1", "replace")[:63]
        if self.desc_raw and self.desc_raw.split(bytes(1), 1)[0] == desc:
            desc = self.desc_raw
        return struct.pack(_FMT, MAGIC, self.available_express & 0xFF, self.available_stunt & 0xFF, self.available_bomb & 0xFF,
                           *self.map_scale, *self.map_offset, self.music_track,
                           self.map_flip_x & 0xFF, self.map_flip_y & 0xFF, self.map_swap_axes & 0xFF,
                           self.view_distance, self.fuel_price, desc)

    def map_point(self, x: float, z: float) -> Tuple[float, float]:
        """World (x, z) -> pixel in the minimap image, as the game computes it."""
        a, b = (x, z) if self.map_swap_axes else (z, x)
        sx = -self.map_scale[0] if self.map_flip_x else self.map_scale[0]
        sy = -self.map_scale[1] if self.map_flip_y else self.map_scale[1]
        return a * sx + self.map_offset[0], b * sy + self.map_offset[1]

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
