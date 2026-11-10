"""
High level, tool-friendly scene model ("IR") shared by the OBJ and Blender importers/exporters and
by the editor.  It sits between the raw chunk tree (ccf.py) and user facing formats.

Conventions
-----------
* Coordinates are the engine's: Y up, left-handed.  Conversion helpers for Blender (Z up,
  right-handed) are at the bottom of the file.
* Object transforms are WORLD space (that is how the shipped files store them).  `parent` is only a
  hierarchy link used by the engine (e.g. wheels under a vehicle root).
* Node-name suffixes carry gameplay information (see constants.NAME_SUFFIXES):
      road-collision     collidable surface (object.flag_b1 = 1; polygons go into the room's static BSP)
      lake-water         collidable water: every material used gets surface type Water (vehicle respawns)
      wall-concrete      collidable, material type Concrete (any name in MATERIAL_TYPES works: -tree ...)
      door-portal        portal polygons between rooms (custom property cc_portal_room names the target)
      sky-backdrop       backdrop mesh
      start1-spawn       point types for empties/null points: -spawn -pickup -drop -fuel -ai
  A trailing Blender ".001" counter is ignored; suffixes may be combined in any order.
"""
from __future__ import annotations
import math, re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import ccf
from .constants import (MATERIAL_TYPES, MATERIAL_TYPE_IDS, NULL_TYPES, NULL_TYPE_TAGS, POINT_SUFFIXES,
                        SUFFIX_COLLISION, SUFFIX_PORTAL, SUFFIX_BACKDROP, SUFFIX_NOCOLLISION, SUFFIX_WATER,
                        NULL_PACKAGE_RECORD_SIZE, NULL_LIST_A_OFFSET, NULL_LIST_B_OFFSET)

Vec3 = Tuple[float, float, float]
Mat3 = Tuple[Vec3, Vec3, Vec3]
IDENT: Mat3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


# ----------------------------------------------------------------------------------------------
# name suffix handling
# ----------------------------------------------------------------------------------------------
_COUNTER = re.compile(r"\.\d{3,}$")
_FLAG_SUFFIXES = [SUFFIX_COLLISION, SUFFIX_NOCOLLISION, SUFFIX_PORTAL, SUFFIX_BACKDROP]
_SURFACE_SUFFIXES = {"-" + n.lower(): tid for tid, n in MATERIAL_TYPES.items() if tid != 0}


@dataclass
class ParsedName:
    base: str
    collision: bool = False
    surface: int = 0                 # material type id (0 = none)
    portal: bool = False
    backdrop: bool = False
    nocollision: bool = False
    point_kind: Optional[str] = None  # spawn / pickup / drop / fuel / ai


def parse_name(name: str) -> ParsedName:
    """Split a node name into its base name and the gameplay suffixes."""
    s = name or ""
    out = ParsedName(base=s)
    changed = True
    while changed:
        changed = False
        s = _COUNTER.sub("", s)
        low = s.lower()
        for suf in _FLAG_SUFFIXES + list(_SURFACE_SUFFIXES) + list(POINT_SUFFIXES):
            if len(s) > len(suf) and low.endswith(suf):
                if suf == SUFFIX_COLLISION: out.collision = True
                elif suf == SUFFIX_NOCOLLISION: out.nocollision = True
                elif suf == SUFFIX_PORTAL: out.portal = True
                elif suf == SUFFIX_BACKDROP: out.backdrop = True
                elif suf in _SURFACE_SUFFIXES: out.surface = _SURFACE_SUFFIXES[suf]
                else: out.point_kind = POINT_SUFFIXES[suf]
                s = s[:-len(suf)]; changed = True
                break
    out.base = s
    if (out.surface or out.portal) and not out.nocollision:
        out.collision = True
    return out


def build_name(base: str, collision=False, surface: int = 0, portal=False, backdrop=False, point_kind=None) -> str:
    n = base
    if point_kind:
        n += "-" + point_kind
    if backdrop: n += SUFFIX_BACKDROP
    if portal: n += SUFFIX_PORTAL
    if surface: n += "-" + MATERIAL_TYPES.get(surface, "custom%d" % surface).lower()
    elif collision and not portal: n += SUFFIX_COLLISION
    return n


