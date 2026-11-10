"""
CcFf scene file (.ccf) reader / writer for the "Cc" engine in cc.dll (Pickup Express, Addgames 2000).

The format was recovered from CcRoom::LoadScene / CcRoom::SaveScene in cc.dll.

Container model
---------------
A file is:   "CcFf"  u32 version(0x98092901)  then one chunk tree.
A chunk is:  u16 tag, u32 size (size INCLUDES the 6-byte header), body.
A body is a fixed "head" (plain ints/floats/sub-chunks in a fixed order) followed by
zero or more child chunks until the chunk is exhausted - exactly like the engine's
OpenChunk()/ChunksLeft()/CloseChunk() loop.  Unknown child chunks are preserved as Raw
so that files round-trip byte-exactly.

Tag map (0x0001 root)
  0x1000 Rooms        -> 0x1100 Room {f010 name; i32 ref; [0x1200 static BSP][0x1201 portal BSP]}
  0x2000 Materials    -> 0x2100 Material {f010; i32 ref; 0x2110{f020 texture}; 0x2140 colours; 0x2150 flags; 0x2152/0x2153 physics}
  0x3000 Blueprints   -> 0x3100 Mesh, 0x4200 Null, 0x4300 Light   (templates)
  0x4000 Instances    -> 0x4100 Object(mesh instance), 0x4200 Null, 0x4300 Light
  f0xx   leaf types: f010 name pair, f020 string, f030 colour, f040 vec3, f050 3x3 matrix,
         f060 uv triple, f070 rotation, f090 polygon paint, f0a0/f0b0/f0b1 int, f0b2 byte,
         f0c0 bsp node, f0c1 bsp polygon, f0d0 (int,float,float)
"""
from __future__ import annotations
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

MAGIC = b"CcFf"
VERSION = 0x98092901

Vec3 = Tuple[float, float, float]
Mat3 = Tuple[Vec3, Vec3, Vec3]
IDENTITY: Mat3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


# ----------------------------------------------------------------------------------------------
# low level streams (mirror CcChunkReadStream / CcChunkWriteStream)
# ----------------------------------------------------------------------------------------------
class ChunkReader:
    def __init__(self, data: bytes):
        self.d = data
        self.p = 0
        self.ends: List[int] = []

    def open(self) -> int:
        tag, size = struct.unpack_from("<HI", self.d, self.p)
        if size < 6:
            raise ValueError(f"bad chunk size {size} at {self.p:#x}")
        self.ends.append(self.p + size)
        self.p += 6
        return tag

    def close(self):
        self.p = self.ends.pop()

    @property
    def end(self) -> int:
        return self.ends[-1] if self.ends else len(self.d)

    def left(self) -> bool:
        """True when a full chunk header still fits in the current chunk (engine: ChunksLeft)."""
        return self.p + 6 <= self.end

    def rest(self) -> bytes:
        b = self.d[self.p:self.end]
        self.p = self.end
        return b

    def peek_tag(self) -> int:
        return struct.unpack_from("<H", self.d, self.p)[0]

    def u8(self) -> int:
        v = self.d[self.p]; self.p += 1; return v

    def i32(self) -> int:
        v = struct.unpack_from("<i", self.d, self.p)[0]; self.p += 4; return v

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.d, self.p)[0]; self.p += 4; return v

    def f32(self) -> float:
        v = struct.unpack_from("<f", self.d, self.p)[0]; self.p += 4; return v

    def vec3(self) -> Vec3:
        v = struct.unpack_from("<3f", self.d, self.p); self.p += 12; return v

    def block(self, n: int) -> bytes:
        b = self.d[self.p:self.p + n]; self.p += n; return b


class ChunkWriter:
    def __init__(self):
        self.buf = bytearray()
        self.starts: List[int] = []

    def open(self, tag: int):
        self.starts.append(len(self.buf))
        self.buf += struct.pack("<HI", tag, 0)

    def close(self):
        s = self.starts.pop()
        struct.pack_into("<I", self.buf, s + 2, len(self.buf) - s)

    def u8(self, v: int): self.buf.append(v & 0xFF)
    def i32(self, v: int): self.buf += struct.pack("<i", int(v))
    def u32(self, v: int): self.buf += struct.pack("<I", int(v) & 0xFFFFFFFF)
    def f32(self, v: float): self.buf += struct.pack("<f", float(v))
    def vec3(self, v: Vec3): self.buf += struct.pack("<3f", *v)
    def block(self, b: bytes): self.buf += b

    def chunk_vec3(self, tag: int, v: Vec3):
        self.open(tag); self.vec3(v); self.close()

    def chunk_string(self, s: Optional[str]):
        """f020: u32 length (including NUL) + bytes; NULL string -> length 0."""
        self.open(0xF020)
        if s is None:
            self.u32(0)
        else:
            b = s.encode("latin-1") + b"\0"
            self.u32(len(b)); self.block(b)
        self.close()


def read_string_body(r: ChunkReader) -> Optional[str]:
    n = r.u32()
    if n == 0:
        return None
    b = r.block(n)
    return b.split(b"\0", 1)[0].decode("latin-1")


