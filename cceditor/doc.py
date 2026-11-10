"""Editor document: the IR scene, file bookkeeping, selection and undo/redo."""
from __future__ import annotations
import copy, math, os
from typing import Callable, Dict, List, Optional

import numpy as np

from ccformats import ccf, model, bsp, tcfg, ccm
from ccformats.model import IRScene, IRObject, IRPoint, IRLight, IRNode, IRMesh, IRMaterial


# ----------------------------------------------------------------------------------------------
# rotation helpers (engine matrices: rows are the basis vectors, world = v @ M)
# ----------------------------------------------------------------------------------------------
def euler_to_matrix(rx: float, ry: float, rz: float):
    """Degrees, applied X then Y then Z to column vectors; returned as the engine's row-basis matrix."""
    rx, ry, rz = (math.radians(a) for a in (rx, ry, rz))
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]]); Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]); Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    R = Rz @ Ry @ Rx
    return tuple(tuple(float(x) for x in row) for row in R.T)


def matrix_to_euler(m):
    R = np.array(m).T
    sy = -R[2, 0]
    ry = math.asin(max(-1.0, min(1.0, sy)))
    if abs(sy) < 0.99999:
        rx = math.atan2(R[2, 1], R[2, 2]); rz = math.atan2(R[1, 0], R[0, 0])
    else:
        rx = math.atan2(-R[1, 2], R[1, 1]); rz = 0.0
    return tuple(math.degrees(a) for a in (rx, ry, rz))


def yaw_matrix(deg: float):
    return euler_to_matrix(0, deg, 0)


def mat_mul_rows(a, b):
    """Engine matrix product: first rotate by a then by b (row-vector convention)."""
    return tuple(tuple(float(x) for x in row) for row in (np.array(a) @ np.array(b)))


