"""
Wavefront OBJ/MTL exporter and importer for Pickup Express scenes.

Everything needed to rebuild a playable .ccf is stored in the files:

  * node names carry gameplay types through suffixes (-collision, -water, -concrete, -tree, -portal,
    -backdrop, and for points -spawn -pickup -drop -fuel -ai)       -> see model.py
  * a `# cc ...` comment line after each `o` line carries the remaining node data
    (room, parent, prefix, transform of points/lights, package lists, light parameters ...)
  * materials keep texture, shading mode, flags and physics in `# cc ...` lines of the .mtl

Coordinates: the engine is left-handed Y-up; OBJ is right-handed, so Z is negated on export
(face winding stays valid, see model.face_normal) and negated again on import.  Blender's stock OBJ
importer then yields exactly the same placement as the dedicated Blender add-on.
UVs use the OBJ convention (v = 1 - engine v).
"""
from __future__ import annotations
import math, os, re, shlex
from typing import Dict, List, Optional, Tuple

from . import model
from .model import IRScene, IRObject, IRPoint, IRLight, IRMesh, IRMaterial
from .constants import MATERIAL_TYPES, MATERIAL_TYPE_IDS


def _f(x: float) -> str:
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _flipz(v):
    return (v[0], v[1], -v[2])


def _q(s: str) -> str:
    return shlex.quote(s) if s else "''"


def _kv(**kw) -> str:
    return " ".join(f"{k}={_q(str(v))}" for k, v in kw.items() if v not in (None, ""))


def _parse_kv(s: str) -> Dict[str, str]:
    out = {}
    for tok in shlex.split(s):
        if "=" in tok:
            k, v = tok.split("=", 1); out[k] = v
    return out


def _mat_flat(m: model.Mat3) -> str:
    return ",".join(_f(x) for r in m for x in r)


def _parse_mat(s: str) -> model.Mat3:
    v = [float(x) for x in s.split(",")]
    return ((v[0], v[1], v[2]), (v[3], v[4], v[5]), (v[6], v[7], v[8]))