# ----------------------------------------------------------------------------------------------
# generic chunk objects
# ----------------------------------------------------------------------------------------------
class Chunk:
    TAG: int = 0
    CHILD_TYPES: dict = {}

    def __init__(self):
        self.children: List[Chunk] = []
        self.tail: bytes = b""

    # --- override in subclasses
    def read_head(self, r: ChunkReader): pass
    def write_head(self, w: ChunkWriter): pass

    def read_body(self, r: ChunkReader):
        self.read_head(r)
        while r.left():
            tag = r.open()
            cls = self.CHILD_TYPES.get(tag)
            if cls is None:
                c = Raw(tag, r.rest())
            else:
                c = cls()
                c.read_body(r)
            self.children.append(c)
            r.close()
        if r.p < r.end:
            self.tail = r.rest()

    def write(self, w: ChunkWriter):
        w.open(self.TAG)
        self.write_head(w)
        for c in self.children:
            c.write(w)
        w.block(self.tail)
        w.close()

    # --- helpers
    def find(self, cls):
        for c in self.children:
            if isinstance(c, cls):
                return c
        return None

    def find_all(self, cls):
        return [c for c in self.children if isinstance(c, cls)]

    def replace(self, cls, new):
        """Replace the first child of type cls (or append); new=None removes it."""
        for i, c in enumerate(self.children):
            if isinstance(c, cls):
                if new is None:
                    del self.children[i]
                else:
                    self.children[i] = new
                return
        if new is not None:
            self.children.append(new)

    def __repr__(self):
        return f"<{type(self).__name__} {self.TAG:#06x}>"


class Raw(Chunk):
    """Unknown / opaque chunk kept verbatim."""
    def __init__(self, tag: int = 0, data: bytes = b""):
        super().__init__()
        self.TAG = tag
        self.data = data

    def write(self, w: ChunkWriter):
        w.open(self.TAG); w.block(self.data); w.close()

    def __repr__(self):
        return f"<Raw {self.TAG:#06x} {len(self.data)}B>"


def _read_child(r: ChunkReader, expect_tag: int, cls):
    """Read one mandatory sub-chunk of a given tag (engine does OpenChunk + cmp ax, tag)."""
    tag = r.open()
    if tag != expect_tag:
        # engine tolerates: it just skips the chunk. We do the same but return None.
        r.close()
        return None
    c = cls()
    c.read_body(r)
    r.close()
    return c


def _read_vec3_chunk(r: ChunkReader, expect_tag: int = 0xF040) -> Vec3:
    tag = r.open()
    v = r.vec3() if tag == expect_tag else (0.0, 0.0, 0.0)
    r.close()
    return v


def _read_opt_string_chunk(r: ChunkReader) -> Optional[str]:
    if not r.left():
        return None
    tag = r.open()
    s = read_string_body(r) if tag == 0xF020 else None
    r.close()
    return s


# ----------------------------------------------------------------------------------------------
# leaf chunks
# ----------------------------------------------------------------------------------------------
class F020String(Chunk):
    TAG = 0xF020
    def __init__(self, s: Optional[str] = None):
        super().__init__(); self.s = s
    def read_body(self, r): self.s = read_string_body(r)
    def write(self, w): w.chunk_string(self.s)


class F010Name(Chunk):
    """Name block: f010 { f020 name, f020 prefix }  (CcName)"""
    TAG = 0xF010
    def __init__(self, name: Optional[str] = None, prefix: Optional[str] = None):
        super().__init__(); self.name = name; self.prefix = prefix
    def read_body(self, r):
        self.name = _read_opt_string_chunk(r)
        self.prefix = _read_opt_string_chunk(r)
        if r.p < r.end:
            self.tail = r.rest()
    def write(self, w):
        w.open(self.TAG); w.chunk_string(self.name); w.chunk_string(self.prefix); w.block(self.tail); w.close()
    def __repr__(self): return f"<Name {self.name!r}/{self.prefix!r}>"


class F030Color(Chunk):
    TAG = 0xF030
    def __init__(self, v: Vec3 = (0.0, 0.0, 0.0)):
        super().__init__(); self.v = v
    def read_body(self, r): self.v = r.vec3()
    def write(self, w): w.chunk_vec3(self.TAG, self.v)


class F040Vec3(Chunk):
    TAG = 0xF040
    def __init__(self, v: Vec3 = (0.0, 0.0, 0.0)):
        super().__init__(); self.v = v
    def read_body(self, r): self.v = r.vec3()
    def write(self, w): w.chunk_vec3(self.TAG, self.v)


class F060UV(Chunk):
    """Polygon mapping coordinates: (u0,v0),(u1,v1),(u2,v2)"""
    TAG = 0xF060
    def __init__(self, uv=((0.0, 0.0), (0.0, 0.0), (0.0, 0.0))):
        super().__init__(); self.uv = tuple(tuple(p) for p in uv)
    def read_body(self, r):
        self.uv = tuple((r.f32(), r.f32()) for _ in range(3))
    def write(self, w):
        w.open(self.TAG)
        for u, v in self.uv:
            w.f32(u); w.f32(v)
        w.close()


class F090Paint(Chunk):
    """Polygon paint: type 1 = flat colour (1 x f030), 3 = per-vertex (3 x f030), 4 = per-vertex 'F' (3 x f030)."""
    TAG = 0xF090
    def __init__(self, ptype: int = 1, colors=((1.0, 1.0, 1.0),)):
        super().__init__(); self.ptype = ptype; self.colors = tuple(colors)
    def read_body(self, r):
        self.ptype = r.i32()
        n = 1 if self.ptype == 1 else 3
        cols = []
        for _ in range(n):
            if not r.left(): break
            cols.append(_read_vec3_chunk(r, 0xF030))
        self.colors = tuple(cols)
        if r.p < r.end: self.tail = r.rest()
    def write(self, w):
        w.open(self.TAG); w.i32(self.ptype)
        for c in self.colors: w.chunk_vec3(0xF030, c)
        w.block(self.tail); w.close()