# ----------------------------------------------------------------------------------------------
# IR dataclasses
# ----------------------------------------------------------------------------------------------
@dataclass
class IRMaterial:
    name: str
    texture: Optional[str] = None
    color_a: Vec3 = (0.6, 0.6, 0.6)
    color_b: Vec3 = (1.0, 1.0, 1.0)
    f: float = 1.0
    mode: int = 2
    blend: int = 1
    flags: int = 0
    mtype: int = 0
    friction: float = 0.0
    absorb: float = 0.9
    extra: float = 0.0

    def clone(self, name: str, mtype: int) -> "IRMaterial":
        import dataclasses
        c = dataclasses.replace(self); c.name = name; c.mtype = mtype
        return c


@dataclass
class IRMesh:
    verts: List[Vec3] = field(default_factory=list)
    normals: List[Optional[Vec3]] = field(default_factory=list)
    faces: List[Tuple[int, int, int]] = field(default_factory=list)
    face_mats: List[str] = field(default_factory=list)                      # material name per face
    face_uvs: List[Optional[Tuple[Tuple[float, float], ...]]] = field(default_factory=list)   # 3 (u,v) per face (engine v)
    face_colors: List[Optional[Tuple[Vec3, ...]]] = field(default_factory=list)               # 3 rgb per face
    vampire: int = 0

    def compute_normals(self):
        acc = [[0.0, 0.0, 0.0] for _ in self.verts]
        for a, b, c in self.faces:
            n = face_normal(self.verts[a], self.verts[b], self.verts[c])
            for i in (a, b, c):
                acc[i][0] += n[0]; acc[i][1] += n[1]; acc[i][2] += n[2]
        self.normals = [normalize(tuple(x)) if any(x) else (0.0, 1.0, 0.0) for x in acc]


@dataclass
class IRNode:
    name: str                       # base name, without suffixes
    pos: Vec3 = (0.0, 0.0, 0.0)
    matrix: Mat3 = IDENT
    scale: float = 1.0
    parent: Optional[str] = None    # unique key of the parent node
    room: str = ""                  # room name ("" = unnamed room)
    prefix: Optional[str] = None
    key: str = ""                   # unique key inside the scene (defaults to name)


@dataclass
class IRObject(IRNode):
    mesh: IRMesh = field(default_factory=IRMesh)
    collision: bool = False
    surface: int = 0
    portal: bool = False
    portal_room: str = ""
    backdrop: bool = False
    static: int = 1                 # f0b0
    hidden: int = 1
    show: int = 1                   # mesh f0b2

    @property
    def full_name(self) -> str:
        return build_name(self.name, self.collision, self.surface, self.portal, self.backdrop)


@dataclass
class IRPoint(IRNode):
    kind: str = "null"              # null / spawn / pickup / drop / fuel / ai
    packages_a: List[str] = field(default_factory=list)
    packages_b: List[str] = field(default_factory=list)
    enabled: int = 1
    raw_data: Optional[bytes] = None    # preserved for unknown null user data

    @property
    def full_name(self) -> str:
        return build_name(self.name, point_kind=self.kind if self.kind != "null" else None)


@dataclass
class IRLight(IRNode):
    ltype: int = 1
    intensity: float = 1.0
    color: Vec3 = (1.0, 1.0, 1.0)
    range_a: float = 0.0
    range_b: float = 20.0
    lightmap: Optional[str] = None
    flare: Optional[Tuple[float, float, float, float]] = None
    spot_dir: Vec3 = (0.0, 0.0, 1.0)
    spot_a: float = math.pi
    spot_b: float = math.pi
    flag_b0: int = 1


@dataclass
class IRScene:
    materials: Dict[str, IRMaterial] = field(default_factory=dict)
    objects: List[IRObject] = field(default_factory=list)
    points: List[IRPoint] = field(default_factory=list)
    lights: List[IRLight] = field(default_factory=list)
    rooms: List[str] = field(default_factory=list)       # names; "" is the unnamed room

    def ensure_room(self, name: str):
        if name not in self.rooms:
            self.rooms.append(name)

    def material(self, name: str) -> IRMaterial:
        m = self.materials.get(name)
        if m is None:
            m = self.materials[name] = IRMaterial(name, texture=None if name.startswith("mat") else name)
        return m


