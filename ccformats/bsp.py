"""
Static / portal BSP builder for CcFf scenes.

cc.dll never builds the collision BSP at load time (CcRoom::CreateStaticBsp is not referenced by
cc.dll itself nor imported by Pickup.exe), so a level must ship its BSP trees inside the .ccf.
This module rebuilds them from the scene geometry in the same layout the engine writes:

    room.static_bsp (0x1200) -> one f0c0 tree holding every polygon of every *collidable*
                                object in the room (object.flag_b1 == 1), incl. portal objects
    room.portal_bsp (0x1201) -> one f0c0 tree holding the polygons of the room's portal objects

Node   : plane (unit normal, point), coplanar polygons, child `neg` (dot < 0), child `pos` (dot > 0)
Polygon: [e1 x e2, unit normal, v0, e1 = v1 - v0, e2 = v2 - v1], polygon index, object ref
Polygons that straddle the plane are split (the engine's own builder does that too: the shipped
files contain more BSP polygons than mesh polygons).
"""
from __future__ import annotations
import math, random, sys
from typing import List, Optional, Sequence, Tuple
from . import ccf

Vec3 = Tuple[float, float, float]
EPS = 1e-3


def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def _scale(a, s): return (a[0] * s, a[1] * s, a[2] * s)
def _norm(a):
    l = math.sqrt(_dot(a, a))
    return (a[0] / l, a[1] / l, a[2] / l) if l > 1e-12 else (0.0, 0.0, 0.0)


class Tri:
    __slots__ = ("v", "n", "object_ref", "poly_index")

    def __init__(self, v0, v1, v2, object_ref: int, poly_index: int):
        self.v = (v0, v1, v2)
        self.n = _norm(_cross(_sub(v1, v0), _sub(v2, v1)))
        self.object_ref = object_ref
        self.poly_index = poly_index

    def degenerate(self) -> bool:
        return self.n == (0.0, 0.0, 0.0)


def transform_point(v: Vec3, matrix, pos: Vec3, scale: float = 1.0) -> Vec3:
    """world = v * scale  @ R (rows = basis vectors) + pos  - the convention proven on shipped data."""
    x, y, z = v[0] * scale, v[1] * scale, v[2] * scale
    r0, r1, r2 = matrix
    return (x * r0[0] + y * r1[0] + z * r2[0] + pos[0],
            x * r0[1] + y * r1[1] + z * r2[1] + pos[1],
            x * r0[2] + y * r1[2] + z * r2[2] + pos[2])


def object_world_triangles(scene: ccf.Scene, obj: ccf.Object) -> List[Tri]:
    mesh = scene.mesh_by_ref(obj.mesh_ref)
    if mesh is None:
        return []
    verts = [transform_point(v.pos, obj.matrix, obj.pos, obj.scale) for v in mesh.vertices]
    out = []
    for i, p in enumerate(mesh.polygons):
        a, b, c = p.indices
        if max(a, b, c) >= len(verts):
            continue
        t = Tri(verts[a], verts[b], verts[c], obj.ref, i)
        if not t.degenerate():
            out.append(t)
    return out


# ----------------------------------------------------------------------------------------------
# plane classification / splitting
# ----------------------------------------------------------------------------------------------
def _classify(tri: Tri, n: Vec3, d: float):
    ds = [_dot(n, v) - d for v in tri.v]
    front = any(x > EPS for x in ds); back = any(x < -EPS for x in ds)
    if front and back:
        return "S", ds
    if front:
        return "P", ds
    if back:
        return "N", ds
    return "C", ds


def _split(tri: Tri, n: Vec3, d: float, ds) -> Tuple[List[Tri], List[Tri]]:
    """Clip a triangle by the plane; returns (positive side tris, negative side tris)."""
    pos_pts: List[Vec3] = []; neg_pts: List[Vec3] = []
    for i in range(3):
        a = tri.v[i]; b = tri.v[(i + 1) % 3]; da = ds[i]; db = ds[(i + 1) % 3]
        if da >= -EPS:
            pos_pts.append(a)
        if da <= EPS:
            neg_pts.append(a)
        if (da > EPS and db < -EPS) or (da < -EPS and db > EPS):
            t = da / (da - db)
            p = _add(a, _scale(_sub(b, a), t))
            pos_pts.append(p); neg_pts.append(p)
    def fan(pts):
        out = []
        for i in range(1, len(pts) - 1):
            t = Tri(pts[0], pts[i], pts[i + 1], tri.object_ref, tri.poly_index)
            if not t.degenerate():
                out.append(t)
        return out
    return fan(pos_pts), fan(neg_pts)