class F0A0Int(Chunk):
    """Mesh: vampire mode (CcMesh::SetVampireMode)"""
    TAG = 0xF0A0
    def __init__(self, v: int = 0): super().__init__(); self.v = v
    def read_body(self, r): self.v = r.i32()
    def write(self, w): w.open(self.TAG); w.i32(self.v); w.close()


class F0B0Int(F0A0Int):
    TAG = 0xF0B0


class F0B1Int(F0A0Int):
    TAG = 0xF0B1


class F0B2Byte(Chunk):
    TAG = 0xF0B2
    def __init__(self, v: int = 0): super().__init__(); self.v = v
    def read_body(self, r): self.v = r.u8()
    def write(self, w): w.open(self.TAG); w.u8(self.v); w.close()


class F0D0(Chunk):
    """Mesh extra: (i32, f32, f32) - bounding sphere / build data."""
    TAG = 0xF0D0
    def __init__(self, i: int = 0, a: float = 0.0, b: float = 0.0):
        super().__init__(); self.i = i; self.a = a; self.b = b
    def read_body(self, r): self.i = r.i32(); self.a = r.f32(); self.b = r.f32()
    def write(self, w): w.open(self.TAG); w.i32(self.i); w.f32(self.a); w.f32(self.b); w.close()


class F070Rotation(Chunk):
    """Rotation: f070 { f050 { f040 row0, f040 row1, f040 row2 } }  or  f070 { f040 axis-rotation }"""
    TAG = 0xF070
    def __init__(self, matrix: Mat3 = IDENTITY, axis: Optional[Vec3] = None):
        super().__init__(); self.matrix = matrix; self.axis = axis
    def read_body(self, r):
        if not r.left():
            return
        tag = r.open()
        if tag == 0xF040:
            self.axis = r.vec3(); self.matrix = IDENTITY
        elif tag == 0xF050:
            rows = []
            for _ in range(3):
                rows.append(_read_vec3_chunk(r))
            self.matrix = tuple(rows); self.axis = None
            if r.p < r.end: self.tail = r.rest()
        r.close()
    def write(self, w):
        w.open(self.TAG)
        if self.axis is not None:
            w.chunk_vec3(0xF040, self.axis)
        else:
            w.open(0xF050)
            for row in self.matrix: w.chunk_vec3(0xF040, row)
            w.block(self.tail)
            w.close()
        w.close()


# ----------------------------------------------------------------------------------------------
# BSP
# ----------------------------------------------------------------------------------------------
class BspPoly(Chunk):
    """f0c1: 5 x f040 (v0, v1, v2, normal, extra) ; i32 polygon index ; i32 object ref."""
    TAG = 0xF0C1
    def __init__(self):
        super().__init__()
        self.vecs: List[Vec3] = [(0.0, 0.0, 0.0)] * 5
        self.poly_index = 0
        self.object_ref = 0
    def read_body(self, r):
        self.vecs = [_read_vec3_chunk(r) for _ in range(5)]
        self.poly_index = r.i32(); self.object_ref = r.i32()
        if r.p < r.end: self.tail = r.rest()
    def write(self, w):
        w.open(self.TAG)
        for v in self.vecs: w.chunk_vec3(0xF040, v)
        w.i32(self.poly_index); w.i32(self.object_ref); w.block(self.tail); w.close()


class BspNode(Chunk):
    """f0c0: i32 hasNeg [f0c0 neg] i32 hasPos [f0c0 pos] f040 plane normal f040 plane point { f0c1 polys }

    `neg` (first child, CcBspNode+4) holds polygons on the negative side of the plane (dot(n, v - point) < 0),
    `pos` (second child, +8) the positive side; polygons stored in the node itself are coplanar with the plane.
    Polygons straddling the plane are split by the engine's builder (and by ccformats.bsp)."""
    TAG = 0xF0C0
    CHILD_TYPES = {0xF0C1: BspPoly}
    def __init__(self):
        super().__init__()
        self.neg: Optional[BspNode] = None
        self.pos: Optional[BspNode] = None
        self.normal: Vec3 = (0.0, 1.0, 0.0)
        self.point: Vec3 = (0.0, 0.0, 0.0)
    def read_head(self, r):
        if r.i32():
            self.neg = _read_child(r, 0xF0C0, BspNode)
        if r.i32():
            self.pos = _read_child(r, 0xF0C0, BspNode)
        self.normal = _read_vec3_chunk(r)
        self.point = _read_vec3_chunk(r)
    def write_head(self, w):
        w.i32(1 if self.neg else 0)
        if self.neg: self.neg.write(w)
        w.i32(1 if self.pos else 0)
        if self.pos: self.pos.write(w)
        w.chunk_vec3(0xF040, self.normal); w.chunk_vec3(0xF040, self.point)
    @property
    def polys(self) -> List[BspPoly]:
        return self.find_all(BspPoly)
    def count(self):
        n = 1
        if self.neg: n += self.neg.count()
        if self.pos: n += self.pos.count()
        return n
    def all_polys(self) -> List["BspPoly"]:
        out = list(self.polys)
        if self.neg: out += self.neg.all_polys()
        if self.pos: out += self.pos.all_polys()
        return out
    def depth(self) -> int:
        return 1 + max(self.neg.depth() if self.neg else 0, self.pos.depth() if self.pos else 0)


class BspList(Chunk):
    """0x1200 static BSP / 0x1201 portal BSP / 0x4101 object dynamic BSP: list of f0c0 root nodes"""
    TAG = 0x1200
    CHILD_TYPES = {0xF0C0: BspNode}
    def __init__(self, tag: int = 0x1200):
        super().__init__(); self.TAG = tag
    @property
    def roots(self) -> List[BspNode]:
        return self.find_all(BspNode)