# ----------------------------------------------------------------------------------------------
# vector helpers
# ----------------------------------------------------------------------------------------------
def sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def normalize(a):
    l = math.sqrt(dot(a, a))
    return (a[0] / l, a[1] / l, a[2] / l) if l > 1e-12 else (0.0, 0.0, 0.0)


def face_normal(a, b, c) -> Vec3:
    """Outward normal of an engine face.  The engine winds front faces so that cross(e1, e2) points
    BACK (verified against 6488 of 6578 stored vertex normals); the outward normal is the negation."""
    return normalize(tuple(-x for x in cross(sub(b, a), sub(c, b))))


def mat_apply(m: Mat3, v: Vec3, scale: float = 1.0, pos: Vec3 = (0, 0, 0)) -> Vec3:
    """world = v * scale  @ M (rows are basis vectors) + pos"""
    x, y, z = v[0] * scale, v[1] * scale, v[2] * scale
    return (x * m[0][0] + y * m[1][0] + z * m[2][0] + pos[0],
            x * m[0][1] + y * m[1][1] + z * m[2][1] + pos[1],
            x * m[0][2] + y * m[1][2] + z * m[2][2] + pos[2])


def mat_apply_dir(m: Mat3, v: Vec3) -> Vec3:
    return mat_apply(m, v)


# ----------------------------------------------------------------------------------------------
# null user data
# ----------------------------------------------------------------------------------------------
def _split_list(raw: bytes) -> List[str]:
    s = raw.split(b"\0", 1)[0].decode("latin-1")
    return [x for x in s.replace("\r", "").split("\n") if x]


def parse_point_data(data: Optional[bytes]) -> dict:
    if not data:
        return {"kind": "null"}
    tag = data[:4].decode("latin-1")
    kind = NULL_TYPES.get(tag)
    if kind is None:
        return {"kind": "null", "raw_data": data}
    out = {"kind": kind}
    if kind in ("pickup", "pickup_v0", "drop") and len(data) >= NULL_PACKAGE_RECORD_SIZE:
        out["enabled"] = data[4]
        out["packages_a"] = _split_list(data[NULL_LIST_A_OFFSET:NULL_LIST_B_OFFSET])
        out["packages_b"] = _split_list(data[NULL_LIST_B_OFFSET:NULL_PACKAGE_RECORD_SIZE])
        if kind == "pickup_v0": out["kind"] = "pickup"
    return out


def build_point_data(p: IRPoint) -> Optional[bytes]:
    if p.kind == "null":
        return p.raw_data
    tag = NULL_TYPE_TAGS[p.kind].encode("latin-1")
    if p.kind in ("pickup", "drop"):
        a = "\r\n".join(p.packages_a).encode("latin-1")[:256]
        b = "\r\n".join(p.packages_b).encode("latin-1")[:255]
        return tag + bytes([p.enabled & 0xFF]) + a.ljust(257, b"\0") + b.ljust(256, b"\0")
    return tag


# ----------------------------------------------------------------------------------------------
# Scene -> IR
# ----------------------------------------------------------------------------------------------
def _uniq(name: str, used: set) -> str:
    if name not in used:
        used.add(name); return name
    i = 1
    while f"{name}~{i}" in used: i += 1
    used.add(f"{name}~{i}"); return f"{name}~{i}"