# ----------------------------------------------------------------------------------------------
# export
# ----------------------------------------------------------------------------------------------
def export_obj(ir: IRScene, path: str, texture_dirs: Optional[List[str]] = None, export_textures: bool = True) -> dict:
    """Write path (.obj) and a sibling .mtl (+ textures/*.png when texture_dirs are given)."""
    base = os.path.splitext(path)[0]
    mtl_path = base + ".mtl"
    out = [f"# Pickup Express scene exported by pickup-express-tools", f"mtllib {os.path.basename(mtl_path)}"]
    vi = ti = ni = 0
    for o in ir.objects:
        out.append(f"o {o.full_name}")
        out.append("# cc " + _kv(type="object", room=o.room, key=o.key, parent=o.parent, prefix=o.prefix,
                                  static=o.static, hidden=o.hidden, show=o.show, portal_room=o.portal_room if o.portal else None))
        m = o.mesh
        wv = [_flipz(model.mat_apply(o.matrix, v, o.scale, o.pos)) for v in m.verts]
        if len(m.normals) != len(m.verts) or any(n is None for n in m.normals):
            m.compute_normals()
        wn = [_flipz(model.mat_apply(o.matrix, n)) for n in m.normals]
        wn = [model.normalize(n) for n in wn]
        for v in wv: out.append(f"v {_f(v[0])} {_f(v[1])} {_f(v[2])}")
        uvbase = ti
        uvlines = []
        for uv in m.face_uvs:
            for (u, v) in (uv or ((0, 0), (0, 0), (0, 0))):
                uvlines.append(f"vt {_f(u)} {_f(1.0 - v)}")
        out.extend(uvlines)
        for n in wn: out.append(f"vn {_f(n[0])} {_f(n[1])} {_f(n[2])}")
        cur = None
        for fi, f in enumerate(m.faces):
            mn = m.face_mats[fi] if fi < len(m.face_mats) else "default"
            if mn != cur:
                out.append(f"usemtl {mn}"); cur = mn
            out.append("f " + " ".join(f"{vi + f[k] + 1}/{uvbase + fi * 3 + k + 1}/{ni + f[k] + 1}" for k in range(3)))
        vi += len(wv); ti += len(uvlines); ni += len(wn)
    for p in ir.points:
        out.append(f"o {p.full_name}")
        out.append("# cc " + _kv(type="point", kind=p.kind, room=p.room, key=p.key, parent=p.parent, prefix=p.prefix, scale=_f(p.scale),
                                  matrix=_mat_flat(p.matrix), enabled=p.enabled,
                                  packages_a="|".join(p.packages_a), packages_b="|".join(p.packages_b),
                                  raw=p.raw_data.hex() if p.raw_data else None))
        v = _flipz(p.pos); out.append(f"v {_f(v[0])} {_f(v[1])} {_f(v[2])}"); vi += 1
        out.append(f"p {vi}")
    for l in ir.lights:
        out.append(f"o {l.name}")
        out.append("# cc " + _kv(type="light", room=l.room, key=l.key, parent=l.parent, prefix=l.prefix, scale=_f(l.scale), matrix=_mat_flat(l.matrix),
                                  ltype=l.ltype, intensity=_f(l.intensity), color=",".join(_f(c) for c in l.color),
                                  range=f"{_f(l.range_a)},{_f(l.range_b)}", lightmap=l.lightmap,
                                  flare=",".join(_f(c) for c in l.flare) if l.flare else None,
                                  spot=",".join(_f(c) for c in (*l.spot_dir, l.spot_a, l.spot_b)), b0=l.flag_b0))
        v = _flipz(l.pos); out.append(f"v {_f(v[0])} {_f(v[1])} {_f(v[2])}"); vi += 1
        out.append(f"p {vi}")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(out) + "\n")
    # mtl
    tex_dir = os.path.join(os.path.dirname(path) or ".", "textures")
    lines = ["# Pickup Express materials"]
    exported = 0
    for name, m in ir.materials.items():
        lines.append(f"newmtl {name}")
        lines.append(f"Kd {_f(m.color_a[0])} {_f(m.color_a[1])} {_f(m.color_a[2])}")
        lines.append(f"Ka {_f(m.color_b[0])} {_f(m.color_b[1])} {_f(m.color_b[2])}")
        if m.blend:
            lines.append("illum 2")
        if m.texture:
            png = m.texture + ".png"
            lines.append(f"map_Kd textures/{png}")
            if export_textures and texture_dirs:
                src = _find_texture(m.texture, texture_dirs)
                if src:
                    os.makedirs(tex_dir, exist_ok=True)
                    dst = os.path.join(tex_dir, png)
                    if not os.path.exists(dst):
                        from .gti import GtiFile
                        GtiFile.load(src).to_pil().save(dst); exported += 1
        lines.append("# cc " + _kv(texture=m.texture, mode=m.mode, blend=m.blend, flags=m.flags, type=MATERIAL_TYPES.get(m.mtype, m.mtype),
                                    friction=_f(m.friction), absorb=_f(m.absorb), extra=_f(m.extra), f=_f(m.f)))
        lines.append("")
    with open(mtl_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))
    return {"objects": len(ir.objects), "points": len(ir.points), "lights": len(ir.lights),
            "materials": len(ir.materials), "textures": exported}


def _find_texture(name: str, dirs: List[str]) -> Optional[str]:
    for d in dirs:
        for fn in (name + ".gti", name.lower() + ".gti"):
            p = os.path.join(d, fn)
            if os.path.isfile(p):
                return p
    # case-insensitive fallback
    for d in dirs:
        if os.path.isdir(d):
            for fn in os.listdir(d):
                if fn.lower() == name.lower() + ".gti":
                    return os.path.join(d, fn)
    return None