class StaticBsp(BspList):
    TAG = 0x1200
    def __init__(self): super().__init__(0x1200)


class PortalBsp(BspList):
    TAG = 0x1201
    def __init__(self): super().__init__(0x1201)


class DynamicBsp(BspList):
    TAG = 0x4101
    def __init__(self): super().__init__(0x4101)


# ----------------------------------------------------------------------------------------------
# entities
# ----------------------------------------------------------------------------------------------
class Named(Chunk):
    """Base for entities that start with f010 name + i32 reference id."""
    def __init__(self, name: Optional[str] = None, prefix: Optional[str] = None, ref: int = 0):
        super().__init__()
        self.name = name; self.prefix = prefix; self.ref = ref

    def _read_name_ref(self, r):
        nm = _read_child(r, 0xF010, F010Name)
        if nm is not None:
            self.name, self.prefix, self._name_tail = nm.name, nm.prefix, nm.tail
        self.ref = r.i32()

    def _write_name_ref(self, w):
        nm = F010Name(self.name, self.prefix); nm.tail = getattr(self, "_name_tail", b"")
        nm.write(w); w.i32(self.ref)

    def __repr__(self):
        return f"<{type(self).__name__} {self.name!r} ref={self.ref:#x}>"


class Room(Named):
    TAG = 0x1100
    CHILD_TYPES = {0x1200: StaticBsp, 0x1201: PortalBsp, 0xF0C0: BspNode}
    def read_head(self, r): self._read_name_ref(r)
    def write_head(self, w): self._write_name_ref(w)
    @property
    def static_bsp(self) -> Optional[StaticBsp]: return self.find(StaticBsp)
    @property
    def portal_bsp(self) -> Optional[PortalBsp]: return self.find(PortalBsp)


class MatTexture(Chunk):
    TAG = 0x2110
    def __init__(self, name: Optional[str] = None): super().__init__(); self.name = name
    def read_body(self, r):
        self.name = _read_opt_string_chunk(r)
        if r.p < r.end: self.tail = r.rest()
    def write(self, w): w.open(self.TAG); w.chunk_string(self.name); w.block(self.tail); w.close()


class MatColors(Chunk):
    """0x2140: f030 colour A (diffuse), f030 colour B (ambient/emissive), f32 (opacity/shininess)"""
    TAG = 0x2140
    def __init__(self, a: Vec3 = (0.5, 0.5, 0.5), b: Vec3 = (0.7, 0.7, 0.7), f: float = 1.0):
        super().__init__(); self.a = a; self.b = b; self.f = f
    def read_body(self, r):
        self.a = _read_vec3_chunk(r, 0xF030); self.b = _read_vec3_chunk(r, 0xF030); self.f = r.f32()
        if r.p < r.end: self.tail = r.rest()
    def write(self, w):
        w.open(self.TAG); w.chunk_vec3(0xF030, self.a); w.chunk_vec3(0xF030, self.b); w.f32(self.f); w.block(self.tail); w.close()


class MatFlags(Chunk):
    """0x2150: u8 mode (CcMaterial+0x50), u8 alpha/blend (CcMaterial+0), i32 flags (CcMaterial+4)"""
    TAG = 0x2150
    def __init__(self, mode: int = 1, blend: int = 1, flags: int = 0):
        super().__init__(); self.mode = mode; self.blend = blend; self.flags = flags
    def read_body(self, r):
        self.mode = r.u8(); self.blend = r.u8(); self.flags = r.i32()
        if r.p < r.end: self.tail = r.rest()
    def write(self, w):
        w.open(self.TAG); w.u8(self.mode); w.u8(self.blend); w.i32(self.flags); w.block(self.tail); w.close()


class MatPhysics(Chunk):
    """0x2153: i32 material type, f32 friction, f32 energy absorption, f32 extra  (see .ccm files)"""
    TAG = 0x2153
    def __init__(self, mtype: int = 0, friction: float = 0.0, absorb: float = 0.9, extra: float = 0.0):
        super().__init__(); self.mtype = mtype; self.friction = friction; self.absorb = absorb; self.extra = extra
    def read_body(self, r):
        self.mtype = r.i32(); self.friction = r.f32(); self.absorb = r.f32(); self.extra = r.f32()
        if r.p < r.end: self.tail = r.rest()
    def write(self, w):
        w.open(self.TAG); w.i32(self.mtype); w.f32(self.friction); w.f32(self.absorb); w.f32(self.extra); w.block(self.tail); w.close()


class MatPhysics2(Chunk):
    """0x2152: older variant, i32 type, f32 friction, f32 absorb"""
    TAG = 0x2152
    def __init__(self, mtype: int = 0, friction: float = 0.0, absorb: float = 0.9):
        super().__init__(); self.mtype = mtype; self.friction = friction; self.absorb = absorb
    def read_body(self, r):
        self.mtype = r.i32(); self.friction = r.f32(); self.absorb = r.f32()
        if r.p < r.end: self.tail = r.rest()
    def write(self, w):
        w.open(self.TAG); w.i32(self.mtype); w.f32(self.friction); w.f32(self.absorb); w.block(self.tail); w.close()