def scene_to_ir(scene: ccf.Scene, split_surfaces: bool = True) -> IRScene:
    ir = IRScene()
    refs = scene.ref_map()
    room_name = {r.ref: (r.name or "") for r in scene.rooms}
    for r in scene.rooms:
        ir.ensure_room(r.name or "")
    # materials
    mat_by_ref: Dict[int, str] = {}
    used: set = set()
    for m in scene.materials:
        nm = _uniq(m.name or (m.texture or "material"), used)
        mat_by_ref[m.ref] = nm
        c, f, p = m.colors, m.flags, m.physics
        ir.materials[nm] = IRMaterial(nm, m.texture, c.a, c.b, c.f, f.mode, f.blend, f.flags, p.mtype, p.friction, p.absorb, p.extra)
    # unique keys for hierarchy
    key_of: Dict[int, str] = {}
    keys: set = set()
    for e in list(scene.objects) + list(scene.nulls) + list(scene.lights):
        key_of[e.ref] = _uniq(f"{e.name or 'node'}", keys)
    # objects
    for o in scene.objects:
        mesh = scene.mesh_by_ref(o.mesh_ref)
        im = IRMesh()
        if mesh is not None:
            for v in mesh.vertices:
                im.verts.append(v.pos); im.normals.append(v.normal)
            im.vampire = (mesh.find(ccf.F0A0Int).v if mesh.find(ccf.F0A0Int) else 0)
            for p in mesh.polygons:
                im.faces.append(p.indices)
                mn = mat_by_ref.get(p.material_ref)
                if mn is None:
                    mn = "default"; ir.material(mn)
                im.face_mats.append(mn)
                im.face_uvs.append(p.uv)
                im.face_colors.append(tuple(p.paint.colors) if p.paint and len(p.paint.colors) == 3 else
                                      (tuple([p.paint.colors[0]] * 3) if p.paint and p.paint.colors else None))
        prt = refs.get(o.portal_room_ref)
        base = o.name or "object"
        io = IRObject(name=base, pos=o.pos, matrix=o.matrix, scale=o.scale, parent=key_of.get(o.parent_ref),
                      room=room_name.get(o.room_ref, ""), prefix=o.prefix, key=key_of[o.ref], mesh=im,
                      collision=bool(o.flag_b1), portal=(o.portal_type == 0), portal_room=room_name.get(o.portal_room_ref, "") if o.portal_room_ref else "",
                      backdrop=bool(mesh and mesh.flag_a), static=o.flag_b0, hidden=o.hidden,
                      show=(mesh.find(ccf.F0B2Byte).v if mesh and mesh.find(ccf.F0B2Byte) else 1))
        ir.objects.append(io)
    if split_surfaces:
        _split_surface_objects(ir)
    for n in scene.nulls:
        d = parse_point_data(n.data)
        ir.points.append(IRPoint(name=n.name or "null", pos=n.pos, matrix=n.matrix, scale=n.scale, parent=key_of.get(n.parent_ref),
                                 room=room_name.get(n.room_ref, ""), prefix=n.prefix, key=key_of[n.ref], **d))
    for l in scene.lights:
        pr = l.params; fl = l.find(ccf.LightFlare); sp = l.find(ccf.LightSpot); b0 = l.find(ccf.F0B0Int)
        il = IRLight(name=l.name or "light", pos=l.pos, matrix=l.matrix, scale=l.scale, parent=key_of.get(l.parent_ref),
                     room=room_name.get(l.room_ref, ""), prefix=l.prefix, key=key_of[l.ref])
        if pr:
            il.ltype, il.intensity, il.color, il.range_a, il.range_b, il.lightmap = pr.ltype, pr.intensity, pr.color, pr.a, pr.b, pr.lightmap
        if fl and any(fl.f): il.flare = tuple(fl.f)
        if sp: il.spot_dir, il.spot_a, il.spot_b = sp.dir, sp.a, sp.b
        il.flag_b0 = b0.v if b0 else 1
        ir.lights.append(il)
    return ir


def _split_surface_objects(ir: IRScene):
    """Objects whose polygons use materials of a special surface type are tagged/split so the type is
    expressed through the node-name suffix (-water, -concrete ...)."""
    out: List[IRObject] = []
    for o in ir.objects:
        groups: Dict[int, List[int]] = {}
        for i, mn in enumerate(o.mesh.face_mats):
            groups.setdefault(ir.materials[mn].mtype if mn in ir.materials else 0, []).append(i)
        if len(groups) <= 1:
            o.surface = next(iter(groups), 0)
            out.append(o); continue
        for k, (mt, idxs) in enumerate(sorted(groups.items())):
            part = _extract_faces(o, idxs)
            part.surface = mt
            if k: part.key = o.key + "~" + str(mt)
            part.name = o.name
            out.append(part)
    ir.objects = out


