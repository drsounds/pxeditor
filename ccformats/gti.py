"""
GtIm texture files (.gti) used by the Gt graphics layer of cc.dll.

Layout:
    "GtIm" u32 version(4)
    chunks: char[4] tag, u32 size, body
      "CRC0"  u32 crc                         (one or more; checksum of source image(s))
      "Imag"  u32 format, u32 width, u32 height, u32 paletteEntries, u32 mipLevels,
              palette[paletteEntries] as BGRA u8x4, then pixel data for every mip level
              (level 0 first, each next level half size, pitch = width*bytesPerPixel)

Every shipped file carries two images: a palettised one (P8 / AP88) followed by a true
colour one (RGB888 / ARGB8888).  The game loads whichever its device prefers.

Format enum (GT_FMT_*):
"""
from __future__ import annotations
import struct, zlib
from dataclasses import dataclass, field
from typing import List, Optional

FORMATS = ("NONE P4 AP44 P8 AP88 RGB565 ARGB4444 RGB888 ARGB8888 RGB332 RGB555 ARGB1555 ARGB8332 "
           "DXT1 DXT2 DXT3 DXT4 DXT5 BGR565 BGRA4444 BGR888 BGRA8888 BGR233 BGR555 BGRA5551 BGRA2338 "
           "ABGR4444 ABGR8888 ABGR1555 ABGR8233 RGBA4444 RGBA8888 RGBA5551 RGBA3328 ARGB4565").split()
FMT = {n: i for i, n in enumerate(FORMATS)}

BPP = {"P4": 0.5, "AP44": 1, "P8": 1, "AP88": 2, "RGB565": 2, "ARGB4444": 2, "RGB888": 3, "ARGB8888": 4,
       "RGB332": 1, "RGB555": 2, "ARGB1555": 2, "ARGB8332": 2, "BGR565": 2, "BGRA4444": 2, "BGR888": 3,
       "BGRA8888": 4, "BGR233": 1, "BGR555": 2, "BGRA5551": 2, "BGRA2338": 2, "ABGR4444": 2, "ABGR8888": 4,
       "ABGR1555": 2, "ABGR8233": 2, "RGBA4444": 2, "RGBA8888": 4, "RGBA5551": 2, "RGBA3328": 2, "ARGB4565": 2}


@dataclass
class Image:
    fmt: int
    width: int
    height: int
    palette: bytes = b""          # paletteEntries * 4 bytes (B,G,R,A order as stored)
    mips: List[bytes] = field(default_factory=list)   # raw pixel data per level

    @property
    def fmt_name(self) -> str:
        return FORMATS[self.fmt] if self.fmt < len(FORMATS) else str(self.fmt)

    @property
    def palette_entries(self) -> int:
        return len(self.palette) // 4

    def to_rgba(self, level: int = 0) -> bytes:
        """Decode a mip level to packed RGBA8888 (row-major, top-down as stored)."""
        name = self.fmt_name
        w = max(1, self.width >> level); h = max(1, self.height >> level)
        data = self.mips[level]
        n = w * h
        out = bytearray(n * 4)
        if name == "P8":
            pal = self.palette
            for i in range(n):
                p = data[i] * 4
                out[i * 4] = pal[p + 2]; out[i * 4 + 1] = pal[p + 1]; out[i * 4 + 2] = pal[p]; out[i * 4 + 3] = 255
        elif name == "AP88":
            pal = self.palette
            for i in range(n):
                p = data[i * 2] * 4; a = data[i * 2 + 1]
                out[i * 4] = pal[p + 2]; out[i * 4 + 1] = pal[p + 1]; out[i * 4 + 2] = pal[p]; out[i * 4 + 3] = a
        elif name in ("RGB888", "BGR888"):
            # stored as B,G,R bytes (little endian 0xRRGGBB)
            for i in range(n):
                out[i * 4] = data[i * 3 + 2]; out[i * 4 + 1] = data[i * 3 + 1]; out[i * 4 + 2] = data[i * 3]; out[i * 4 + 3] = 255
        elif name in ("ARGB8888", "BGRA8888"):
            for i in range(n):
                out[i * 4] = data[i * 4 + 2]; out[i * 4 + 1] = data[i * 4 + 1]; out[i * 4 + 2] = data[i * 4]; out[i * 4 + 3] = data[i * 4 + 3]
        elif name == "RGB565":
            for i in range(n):
                v = data[i * 2] | (data[i * 2 + 1] << 8)
                out[i * 4] = ((v >> 11) & 31) * 255 // 31; out[i * 4 + 1] = ((v >> 5) & 63) * 255 // 63; out[i * 4 + 2] = (v & 31) * 255 // 31; out[i * 4 + 3] = 255
        elif name == "ARGB4444":
            for i in range(n):
                v = data[i * 2] | (data[i * 2 + 1] << 8)
                out[i * 4] = ((v >> 8) & 15) * 17; out[i * 4 + 1] = ((v >> 4) & 15) * 17; out[i * 4 + 2] = (v & 15) * 17; out[i * 4 + 3] = ((v >> 12) & 15) * 17
        elif name == "ARGB1555":
            for i in range(n):
                v = data[i * 2] | (data[i * 2 + 1] << 8)
                out[i * 4] = ((v >> 10) & 31) * 255 // 31; out[i * 4 + 1] = ((v >> 5) & 31) * 255 // 31; out[i * 4 + 2] = (v & 31) * 255 // 31; out[i * 4 + 3] = 255 if v & 0x8000 else 0
        else:
            raise NotImplementedError(f"decode of {name}")
        return bytes(out)