class Material(Named):
    TAG = 0x2100
    CHILD_TYPES = {0x2110: MatTexture, 0x2140: MatColors, 0x2150: MatFlags, 0x2152: MatPhysics2, 0x2153: MatPhysics}
    def read_head(self, r): self._read_name_ref(r)
    def write_head(self, w): self._write_name_ref(w)

    # convenience
    @property
    def texture(self) -> Optional[str]:
        t = self.find(MatTexture); return t.name if t else None
    @texture.setter
    def texture(self, name: Optional[str]):
        self.replace(MatTexture, MatTexture(name) if name else None)
    @property
    def colors(self) -> MatColors:
        c = self.find(MatColors)
        if c is None: c = MatColors(); self.children.append(c)
        return c
    @property
    def flags(self) -> MatFlags:
        c = self.find(MatFlags)
        if c is None: c = MatFlags(); self.children.append(c)
        return c
    @property
    def physics(self) -> MatPhysics:
        c = self.find(MatPhysics)
        if c is None: c = MatPhysics(); self.children.append(c)
        return c


class SrtEntity(Named):
    """Anything with position / scale / rotation (CcSrtNode)."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.parent_ref = 0
        self.pos: Vec3 = (0.0, 0.0, 0.0)
        self.scale: float = 1.0
        self.rot = F070Rotation()

    @property
    def matrix(self) -> Mat3: return self.rot.matrix
    @matrix.setter
    def matrix(self, m: Mat3): self.rot.matrix = tuple(tuple(float(x) for x in row) for row in m); self.rot.axis = None

    def _read_srt(self, r):
        self.pos = _read_vec3_chunk(r)
        self.scale = r.f32()
        self.rot = _read_child(r, 0xF070, F070Rotation) or F070Rotation()

    def _write_srt(self, w):
        w.chunk_vec3(0xF040, self.pos); w.f32(self.scale); self.rot.write(w)


class Vertex(Chunk):
    """0x3110: f040 position ; optional f040 normal ; optional f030 colour"""
    TAG = 0x3110
    CHILD_TYPES = {0xF040: F040Vec3, 0xF030: F030Color}
    def __init__(self, pos: Vec3 = (0.0, 0.0, 0.0), normal: Optional[Vec3] = None, color: Optional[Vec3] = None):
        super().__init__(); self.pos = pos
        if normal is not None: self.children.append(F040Vec3(normal))
        if color is not None: self.children.append(F030Color(color))
    def read_head(self, r): self.pos = _read_vec3_chunk(r)
    def write_head(self, w): w.chunk_vec3(0xF040, self.pos)
    @property
    def normal(self) -> Optional[Vec3]:
        c = self.find(F040Vec3); return c.v if c else None
    @normal.setter
    def normal(self, v): self.replace(F040Vec3, F040Vec3(v) if v is not None else None)
    @property
    def color(self) -> Optional[Vec3]:
        c = self.find(F030Color); return c.v if c else None
    @color.setter
    def color(self, v): self.replace(F030Color, F030Color(v) if v is not None else None)
    def __repr__(self): return f"<Vertex {self.pos}>"


class Polygon(Chunk):
    """0x3120: i32 i0, i1, i2 ; i32 material ref ; optional f090 paint ; optional f060 uv"""
    TAG = 0x3120
    CHILD_TYPES = {0xF090: F090Paint, 0xF060: F060UV}
    def __init__(self, indices=(0, 1, 2), material_ref: int = 0, uv=None, paint: Optional[F090Paint] = None):
        super().__init__(); self.indices = tuple(indices); self.material_ref = material_ref
        if paint is not None: self.children.append(paint)
        if uv is not None: self.children.append(F060UV(uv))
    def read_head(self, r):
        self.indices = (r.i32(), r.i32(), r.i32()); self.material_ref = r.i32()
    def write_head(self, w):
        for i in self.indices: w.i32(i)
        w.i32(self.material_ref)
    @property
    def uv(self):
        c = self.find(F060UV); return c.uv if c else None
    @uv.setter
    def uv(self, v): self.replace(F060UV, F060UV(v) if v is not None else None)
    @property
    def paint(self) -> Optional[F090Paint]: return self.find(F090Paint)
    def __repr__(self): return f"<Poly {self.indices} mat={self.material_ref:#x}>"


class Mesh(SrtEntity):
    """0x3100 mesh blueprint.
    head: f010 name ; i32 ref ; u8 flag_a ; u8 flag_b ; i32 parent ref ; f040 pos ; f32 scale ; f070 rot
    children: 0x3110 vertices, 0x3120 polygons, f0a0 vampire mode, f0d0, f0b2 backdrop flag
    flag_a/flag_b: mesh type bits tested against LoadScene flags (0x200 / 0x100 masks): portal / backdrop meshes.
    """
    TAG = 0x3100
    CHILD_TYPES = {0x3110: Vertex, 0x3120: Polygon, 0xF0A0: F0A0Int, 0xF0D0: F0D0, 0xF0B2: F0B2Byte}
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.flag_a = 0; self.flag_b = 0
    def read_head(self, r):
        self._read_name_ref(r)
        self.flag_a = r.u8(); self.flag_b = r.u8()
        self.parent_ref = r.i32()
        self._read_srt(r)
    def write_head(self, w):
        self._write_name_ref(w); w.u8(self.flag_a); w.u8(self.flag_b); w.i32(self.parent_ref); self._write_srt(w)
    @property
    def vertices(self) -> List[Vertex]: return self.find_all(Vertex)
    @property
    def polygons(self) -> List[Polygon]: return self.find_all(Polygon)
    def set_geometry(self, vertices: List[Vertex], polygons: List[Polygon]):
        others = [c for c in self.children if not isinstance(c, (Vertex, Polygon))]
        self.children = list(vertices) + list(polygons) + others
    def __repr__(self): return f"<Mesh {self.name!r} ref={self.ref:#x} v={len(self.vertices)} p={len(self.polygons)}>"


class NullData(Chunk):
    """0x4210: i32 length + raw bytes (user data attached to a null, e.g. 'ANIMSF' animation tables)"""
    TAG = 0x4210
    def __init__(self, data: bytes = b""): super().__init__(); self.data = data
    def read_body(self, r):
        n = r.i32(); self.data = r.block(n)
        if r.p < r.end: self.tail = r.rest()
    def write(self, w): w.open(self.TAG); w.i32(len(self.data)); w.block(self.data); w.block(self.tail); w.close()


class Null(SrtEntity):
    """0x4200 null (dummy/helper point). head: f010 ; i32 ref ; i32 room ref ; i32 parent ref ; f040 pos ; f32 scale ; f070 rot"""
    TAG = 0x4200
    CHILD_TYPES = {0x4210: NullData}
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.room_ref = 0
    def read_head(self, r):
        self._read_name_ref(r); self.room_ref = r.i32(); self.parent_ref = r.i32(); self._read_srt(r)
    def write_head(self, w):
        self._write_name_ref(w); w.i32(self.room_ref); w.i32(self.parent_ref); self._write_srt(w)
    @property
    def data(self) -> Optional[bytes]:
        c = self.find(NullData); return c.data if c else None
    @data.setter
    def data(self, b): self.replace(NullData, NullData(b) if b is not None else None)


class LightParams(Chunk):
    """0x4311: u8 light type ; f32 intensity ; f030 colour ; f32 range/attenuation a ; f32 b ; [f020 lightmap texture]
       0x4310: same without the leading type byte."""
    TAG = 0x4311
    def __init__(self, ltype: int = 0, intensity: float = 1.0, color: Vec3 = (1.0, 1.0, 1.0), a: float = 0.0, b: float = 0.0, lightmap: Optional[str] = None):
        super().__init__(); self.ltype = ltype; self.intensity = intensity; self.color = color; self.a = a; self.b = b; self.lightmap = lightmap
    def read_body(self, r):
        if self.TAG == 0x4311: self.ltype = r.u8()
        self.intensity = r.f32(); self.color = _read_vec3_chunk(r, 0xF030); self.a = r.f32(); self.b = r.f32()
        self.lightmap = _read_opt_string_chunk(r)
        if r.p < r.end: self.tail = r.rest()
    def write(self, w):
        w.open(self.TAG)
        if self.TAG == 0x4311: w.u8(self.ltype)
        w.f32(self.intensity); w.chunk_vec3(0xF030, self.color); w.f32(self.a); w.f32(self.b)
        if self.lightmap is not None: w.chunk_string(self.lightmap)
        w.block(self.tail); w.close()


class LightParamsOld(LightParams):
    TAG = 0x4310


class LightFlare(Chunk):
    """0x4320: 4 x f32 ; f020 star texture ; f020 flare texture (lens flare)"""
    TAG = 0x4320
    def __init__(self): super().__init__(); self.f = (0.0, 0.0, 0.0, 0.0); self.star = None; self.flare = None
    def read_body(self, r):
        self.f = tuple(r.f32() for _ in range(4)); self.star = _read_opt_string_chunk(r); self.flare = _read_opt_string_chunk(r)
        if r.p < r.end: self.tail = r.rest()
    def write(self, w):
        w.open(self.TAG)
        for x in self.f: w.f32(x)
        if self.star is not None or self.flare is not None:   # exporter files omit both strings entirely
            w.chunk_string(self.star); w.chunk_string(self.flare)
        w.block(self.tail); w.close()


class LightSpot(Chunk):
    """0x4330: f040 direction ; f32 ; f32 (spot cone parameters)"""
    TAG = 0x4330
    def __init__(self): super().__init__(); self.dir = (0.0, 0.0, 1.0); self.a = 0.0; self.b = 0.0
    def read_body(self, r):
        self.dir = _read_vec3_chunk(r); self.a = r.f32(); self.b = r.f32()
        if r.p < r.end: self.tail = r.rest()
    def write(self, w):
        w.open(self.TAG); w.chunk_vec3(0xF040, self.dir); w.f32(self.a); w.f32(self.b); w.block(self.tail); w.close()


class Light(SrtEntity):
    """0x4300 light. head like Null (name, ref, room, parent, pos, scale, rot). children 0x4310/0x4311/0x4320/0x4330/f0b0"""
    TAG = 0x4300
    CHILD_TYPES = {0x4310: LightParamsOld, 0x4311: LightParams, 0x4320: LightFlare, 0x4330: LightSpot, 0xF0B0: F0B0Int}
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.room_ref = 0
    def read_head(self, r):
        self._read_name_ref(r); self.room_ref = r.i32(); self.parent_ref = r.i32(); self._read_srt(r)
    def write_head(self, w):
        self._write_name_ref(w); w.i32(self.room_ref); w.i32(self.parent_ref); self._write_srt(w)
    @property
    def params(self) -> Optional[LightParams]:
        return self.find(LightParams)


class Object(SrtEntity):
    """0x4100 mesh instance.
    head: f010 ; i32 ref ; i32 mesh ref ; i32 room ref ; i32 parent ref ; u8 hidden ; i32 portal type ; i32 portal room ref ; f040 pos ; f32 scale ; f070 rot
    children: f0b0 i32 (CcObject+0x166, static/collision flag) ; f0b1 i32 (CcObject+0x167) ; 0x4101 dynamic BSP
    """
    TAG = 0x4100
    CHILD_TYPES = {0xF0B0: F0B0Int, 0xF0B1: F0B1Int, 0x4101: DynamicBsp}
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.mesh_ref = 0; self.room_ref = 0; self.hidden = 0; self.portal_type = 0; self.portal_room_ref = 0
    def read_head(self, r):
        self._read_name_ref(r)
        self.mesh_ref = r.i32(); self.room_ref = r.i32(); self.parent_ref = r.i32()
        self.hidden = r.u8(); self.portal_type = r.i32(); self.portal_room_ref = r.i32()
        self._read_srt(r)
    def write_head(self, w):
        self._write_name_ref(w)
        w.i32(self.mesh_ref); w.i32(self.room_ref); w.i32(self.parent_ref)
        w.u8(self.hidden); w.i32(self.portal_type); w.i32(self.portal_room_ref)
        self._write_srt(w)
    @property
    def flag_b0(self) -> int:
        c = self.find(F0B0Int); return c.v if c else 0
    @flag_b0.setter
    def flag_b0(self, v): self.replace(F0B0Int, F0B0Int(v))
    @property
    def flag_b1(self) -> int:
        c = self.find(F0B1Int); return c.v if c else 0
    @flag_b1.setter
    def flag_b1(self, v): self.replace(F0B1Int, F0B1Int(v))
    def __repr__(self): return f"<Object {self.name!r} ref={self.ref:#x} mesh={self.mesh_ref:#x}>"


# ----------------------------------------------------------------------------------------------
# sections + root
# ----------------------------------------------------------------------------------------------
class Rooms(Chunk):
    TAG = 0x1000
    CHILD_TYPES = {0x1100: Room}


class Materials(Chunk):
    TAG = 0x2000
    CHILD_TYPES = {0x2100: Material}


class Blueprints(Chunk):
    TAG = 0x3000
    CHILD_TYPES = {0x3100: Mesh, 0x4200: Null, 0x4300: Light}


class Instances(Chunk):
    TAG = 0x4000
    CHILD_TYPES = {0x4100: Object, 0x4200: Null, 0x4300: Light}


class Scene(Chunk):
    TAG = 0x0001
    CHILD_TYPES = {0x1000: Rooms, 0x2000: Materials, 0x3000: Blueprints, 0x4000: Instances}

    def __init__(self):
        super().__init__()
        self.version = VERSION

    # ---- sections (created on demand)
    def _section(self, cls):
        s = self.find(cls)
        if s is None:
            s = cls()
            # keep canonical order 1000,2000,3000,4000
            self.children.append(s)
            self.children.sort(key=lambda c: c.TAG if c.TAG in (0x1000, 0x2000, 0x3000, 0x4000) else 0x9000)
        return s

    @property
    def rooms(self) -> List[Room]:
        s = self.find(Rooms); return s.find_all(Room) if s else []
    @property
    def materials(self) -> List[Material]:
        s = self.find(Materials); return s.find_all(Material) if s else []
    @property
    def meshes(self) -> List[Mesh]:
        s = self.find(Blueprints); return s.find_all(Mesh) if s else []
    @property
    def blueprint_nulls(self) -> List[Null]:
        s = self.find(Blueprints); return s.find_all(Null) if s else []
    @property
    def blueprint_lights(self) -> List[Light]:
        s = self.find(Blueprints); return s.find_all(Light) if s else []
    @property
    def objects(self) -> List[Object]:
        s = self.find(Instances); return s.find_all(Object) if s else []
    @property
    def nulls(self) -> List[Null]:
        s = self.find(Instances); return s.find_all(Null) if s else []
    @property
    def lights(self) -> List[Light]:
        s = self.find(Instances); return s.find_all(Light) if s else []

    def add_room(self, r: Room): self._section(Rooms).children.append(r)
    def add_material(self, m: Material): self._section(Materials).children.append(m)
    def add_mesh(self, m: Mesh): self._section(Blueprints).children.append(m)
    def add_blueprint(self, e): self._section(Blueprints).children.append(e)
    def add_instance(self, e): self._section(Instances).children.append(e)

    def remove(self, entity: Chunk) -> bool:
        for sec in self.children:
            if entity in sec.children:
                sec.children.remove(entity); return True
        return False

    # ---- lookups
    def ref_map(self) -> dict:
        m = {}
        for lst in (self.rooms, self.materials, self.meshes, self.blueprint_nulls, self.blueprint_lights, self.objects, self.nulls, self.lights):
            for e in lst:
                m[e.ref] = e
        return m

    def all_entities(self):
        for lst in (self.rooms, self.materials, self.meshes, self.blueprint_nulls, self.blueprint_lights, self.objects, self.nulls, self.lights):
            yield from lst

    def material_by_ref(self, ref: int) -> Optional[Material]:
        for m in self.materials:
            if m.ref == ref: return m
        return None

    def mesh_by_ref(self, ref: int) -> Optional[Mesh]:
        for m in self.meshes:
            if m.ref == ref: return m
        return None

    def new_ref(self) -> int:
        used = set(self.ref_map())
        n = 0x10000
        while n in used: n += 0x10
        return n

    def renumber_refs(self):
        """Give every entity a fresh unique ref and fix all cross references."""
        ents = list(self.all_entities())
        old2new = {}
        for i, e in enumerate(ents):
            old2new.setdefault(e.ref, 0x10000 + i * 0x10)
        fix = lambda x: old2new.get(x, 0) if x else 0
        for e in ents:
            e.ref = old2new[e.ref]
        for e in ents:
            if isinstance(e, SrtEntity): e.parent_ref = fix(e.parent_ref)
            if isinstance(e, (Null, Light)): e.room_ref = fix(e.room_ref)
            if isinstance(e, Object):
                e.mesh_ref = fix(e.mesh_ref); e.room_ref = fix(e.room_ref); e.portal_room_ref = fix(e.portal_room_ref)
            if isinstance(e, Mesh):
                for p in e.polygons: p.material_ref = fix(p.material_ref)
        for room in self.rooms:
            for lst in room.find_all(BspList):
                for root in lst.roots:
                    _walk_bsp(root, lambda n: [setattr(p, "object_ref", fix(p.object_ref)) for p in n.polys])
        for o in self.objects:
            for lst in o.find_all(DynamicBsp):
                for root in lst.roots:
                    _walk_bsp(root, lambda n: [setattr(p, "object_ref", fix(p.object_ref)) for p in n.polys])

    # ---- io
    def write_bytes(self) -> bytes:
        w = ChunkWriter()
        w.block(MAGIC); w.u32(self.version)
        self.write(w)
        return bytes(w.buf)

    def save(self, path: str):
        with open(path, "wb") as f:
            f.write(self.write_bytes())

    @staticmethod
    def from_bytes(data: bytes) -> "Scene":
        if data[:4] != MAGIC:
            raise ValueError("not a CcFf file")
        r = ChunkReader(data)
        r.p = 4
        sc = Scene()
        sc.version = r.u32()
        tag = r.open()
        if tag != 0x0001:
            raise ValueError(f"unexpected root chunk {tag:#x}")
        sc.read_body(r)
        r.close()
        sc.trailing = data[r.p:]
        return sc

    @staticmethod
    def load(path: str) -> "Scene":
        with open(path, "rb") as f:
            return Scene.from_bytes(f.read())


def _walk_bsp(node: BspNode, fn):
    fn(node)
    if node.neg: _walk_bsp(node.neg, fn)
    if node.pos: _walk_bsp(node.pos, fn)


# ----------------------------------------------------------------------------------------------
# text dump (debug)
# ----------------------------------------------------------------------------------------------
def dump(scene: Scene, verbose: bool = False) -> str:
    out = []
    out.append(f"version {scene.version:#x}")
    out.append(f"rooms: {len(scene.rooms)}")
    for rm in scene.rooms:
        sb = rm.static_bsp; pb = rm.portal_bsp
        out.append(f"  {rm!r} staticBSP={'%d nodes' % sum(n.count() for n in sb.roots) if sb else None} portalBSP={'%d nodes' % sum(n.count() for n in pb.roots) if pb else None} extra={[c for c in rm.children if isinstance(c, Raw)]}")
    out.append(f"materials: {len(scene.materials)}")
    for m in scene.materials:
        c = m.colors; f = m.flags; p = m.find(MatPhysics)
        out.append(f"  {m!r} tex={m.texture!r} colA={tuple(round(x,3) for x in c.a)} colB={tuple(round(x,3) for x in c.b)} f={c.f:.3f} mode={f.mode} blend={f.blend} flags={f.flags:#x} phys={(p.mtype, p.friction, p.absorb, p.extra) if p else None} extra={[x for x in m.children if isinstance(x, Raw)]}")
    out.append(f"meshes: {len(scene.meshes)}")
    for me in scene.meshes:
        extras = [c for c in me.children if not isinstance(c, (Vertex, Polygon))]
        out.append(f"  {me!r} flags={me.flag_a},{me.flag_b} parent={me.parent_ref:#x} pos={me.pos} scale={me.scale} extras={extras}")
        if verbose:
            for v in me.vertices[:4]: out.append(f"     {v!r} n={v.normal} c={v.color} extra={[c for c in v.children if isinstance(c, Raw)]}")
            for p in me.polygons[:4]: out.append(f"     {p!r} uv={p.uv} paint={(p.paint.ptype, p.paint.colors) if p.paint else None}")
    out.append(f"blueprint nulls: {len(scene.blueprint_nulls)}  blueprint lights: {len(scene.blueprint_lights)}")
    for n in scene.blueprint_nulls:
        out.append(f"  {n!r} room={n.room_ref:#x} parent={n.parent_ref:#x} pos={n.pos} data={len(n.data) if n.data else None}")
    out.append(f"objects: {len(scene.objects)}")
    for o in scene.objects[: (None if verbose else 20)]:
        out.append(f"  {o!r} room={o.room_ref:#x} parent={o.parent_ref:#x} hidden={o.hidden} portal={o.portal_type},{o.portal_room_ref:#x} pos={tuple(round(x,3) for x in o.pos)} scale={o.scale} b0={o.flag_b0} b1={o.flag_b1} dynbsp={o.find(DynamicBsp) is not None}")
    out.append(f"nulls: {len(scene.nulls)}")
    for n in scene.nulls[: (None if verbose else 40)]:
        out.append(f"  {n!r} room={n.room_ref:#x} parent={n.parent_ref:#x} pos={tuple(round(x,3) for x in n.pos)} scale={n.scale} data={n.data[:16] if n.data else None}")
    out.append(f"lights: {len(scene.lights)}")
    for l in scene.lights:
        p = l.params
        out.append(f"  {l!r} pos={tuple(round(x,3) for x in l.pos)} params={(p.TAG, p.ltype, p.intensity, p.color, p.a, p.b, p.lightmap) if p else None} children={l.children}")
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    for path in sys.argv[1:]:
        sc = Scene.load(path)
        print("=" * 10, path)
        print(dump(sc, verbose="-v" in sys.argv))
