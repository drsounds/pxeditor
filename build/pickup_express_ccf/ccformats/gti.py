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
            # shipped RGB888 images store the bytes in R,G,B order (verified against the palettised twin,
            # whose palette and ARGB8888 are B,G,R)
            rgb = name == "RGB888"
            for i in range(n):
                a, b, c = data[i * 3], data[i * 3 + 1], data[i * 3 + 2]
                if not rgb: a, c = c, a
                out[i * 4] = a; out[i * 4 + 1] = b; out[i * 4 + 2] = c; out[i * 4 + 3] = 255
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

    def to_rgba_np(self, level: int = 0):
        """Decode to a numpy uint8 array (h, w, 4) RGBA, top row first.  Falls back to the slow decoder."""
        import numpy as np
        im = self.best()
        w = max(1, im.width >> level); h = max(1, im.height >> level)
        name = im.fmt_name; data = np.frombuffer(im.mips[level], dtype=np.uint8)
        out = np.empty((w * h, 4), np.uint8)
        if name in ("P8", "AP88"):
            pal = np.frombuffer(im.palette, np.uint8).reshape(-1, 4)
            if name == "P8":
                idx = data[:w * h]; a = np.full(w * h, 255, np.uint8)
            else:
                d = data[:w * h * 2].reshape(-1, 2); idx, a = d[:, 0], d[:, 1]
            out[:, 0] = pal[idx, 2]; out[:, 1] = pal[idx, 1]; out[:, 2] = pal[idx, 0]; out[:, 3] = a
        elif name in ("RGB888", "BGR888"):
            d = data[:w * h * 3].reshape(-1, 3); sw = name == "BGR888"
            out[:, 0] = d[:, 2 if sw else 0]; out[:, 1] = d[:, 1]; out[:, 2] = d[:, 0 if sw else 2]; out[:, 3] = 255
        elif name in ("ARGB8888", "BGRA8888"):
            d = data[:w * h * 4].reshape(-1, 4); out[:, 0] = d[:, 2]; out[:, 1] = d[:, 1]; out[:, 2] = d[:, 0]; out[:, 3] = d[:, 3]
        else:
            out = np.frombuffer(im.to_rgba(level), np.uint8).reshape(-1, 4).copy()
        return out.reshape(h, w, 4)

    @staticmethod
    def from_pil(pil_img, mipmaps: bool = True, with_palette: bool = True) -> "GtiFile":
        import numpy as np
        return GtiFile.from_rgba(np.asarray(pil_img.convert("RGBA")), mipmaps, with_palette)

    @staticmethod
    def from_rgba(rgba, mipmaps: bool = True, with_palette: bool = True) -> "GtiFile":
        """Build a GTI (palettised + true colour image, full mip chain) from an (h, w, 4) uint8 numpy array.
        Width and height should be powers of two (the engine's texture cache requires it).
        No CRC0 chunk is written; the engine regenerates the checksum itself."""
        import numpy as np
        rgba = np.ascontiguousarray(rgba, dtype=np.uint8)
        h, w = rgba.shape[:2]
        has_alpha = bool((rgba[:, :, 3] != 255).any())
        levels = [rgba]
        cur = rgba
        while mipmaps and min(cur.shape[0], cur.shape[1]) >= 16 and cur.shape[0] % 2 == 0 and cur.shape[1] % 2 == 0:   # engine files stop at 8 px
            c = cur.astype(np.uint16)
            cur = ((c[0::2, 0::2] + c[1::2, 0::2] + c[0::2, 1::2] + c[1::2, 1::2] + 2) // 4).astype(np.uint8)
            levels.append(cur)
        g = GtiFile()
        if with_palette:
            pal = _quantize(rgba[:, :, :3].reshape(-1, 3))
            mips = []
            for lv in levels:
                idx = _nearest(lv[:, :, :3].reshape(-1, 3), pal).astype(np.uint8)
                if has_alpha:
                    idx = np.stack([idx, lv[:, :, 3].reshape(-1)], axis=1).reshape(-1)
                mips.append(idx.tobytes())
            palette = np.zeros((256, 4), np.uint8); palette[:, 0] = pal[:, 2]; palette[:, 1] = pal[:, 1]; palette[:, 2] = pal[:, 0]
            g.images.append(Image(FMT["AP88"] if has_alpha else FMT["P8"], w, h, palette.tobytes(), mips))
        mips = []
        for lv in levels:
            if has_alpha:
                mips.append(lv[:, :, [2, 1, 0, 3]].tobytes())
            else:
                mips.append(lv[:, :, [0, 1, 2]].tobytes())
        g.images.append(Image(FMT["ARGB8888"] if has_alpha else FMT["RGB888"], w, h, b"", mips))
        return g


def _nearest(px, pal):
    import numpy as np
    out = np.empty(len(px), np.int32)
    p = pal.astype(np.int32); pn = (p * p).sum(1)
    step = 16384
    for i in range(0, len(px), step):
        c = px[i:i + step].astype(np.int32)
        d = pn[None, :] - 2 * c @ p.T
        out[i:i + step] = d.argmin(1)
    return out


def _quantize(px, k: int = 256, iters: int = 6):
    """Small k-means colour quantiser (numpy only). Returns (256, 3) uint8."""
    import numpy as np
    uniq = np.unique(px, axis=0)
    if len(uniq) <= k:
        pal = np.zeros((k, 3), np.uint8); pal[:len(uniq)] = uniq; return pal
    rng = np.random.default_rng(1)
    sample = px[rng.choice(len(px), min(len(px), 30000), replace=False)].astype(np.float32)
    cent = sample[rng.choice(len(sample), k, replace=False)].copy()
    for _ in range(iters):
        idx = _nearest(sample.astype(np.int32), cent.astype(np.int32))
        for j in range(k):
            m = idx == j
            if m.any(): cent[j] = sample[m].mean(0)
    return np.clip(cent + 0.5, 0, 255).astype(np.uint8)


if __name__ == "__main__":
    import sys
    for path in sys.argv[1:]:
        g = GtiFile.load(path)
        print(path, "crc", [hex(c) for c in g.crcs], [(im.fmt_name, im.width, im.height, im.palette_entries, len(im.mips)) for im in g.images])