# ----------------------------------------------------------------------------------------------
# tree construction
# ----------------------------------------------------------------------------------------------
def build_tree(tris: Sequence[Tri], rng: Optional[random.Random] = None, samples: int = 16) -> Optional[ccf.BspNode]:
    """Build an engine-compatible BSP tree from world-space triangles.

    Vectorised with numpy: every node evaluates `samples` candidate splitter planes against all of its
    triangles at once (score = 6 * splits + |front - back|) and only the few straddling triangles are
    clipped in Python.  Uses an explicit stack, so depth is not limited by the recursion limit."""
    import numpy as np
    rng = rng or random.Random(1234)
    tris = [t for t in tris if not t.degenerate()]
    if not tris:
        return None
    V = np.array([t.v for t in tris], dtype=np.float64)           # (N,3,3)
    NR = np.array([t.n for t in tris], dtype=np.float64)          # (N,3)
    OBJ = np.array([t.object_ref for t in tris], dtype=np.int64)
    PI = np.array([t.poly_index for t in tris], dtype=np.int64)
    root = ccf.BspNode()
    stack = [(root, V, NR, OBJ, PI)]
    while stack:
        node, V, NR, OBJ, PI = stack.pop()
        n_tri = len(V)
        cand = list(range(n_tri)) if n_tri <= samples else rng.sample(range(n_tri), samples)
        best = None; best_score = None
        for c in cand:
            n = NR[c]; d = float(n @ V[c, 0])
            ds = V @ n - d
            fr = (ds > EPS).any(1); bk = (ds < -EPS).any(1)
            sp = (fr & bk).sum(); pc = (fr & ~bk).sum(); nc = (bk & ~fr).sum()
            score = sp * 6 + abs(int(pc) - int(nc))
            if best_score is None or score < best_score:
                best, best_score = c, score
                if score == 0: break
        n = NR[best]; point = V[best, 0]; d = float(n @ point)
        node.normal = (float(n[0]), float(n[1]), float(n[2])); node.point = (float(point[0]), float(point[1]), float(point[2]))
        ds = V @ n - d
        fr = (ds > EPS).any(1); bk = (ds < -EPS).any(1)
        co = ~fr & ~bk; pos = fr & ~bk; neg = bk & ~fr; spl = fr & bk
        for i in np.nonzero(co)[0]:
            t = Tri(tuple(V[i, 0]), tuple(V[i, 1]), tuple(V[i, 2]), int(OBJ[i]), int(PI[i]))
            node.children.append(make_bsp_poly(t))
        pV, pN, pO, pP = [V[pos]], [NR[pos]], [OBJ[pos]], [PI[pos]]
        nV, nN, nO, nP = [V[neg]], [NR[neg]], [OBJ[neg]], [PI[neg]]
        extra_p = []; extra_n = []
        for i in np.nonzero(spl)[0]:
            t = Tri(tuple(V[i, 0]), tuple(V[i, 1]), tuple(V[i, 2]), int(OBJ[i]), int(PI[i]))
            a, b = _split(t, tuple(n), d, [float(x) for x in ds[i]])
            extra_p += a; extra_n += b
        def pack(lst, vs, ns, os_, ps):
            if lst:
                vs.append(np.array([t.v for t in lst], dtype=np.float64)); ns.append(np.array([t.n for t in lst], dtype=np.float64))
                os_.append(np.array([t.object_ref for t in lst], dtype=np.int64)); ps.append(np.array([t.poly_index for t in lst], dtype=np.int64))
            return (np.concatenate(vs), np.concatenate(ns), np.concatenate(os_), np.concatenate(ps))
        pv = pack(extra_p, pV, pN, pO, pP); nv = pack(extra_n, nV, nN, nO, nP)
        if len(nv[0]):
            node.neg = ccf.BspNode(); stack.append((node.neg,) + nv)
        if len(pv[0]):
            node.pos = ccf.BspNode(); stack.append((node.pos,) + pv)
    return root