def _extract_faces(o: IRObject, idxs: List[int]) -> IRObject:
    import dataclasses
    m = o.mesh; remap: Dict[int, int] = {}
    nm = IRMesh(vampire=m.vampire)
    for fi in idxs:
        f = []
        for vi in m.faces[fi]:
            if vi not in remap:
                remap[vi] = len(nm.verts); nm.verts.append(m.verts[vi]); nm.normals.append(m.normals[vi] if vi < len(m.normals) else None)
            f.append(remap[vi])
        nm.faces.append(tuple(f)); nm.face_mats.append(m.face_mats[fi]); nm.face_uvs.append(m.face_uvs[fi]); nm.face_colors.append(m.face_colors[fi])
    c = dataclasses.replace(o); c.mesh = nm
    return c


# ----------------------------------------------------------------------------------------------
# IR -> Scene
# ----------------------------------------------------------------------------------------------
class _Refs:
    def __init__(self): self.n = 0
    def next(self) -> int:
        self.n += 1; return 0x10000 + self.n * 0x10


def ir_to_scene(ir: IRScene, build_bsp: bool = True, bsp_samples: int = 12, progress=None) -> ccf.Scene:
    sc = ccf.Scene()
    R = _Refs()
    # rooms (unnamed room first, as in the shipped files)
    room_names = [""] + [r for r in ir.rooms if r != ""]
    for o in ir.objects + ir.points + ir.lights:      # make sure every used room exists
        if o.room not in room_names: room_names.append(o.room)
        if getattr(o, "portal_room", "") and o.portal_room not in room_names: room_names.append(o.portal_room)
    room_ref: Dict[str, int] = {}
    for rn in room_names:
        room = ccf.Room(rn or None, None, R.next()); room_ref[rn] = room.ref; sc.add_room(room)
    # materials; surface clones
    mats: Dict[str, ccf.Material] = {}
    def get_mat(mname: str, surface: int) -> ccf.Material:
        src = ir.material(mname)
        key = mname if (not surface or src.mtype == surface) else f"{mname}-{MATERIAL_TYPES.get(surface, 's%d' % surface).lower()}"
        if key in mats: return mats[key]
        im = src if key == mname else src.clone(key, surface)
        if surface and im.mtype != surface: im = im.clone(key, surface)
        m = ccf.Material(key, None, R.next())
        m.texture = im.texture
        m.children.append(ccf.MatColors(im.color_a, im.color_b, im.f))
        m.children.append(ccf.MatFlags(im.mode, im.blend, im.flags))
        m.children.append(ccf.MatPhysics(im.mtype, im.friction, im.absorb, im.extra))
        sc.add_material(m); mats[key] = m
        return m
    # sort objects so that parents come first
    keymap_obj: Dict[str, Tuple[ccf.Object, ccf.Mesh]] = {}
    keymap_all: Dict[str, Tuple[int, int]] = {}      # key -> (instance ref, blueprint ref)
    # pass 1: allocate refs
    inst_ref: Dict[str, int] = {}; bp_ref: Dict[str, int] = {}
    nodes = list(ir.objects) + list(ir.points) + list(ir.lights)
    used = set()
    for n in nodes:
        k = n.key or n.name
        if k in used:
            i = 1
            while f"{k}~{i}" in used: i += 1
            k = f"{k}~{i}"
        used.add(k); n.key = k
        inst_ref[k] = R.next(); bp_ref[k] = R.next()
    for idx, o in enumerate(ir.objects):
        if progress: progress(idx, len(ir.objects), o.name)
        m = ccf.Mesh(o.name, o.prefix, bp_ref[o.key])
        m.flag_a = 1 if o.backdrop else 0
        m.flag_b = 1 if o.portal else 0
        m.parent_ref = bp_ref.get(o.parent, 0) if o.parent else 0
        m.pos = o.pos; m.scale = o.scale; m.matrix = o.matrix
        im = o.mesh
        if len(im.normals) != len(im.verts) or any(n is None for n in im.normals):
            im.compute_normals()
        verts = []
        for p, n in zip(im.verts, im.normals):
            v = ccf.Vertex(p, n); v.children.append(ccf.Raw(0x4500, b"\0\0\0\0")); verts.append(v)
        polys = []
        for fi, f in enumerate(im.faces):
            mat = get_mat(im.face_mats[fi] if fi < len(im.face_mats) else "default", o.surface)
            paint = None
            cols = im.face_colors[fi] if fi < len(im.face_colors) else None
            if cols is None and mat.find(ccf.MatFlags).mode != 0:
                ca = mat.colors.a; cols = (ca, ca, ca)
            if cols is not None:
                paint = ccf.F090Paint(3, cols)
            uv = im.face_uvs[fi] if fi < len(im.face_uvs) else None
            polys.append(ccf.Polygon(f, mat.ref, uv, paint))
        m.set_geometry(verts, polys)
        m.children.append(ccf.F0A0Int(im.vampire)); m.children.append(ccf.F0B2Byte(o.show))
        sc.add_mesh(m)
        ob = ccf.Object(o.name, o.prefix, inst_ref[o.key])
        ob.mesh_ref = m.ref; ob.room_ref = room_ref[o.room]; ob.parent_ref = inst_ref.get(o.parent, 0) if o.parent else 0
        ob.hidden = o.hidden
        ob.portal_type = 0 if o.portal else -1
        ob.portal_room_ref = room_ref.get(o.portal_room, 0) if o.portal and o.portal_room else 0
        ob.pos = o.pos; ob.scale = o.scale; ob.matrix = o.matrix
        ob.flag_b0 = o.static; ob.flag_b1 = 1 if (o.collision or o.surface or o.portal) else 0
        sc.add_instance(ob)
    for p in ir.points:
        data = build_point_data(p)
        for inst in (False, True):
            n = ccf.Null(p.name, p.prefix, inst_ref[p.key] if inst else bp_ref[p.key])
            n.room_ref = room_ref[p.room] if inst else 0
            n.parent_ref = (inst_ref if inst else bp_ref).get(p.parent, 0) if p.parent else 0
            n.pos = p.pos; n.scale = p.scale; n.matrix = p.matrix
            if data is not None: n.data = data
            (sc.add_instance if inst else sc.add_blueprint)(n)
    for l in ir.lights:
        for inst in (False, True):
            e = ccf.Light(l.name, l.prefix, inst_ref[l.key] if inst else bp_ref[l.key])
            e.room_ref = room_ref[l.room] if inst else 0
            e.parent_ref = (inst_ref if inst else bp_ref).get(l.parent, 0) if l.parent else 0
            e.pos = l.pos; e.scale = l.scale; e.matrix = l.matrix
            e.children.append(ccf.LightParams(l.ltype, l.intensity, l.color, l.range_a, l.range_b, l.lightmap))
            e.children.append(ccf.LightFlare())
            if l.flare: e.find(ccf.LightFlare).f = tuple(l.flare)
            sp = ccf.LightSpot(); sp.dir, sp.a, sp.b = l.spot_dir, l.spot_a, l.spot_b
            e.children.append(sp); e.children.append(ccf.F0B0Int(l.flag_b0))
            (sc.add_instance if inst else sc.add_blueprint)(e)
    if build_bsp:
        from . import bsp
        bsp.rebuild_all(sc, samples=bsp_samples)
    return sc


# ----------------------------------------------------------------------------------------------
# engine <-> Blender coordinate conversion  (swap Y/Z: left-handed Y-up  ->  right-handed Z-up)
# ----------------------------------------------------------------------------------------------
def cc_to_blender_vec(v: Vec3) -> Vec3:
    return (v[0], v[2], v[1])


blender_to_cc_vec = cc_to_blender_vec          # the swap is its own inverse


def cc_to_blender_matrix(m: Mat3) -> Mat3:
    """Engine rotation (rows = basis vectors, world = v @ R) -> Blender 3x3 (columns = basis, world = M @ v)."""
    # M_b = S R^T S
    def S(i): return (0, 2, 1)[i]
    return tuple(tuple(m[S(j)][S(i)] for j in range(3)) for i in range(3))


def blender_to_cc_matrix(mb: Mat3) -> Mat3:
    # R = S M_b^T S
    def S(i): return (0, 2, 1)[i]
    return tuple(tuple(mb[S(j)][S(i)] for j in range(3)) for i in range(3))