# ----------------------------------------------------------------------------------------------
# import
# ----------------------------------------------------------------------------------------------
def import_mtl(path: str, ir: IRScene):
    cur: Optional[IRMaterial] = None
    if not os.path.isfile(path):
        return
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if line.startswith("newmtl "):
            nm = line[7:].strip(); cur = ir.materials.setdefault(nm, IRMaterial(nm, None))
        elif cur is None:
            continue
        elif line.startswith("Kd "):
            cur.color_a = tuple(float(x) for x in line.split()[1:4])
        elif line.startswith("Ka "):
            cur.color_b = tuple(float(x) for x in line.split()[1:4])
        elif line.startswith("map_Kd "):
            cur.texture = os.path.splitext(os.path.basename(line[7:].strip().replace("\\", "/")))[0]
        elif line.startswith("# cc "):
            kv = _parse_kv(line[5:])
            if kv.get("texture"): cur.texture = kv["texture"]
            cur.mode = int(kv.get("mode", cur.mode)); cur.blend = int(kv.get("blend", cur.blend)); cur.flags = int(kv.get("flags", cur.flags))
            t = kv.get("type")
            if t is not None:
                cur.mtype = MATERIAL_TYPE_IDS.get(t.lower(), int(t) if t.lstrip("-").isdigit() else 0)
            cur.friction = float(kv.get("friction", cur.friction)); cur.absorb = float(kv.get("absorb", cur.absorb)); cur.extra = float(kv.get("extra", cur.extra))
            cur.f = float(kv.get("f", cur.f))