def make_bsp_poly(t: Tri) -> ccf.BspPoly:
    p = ccf.BspPoly()
    v0, v1, v2 = t.v
    e1 = _sub(v1, v0); e2 = _sub(v2, v1)
    p.vecs = [_cross(e1, e2), t.n, v0, e1, e2]
    p.poly_index = t.poly_index; p.object_ref = t.object_ref
    return p


# ----------------------------------------------------------------------------------------------
# scene level helpers
# ----------------------------------------------------------------------------------------------
def room_objects(scene: ccf.Scene, room: ccf.Room) -> List[ccf.Object]:
    return [o for o in scene.objects if o.room_ref == room.ref]


def collidable_triangles(scene: ccf.Scene, room: ccf.Room, include_portals: bool = True) -> List[Tri]:
    out = []
    for o in room_objects(scene, room):
        if not o.flag_b1:
            continue
        if not include_portals and o.portal_type == 0:
            continue
        out.extend(object_world_triangles(scene, o))
    return out


def portal_triangles(scene: ccf.Scene, room: ccf.Room) -> List[Tri]:
    out = []
    for o in room_objects(scene, room):
        if o.portal_type == 0:
            out.extend(object_world_triangles(scene, o))
    return out


def rebuild_room(scene: ccf.Scene, room: ccf.Room, static: bool = True, portal: bool = True, samples: int = 12) -> dict:
    """Rebuild the BSP lists of one room in place. Returns statistics."""
    stats = {}
    if static:
        tris = collidable_triangles(scene, room)
        root = build_tree(tris, samples=samples)
        lst = ccf.StaticBsp()
        if root is not None:
            lst.children.append(root)
        room.replace(ccf.StaticBsp, lst)
        stats["static_input"] = len(tris); stats["static_nodes"] = root.count() if root else 0
        stats["static_polys"] = len(root.all_polys()) if root else 0
    if portal:
        tris = portal_triangles(scene, room)
        root = build_tree(tris, samples=samples)
        lst = ccf.PortalBsp()
        if root is not None:
            lst.children.append(root)
        room.replace(ccf.PortalBsp, lst)
        stats["portal_input"] = len(tris); stats["portal_nodes"] = root.count() if root else 0
    # remove stray bare f0c0 nodes (old-style) - the list form is what the writer emits
    room.children = [c for c in room.children if not isinstance(c, ccf.BspNode)]
    return stats


def rebuild_all(scene: ccf.Scene, samples: int = 12, progress=None) -> dict:
    """Rebuild static + portal BSP trees of every room.  Rooms without a name and without objects are skipped."""
    total = {}
    rooms = scene.rooms
    for i, room in enumerate(rooms):
        if progress:
            progress(i, len(rooms), room.name)
        if not room_objects(scene, room):
            # keep (empty) lists so the engine finds something for named rooms
            if room.name:
                room.replace(ccf.StaticBsp, ccf.StaticBsp()); room.replace(ccf.PortalBsp, ccf.PortalBsp())
            continue
        total[room.name] = rebuild_room(scene, room, samples=samples)
    return total


def validate_tree(root: ccf.BspNode) -> dict:
    """Sanity statistics: every polygon in a subtree must lie on the correct side of each ancestor plane."""
    bad = 0; total = 0; depth = root.depth()
    def walk(n, planes):
        nonlocal bad, total
        for p in n.polys:
            total += 1
            v0 = p.vecs[2]; v1 = _add(v0, p.vecs[3]); v2 = _add(v1, p.vecs[4])
            for (pn, pp, sign) in planes:
                if any(sign * (_dot(pn, v) - _dot(pn, pp)) < -1e-2 for v in (v0, v1, v2)):
                    bad += 1; break
        if n.neg: walk(n.neg, planes + [(n.normal, n.point, -1)])
        if n.pos: walk(n.pos, planes + [(n.normal, n.point, 1)])
    walk(root, [])
    return {"polys": total, "misplaced": bad, "depth": depth, "nodes": root.count()}


if __name__ == "__main__":
    import time
    path = sys.argv[1]
    sc = ccf.Scene.load(path)
    t = time.time()
    st = rebuild_all(sc, progress=lambda i, n, name: print(f"  room {i + 1}/{n} {name}"))
    print("rebuilt in %.1fs" % (time.time() - t))
    for name, s in list(st.items())[:10]:
        print(name, s)
    if len(sys.argv) > 2:
        sc.save(sys.argv[2]); print("saved", sys.argv[2])