@dataclass
class GtiFile:
    version: int = 4
    crcs: List[int] = field(default_factory=list)
    images: List[Image] = field(default_factory=list)

    # ---------------- reading
    @staticmethod
    def from_bytes(d: bytes) -> "GtiFile":
        if d[:4] != b"GtIm":
            raise ValueError("not a GtIm file")
        g = GtiFile(version=struct.unpack_from("<I", d, 4)[0])
        p = 8
        while p + 8 <= len(d):
            tag = d[p:p + 4]; size = struct.unpack_from("<I", d, p + 4)[0]
            body = d[p + 8:p + 8 + size]; p += 8 + size
            if tag == b"CRC0":
                g.crcs.append(struct.unpack_from("<I", body)[0])
            elif tag == b"Imag":
                fmt, w, h, pal, mip = struct.unpack_from("<5I", body)
                q = 20
                palette = body[q:q + pal * 4]; q += pal * 4
                name = FORMATS[fmt] if fmt < len(FORMATS) else "?"
                bpp = BPP.get(name, 1)
                mips = []
                for lv in range(mip):
                    lw = max(1, w >> lv); lh = max(1, h >> lv)
                    n = int(lw * lh * bpp)
                    mips.append(body[q:q + n]); q += n
                g.images.append(Image(fmt, w, h, palette, mips))
        return g

    @staticmethod
    def load(path: str) -> "GtiFile":
        with open(path, "rb") as f:
            return GtiFile.from_bytes(f.read())

    # ---------------- writing
    def to_bytes(self) -> bytes:
        out = bytearray(b"GtIm" + struct.pack("<I", self.version))
        for c in self.crcs:
            out += b"CRC0" + struct.pack("<II", 4, c)
        for im in self.images:
            body = struct.pack("<5I", im.fmt, im.width, im.height, im.palette_entries, len(im.mips)) + im.palette + b"".join(im.mips)
            out += b"Imag" + struct.pack("<I", len(body)) + body
        return bytes(out)

    def save(self, path: str):
        with open(path, "wb") as f:
            f.write(self.to_bytes())

    # ---------------- convenience
    def best(self) -> Image:
        """Prefer the true-colour image."""
        for im in self.images:
            if im.fmt_name in ("ARGB8888", "RGB888", "BGRA8888", "BGR888"):
                return im
        return self.images[0]

    def to_pil(self, level: int = 0):
        from PIL import Image as PILImage
        im = self.best()
        w = max(1, im.width >> level); h = max(1, im.height >> level)
        return PILImage.frombytes("RGBA", (w, h), im.to_rgba(level))

    @staticmethod
    def from_pil(pil_img, mipmaps: bool = True, with_palette: bool = True) -> "GtiFile":
        """Build a GTI (palettised + truecolour, with mip chain) from a PIL image."""
        from PIL import Image as PILImage
        img = pil_img.convert("RGBA")
        has_alpha = any(a != 255 for a in img.getchannel("A").getdata())
        w, h = img.size
        levels = []
        cur = img
        while True:
            levels.append(cur)
            if not mipmaps or (cur.width <= 1 and cur.height <= 1) or cur.width % 2 or cur.height % 2:
                break
            cur = cur.resize((max(1, cur.width // 2), max(1, cur.height // 2)), PILImage.LANCZOS)
        g = GtiFile()
        g.crcs.append(zlib.crc32(img.tobytes()) & 0xFFFFFFFF)
        if with_palette:
            # palettised version: quantise level 0 and map all mips through the same palette
            pal_img = img.convert("RGB").quantize(256, method=PILImage.Quantize.MEDIANCUT)
            pal = pal_img.getpalette()[: 256 * 3]
            pal += [0] * (768 - len(pal))
            palette = bytearray()
            for i in range(256):
                palette += bytes((pal[i * 3 + 2], pal[i * 3 + 1], pal[i * 3], 0))
            mips = []
            for lv in levels:
                q = lv.convert("RGB").quantize(palette=pal_img, dither=PILImage.Dither.NONE)
                idx = q.tobytes()
                if has_alpha:
                    a = lv.getchannel("A").tobytes()
                    mips.append(bytes(b for pair in zip(idx, a) for b in pair))
                else:
                    mips.append(idx)
            g.images.append(Image(FMT["AP88"] if has_alpha else FMT["P8"], w, h, bytes(palette), mips))
        mips = []
        for lv in levels:
            raw = lv.tobytes()
            if has_alpha:
                mips.append(bytes(b for i in range(0, len(raw), 4) for b in (raw[i + 2], raw[i + 1], raw[i], raw[i + 3])))
            else:
                mips.append(bytes(b for i in range(0, len(raw), 4) for b in (raw[i + 2], raw[i + 1], raw[i])))
        g.images.append(Image(FMT["ARGB8888"] if has_alpha else FMT["RGB888"], w, h, b"", mips))
        return g


if __name__ == "__main__":
    import sys
    for path in sys.argv[1:]:
        g = GtiFile.load(path)
        print(path, "crc", [hex(c) for c in g.crcs], [(im.fmt_name, im.width, im.height, im.palette_entries, len(im.mips)) for im in g.images])