def import_obj(path: str) -> IRScene:
    ir = IRScene()
    V: List[Tuple[float, float, float]] = []; VT: List[Tuple[float, float]] = []; VN: List[Tuple[float, float, float]] = []
    cur = None          # dict for current object
    objs: List[dict] = []

    def new(name):
        d = {"name": name, "cc": {}, "faces": [], "points": [], "mat": None}
        objs.append(d); return d

    base = os.path.dirname(path)
    for raw in open(path, encoding="utf-8", errors="replace"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("mtllib "):
            import_mtl(os.path.join(base, line[7:].strip()), ir)
        elif line.startswith("o ") or line.startswith("g "):
            if line.startswith("g ") and cur is not None and cur["faces"]:
                continue
            cur = new(line[2:].strip())
        elif line.startswith("# cc "):
            if cur is not None:
                cur["cc"].update(_parse_kv(line[5:]))
        elif line.startswith("v "):
            x, y, z = (float(t) for t in line.split()[1:4]); V.append((x, y, -z))
        elif line.startswith("vt "):
            t = line.split(); VT.append((float(t[1]), 1.0 - float(t[2])))
        elif line.startswith("vn "):
            x, y, z = (float(t) for t in line.split()[1:4]); VN.append((x, y, -z))
        elif line.startswith("usemtl "):
            if cur is None: cur = new("object")
            cur["mat"] = line[7:].strip()
        elif line.startswith("f "):
            if cur is None: cur = new("object")
            corners = []
            for tok in line.split()[1:]:
                parts = tok.split("/")
                def idx(s, n):
                    if not s: return None
                    i = int(s); return i - 1 if i > 0 else n + i
                corners.append((idx(parts[0], len(V)), idx(parts[1], len(VT)) if len(parts) > 1 else None, idx(parts[2], len(VN)) if len(parts) > 2 else None))
            for k in range(1, len(corners) - 1):
                cur["faces"].append((corners[0], corners[k], corners[k + 1], cur["mat"]))
        elif line.startswith("p "):
            if cur is None: cur = new("point")
            for t in line.split()[1:]:
                i = int(t); cur["points"].append(i - 1 if i > 0 else len(V) + i)
    for d in objs:
        cc = d["cc"]; typ = cc.get("type")
        pn = model.parse_name(d["name"])
        room = cc.get("room", ""); ir.ensure_room(room)
        common = dict(room=room, parent=cc.get("parent") or None, prefix=cc.get("prefix") or None, key=cc.get("key") or "")
        if d["points"] and not d["faces"]:
            pos = V[d["points"][0]]
            m = _parse_mat(cc["matrix"]) if "matrix" in cc else model.IDENT
            sc = float(cc.get("scale", 1.0))
            if typ == "light":
                l = IRLight(name=pn.base, pos=pos, matrix=m, scale=sc, **common)
                l.ltype = int(cc.get("ltype", 1)); l.intensity = float(cc.get("intensity", 1))
                if "color" in cc: l.color = tuple(float(x) for x in cc["color"].split(","))
                if "range" in cc: l.range_a, l.range_b = (float(x) for x in cc["range"].split(","))
                l.lightmap = cc.get("lightmap") or None
                if "flare" in cc: l.flare = tuple(float(x) for x in cc["flare"].split(","))
                if "spot" in cc:
                    s = [float(x) for x in cc["spot"].split(",")]; l.spot_dir = tuple(s[:3]); l.spot_a, l.spot_b = s[3], s[4]
                l.flag_b0 = int(cc.get("b0", 1))
                ir.lights.append(l)
            else:
                kind = cc.get("kind") or pn.point_kind or "null"
                p = IRPoint(name=pn.base, pos=pos, matrix=m, scale=sc, kind=kind, **common)
                p.enabled = int(cc.get("enabled", 1))
                p.packages_a = [x for x in cc.get("packages_a", "").split("|") if x]
                p.packages_b = [x for x in cc.get("packages_b", "").split("|") if x]
                if cc.get("raw"): p.raw_data = bytes.fromhex(cc["raw"])
                ir.points.append(p)
            continue
        mesh = IRMesh(); remap = {}
        for (c0, c1, c2, mat) in d["faces"]:
            f = []; uvs = []
            for (vi_, ti_, ni_) in (c0, c1, c2):
                key = (vi_, ni_)
                if key not in remap:
                    remap[key] = len(mesh.verts); mesh.verts.append(V[vi_]); mesh.normals.append(VN[ni_] if ni_ is not None and ni_ < len(VN) else None)
                f.append(remap[key]); uvs.append(VT[ti_] if ti_ is not None and ti_ < len(VT) else (0.0, 0.0))
            mesh.faces.append(tuple(f)); mesh.face_uvs.append(tuple(uvs)); mesh.face_colors.append(None)
            mn = mat or "default"; ir.material(mn); mesh.face_mats.append(mn)
        o = IRObject(name=pn.base, mesh=mesh, collision=pn.collision, surface=pn.surface, portal=pn.portal,
                     portal_room=cc.get("portal_room", ""), backdrop=pn.backdrop, **common)
        o.static = int(cc.get("static", 1)); o.hidden = int(cc.get("hidden", 1)); o.show = int(cc.get("show", 1))
        if pn.nocollision: o.collision = False
        if not o.key: o.key = o.name
        ir.objects.append(o)
    return ir


def convert_textures(ir: IRScene, src_dirs: List[str], gti_out_dir: str, image_dirs: List[str]) -> int:
    """For every material texture without a .gti in gti_out_dir, convert a PNG/TGA/JPG found in image_dirs."""
    from PIL import Image
    from .gti import GtiFile
    os.makedirs(gti_out_dir, exist_ok=True)
    n = 0
    for m in ir.materials.values():
        if not m.texture: continue
        dst = os.path.join(gti_out_dir, m.texture + ".gti")
        if os.path.exists(dst): continue
        for d in image_dirs:
            for ext in (".png", ".tga", ".jpg", ".jpeg", ".bmp"):
                p = os.path.join(d, m.texture + ext)
                if os.path.isfile(p):
                    img = Image.open(p)
                    w, h = img.size
                    # engine wants power-of-two textures
                    pw = 1 << max(0, (w - 1).bit_length()); ph = 1 << max(0, (h - 1).bit_length())
                    if (pw, ph) != (w, h): img = img.resize((pw, ph))
                    GtiFile.from_pil(img).save(dst); n += 1; break
            else:
                continue
            break
    return n
