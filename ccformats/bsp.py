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
def _choose_splitter(tris: List[Tri], rng: random.Random, samples: int) -> Tri:
    if len(tris) <= samples:
        cands = tris
    else:
        cands = rng.sample(tris, samples)
    best = None; best_score = None
    for c in cands:
        n = c.n; d = _dot(n, c.v[0])
        fp = fn = fs = 0
        for t in tris:
            k, _ = _classify(t, n, d)
            if k == "P": fp += 1
            elif k == "N": fn += 1
            elif k == "S": fs += 1
        score = fs * 6 + abs(fp - fn)
        if best_score is None or score < best_score:
            best, best_score = c, score
    return best


def build_tree(tris: Sequence[Tri], rng: Optional[random.Random] = None, samples: int = 12) -> Optional[ccf.BspNode]:
    """Build an engine-compatible BSP tree from world-space triangles (iterative, explicit stack)."""
    rng = rng or random.Random(1234)
    tris = [t for t in tris if not t.degenerate()]
    if not tris:
        return None
    root = ccf.BspNode()
    stack = [(root, tris)]
    while stack:
        node, lst = stack.pop()
        sp = _choose_splitter(lst, rng, samples)
        n = sp.n; d = _dot(n, sp.v[0])
        node.normal = n; node.point = sp.v[0]
        pos: List[Tri] = []; neg: List[Tri] = []
        for t in lst:
            k, ds = _classify(t, n, d)
            if k == "C":
                node.children.append(make_bsp_poly(t))
            elif k == "P":
                pos.append(t)
            elif k == "N":
                neg.append(t)
            else:
                a, b = _split(t, n, d, ds)
                pos.extend(a); neg.extend(b)
        if neg:
            node.neg = ccf.BspNode(); stack.append((node.neg, neg))
        if pos:
            node.pos = ccf.BspNode(); stack.append((node.pos, pos))
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