# ----------------------------------------------------------------------------------------------
class Document:
    def __init__(self):
        self.ir = IRScene()
        self.ir.ensure_room("")
        self.path: Optional[str] = None
        self.game_dir: Optional[str] = None
        self.texture_dirs: List[str] = []
        self.town_cfg: Optional[tcfg.TownConfig] = None
        self.town_cfg_path: Optional[str] = None
        self.selection: List[IRNode] = []
        self.undo_stack: List[tuple] = []
        self.redo_stack: List[tuple] = []
        self.dirty = False
        self.listeners: List[Callable[[str], None]] = []
        self.rebuild_bsp_on_save = True

    # ---- notifications
    def notify(self, what: str):
        for l in list(self.listeners):
            l(what)

    # ---- nodes
    def all_nodes(self) -> List[IRNode]:
        return list(self.ir.objects) + list(self.ir.points) + list(self.ir.lights)

    def set_selection(self, nodes: List[IRNode]):
        self.selection = list(nodes)
        self.notify("selection")

    # ---- undo
    def push(self, label: str, do: Callable, undo: Callable, run: bool = True):
        if run: do()
        self.undo_stack.append((label, do, undo)); self.redo_stack.clear(); self.dirty = True
        self.notify("scene")

    def undo(self):
        if not self.undo_stack: return
        label, do, undo = self.undo_stack.pop(); undo(); self.redo_stack.append((label, do, undo)); self.dirty = True
        self.notify("scene")

    def redo(self):
        if not self.redo_stack: return
        label, do, undo = self.redo_stack.pop(); do(); self.undo_stack.append((label, do, undo)); self.dirty = True
        self.notify("scene")

    # ---- editing helpers
    def set_attr(self, nodes, attr: str, value, label: Optional[str] = None):
        nodes = list(nodes); old = [getattr(n, attr) for n in nodes]
        def do():
            for n in nodes: setattr(n, attr, value)
        def undo():
            for n, o in zip(nodes, old): setattr(n, attr, o)
        self.push(label or f"Set {attr}", do, undo)

    def set_transform(self, nodes, newstates, oldstates, label="Transform"):
        """states: list of (pos, matrix, scale)"""
        nodes = list(nodes)
        def apply(states):
            for n, (p, m, s) in zip(nodes, states):
                n.pos, n.matrix, n.scale = p, m, s
        self.push(label, lambda: apply(newstates), lambda: apply(oldstates))

    def unique_key(self, base: str) -> str:
        keys = {n.key for n in self.all_nodes()}
        if base not in keys: return base
        i = 1
        while f"{base}~{i}" in keys: i += 1
        return f"{base}~{i}"

    def add_node(self, node: IRNode, label="Add"):
        node.key = self.unique_key(node.key or node.name)
        def do():
            if isinstance(node, IRObject): self.ir.objects.append(node)
            elif isinstance(node, IRPoint): self.ir.points.append(node)
            else: self.ir.lights.append(node)
            self.ir.ensure_room(node.room)
        def undo():
            for lst in (self.ir.objects, self.ir.points, self.ir.lights):
                if node in lst: lst.remove(node)
        self.push(label, do, undo)
        self.set_selection([node])

    def delete_nodes(self, nodes):
        nodes = list(nodes)
        if not nodes: return
        removed = {id(n): [] for n in nodes}
        positions = []
        for n in nodes:
            for lst in (self.ir.objects, self.ir.points, self.ir.lights):
                if n in lst: positions.append((lst, lst.index(n), n))
        def do():
            for lst, _, n in positions:
                if n in lst: lst.remove(n)
            self.selection = [s for s in self.selection if s not in nodes]
        def undo():
            for lst, i, n in sorted(positions, key=lambda t: t[1]):
                lst.insert(min(i, len(lst)), n)
        self.push("Delete", do, undo)
        self.notify("selection")

    def duplicate(self, nodes, offset=(2.0, 0.0, 2.0)):
        new = []
        for n in nodes:
            c = copy.deepcopy(n)
            c.name = n.name; c.key = self.unique_key(n.key + "_copy")
            c.pos = (n.pos[0] + offset[0], n.pos[1] + offset[1], n.pos[2] + offset[2])
            c.parent = None
            new.append(c)
        def do():
            for c in new:
                (self.ir.objects if isinstance(c, IRObject) else self.ir.points if isinstance(c, IRPoint) else self.ir.lights).append(c)
        def undo():
            for c in new:
                for lst in (self.ir.objects, self.ir.points, self.ir.lights):
                    if c in lst: lst.remove(c)
        self.push("Duplicate", do, undo)
        self.set_selection(new)

    # ---- files
    def detect_paths(self, ccf_path: str):
        d = os.path.dirname(os.path.abspath(ccf_path))
        self.texture_dirs = []
        for cand in (os.path.join(d, "Textures"), os.path.join(d, "textures")):
            if os.path.isdir(cand): self.texture_dirs.append(cand)
        # game root: walk up until General/Textures exists
        cur = d; self.game_dir = None
        for _ in range(5):
            if os.path.isdir(os.path.join(cur, "General")) and os.path.isfile(os.path.join(cur, "Pickup.exe")):
                self.game_dir = cur; break
            cur = os.path.dirname(cur)
        if self.game_dir:
            for sub in ("General/Textures", "Towns/%s/Textures" % os.path.basename(d), "Posters"):
                p = os.path.join(self.game_dir, *sub.split("/"))
                if os.path.isdir(p) and p not in self.texture_dirs: self.texture_dirs.append(p)
        # tcfg next to the ccf
        base = os.path.splitext(os.path.abspath(ccf_path))[0]
        self.town_cfg = None; self.town_cfg_path = None
        if os.path.isfile(base + ".tcfg"):
            self.town_cfg_path = base + ".tcfg"; self.town_cfg = tcfg.TownConfig.load(self.town_cfg_path)

    def load(self, path: str):
        sc = ccf.Scene.load(path)
        self.ir = model.scene_to_ir(sc)
        self.path = path; self.detect_paths(path)
        mt = os.path.splitext(path)[0] + ".ccm"
        self.selection = []; self.undo_stack.clear(); self.redo_stack.clear(); self.dirty = False
        self.notify("loaded")

    def load_ir(self, ir: IRScene, path: Optional[str] = None):
        self.ir = ir; self.path = path; self.selection = []; self.undo_stack.clear(); self.redo_stack.clear(); self.dirty = True
        if path: self.detect_paths(path)
        self.notify("loaded")

    def build_scene(self, progress=None) -> ccf.Scene:
        return model.ir_to_scene(self.ir, build_bsp=self.rebuild_bsp_on_save, progress=progress)

    def save(self, path: Optional[str] = None, progress=None):
        path = path or self.path
        sc = self.build_scene(progress)
        sc.save(path)
        self.path = path
        base = os.path.splitext(path)[0]
        ccm.MaterialTable.from_scene(sc).save(base + ".ccm")
        if self.town_cfg:
            self.town_cfg_path = base + ".tcfg"          # always next to the saved .ccf (never overwrite the source)
            self.town_cfg.save(self.town_cfg_path)
        self.dirty = False
        self.notify("saved")

    # ---- queries
    def bounds(self, nodes=None):
        nodes = nodes or self.all_nodes()
        pts = [n.pos for n in nodes]
        if not pts: return (np.zeros(3), np.ones(3))
        a = np.array(pts)
        return a.min(0), a.max(0)

    def rooms(self) -> List[str]:
        return list(self.ir.rooms)
