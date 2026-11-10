"""OpenGL viewport for the Pickup Express level editor (wx.glcanvas + PyOpenGL, fixed function pipeline).

World space used for rendering is the engine's (x, y, z) with z negated so that the left-handed
engine space is displayed with OpenGL's right-handed conventions ("GL space").
"""
from __future__ import annotations
import math, os
from typing import Dict, List, Optional, Tuple

import numpy as np
import wx
from wx import glcanvas
from OpenGL import GL as gl

from ccformats import gti, model
from ccformats.model import IRObject, IRPoint, IRLight
from .doc import Document, yaw_matrix, mat_mul_rows

KIND_COLORS = {"spawn": (0.2, 1.0, 0.2), "pickup": (1.0, 0.9, 0.1), "drop": (1.0, 0.3, 1.0), "fuel": (1.0, 0.55, 0.1),
               "ai": (0.2, 0.9, 1.0), "null": (0.9, 0.9, 0.9)}
SURFACE_COLORS = {15: (0.2, 0.4, 1.0), 10: (0.8, 0.55, 0.3), 2: (0.2, 0.7, 0.2)}
TOOL_SELECT, TOOL_MOVE, TOOL_ROTATE, TOOL_SCALE = range(4)


def to_gl(v):
    return np.array([v[0], v[1], -v[2]], dtype=np.float64)


def from_gl(v):
    return (float(v[0]), float(v[1]), -float(v[2]))


# ----------------------------------------------------------------------------------------------
class TextureManager:
    def __init__(self):
        self.dirs: List[str] = []
        self.index: Dict[str, str] = {}
        self.gl_tex: Dict[str, Tuple[int, bool]] = {}
        self.cpu: Dict[str, np.ndarray] = {}

    def set_dirs(self, dirs: List[str]):
        self.dirs = list(dirs); self.index = {}
        for d in dirs:
            if not os.path.isdir(d): continue
            for fn in os.listdir(d):
                if fn.lower().endswith(".gti"):
                    self.index.setdefault(fn[:-4].lower(), os.path.join(d, fn))
        self.clear_gl()

    def names(self) -> List[str]:
        return sorted(os.path.splitext(os.path.basename(p))[0] for p in self.index.values())

    def clear_gl(self):
        for tid, _ in self.gl_tex.values():
            try: gl.glDeleteTextures([tid])
            except Exception: pass
        self.gl_tex = {}

    def load_cpu(self, name: str) -> Optional[np.ndarray]:
        key = (name or "").lower()
        if key in self.cpu: return self.cpu[key]
        p = self.index.get(key); arr = None
        if p:
            try: arr = gti.GtiFile.load(p).to_rgba_np()
            except Exception: arr = None
        self.cpu[key] = arr
        return arr

    def get(self, name: Optional[str]) -> Optional[Tuple[int, bool]]:
        if not name: return None
        key = name.lower()
        if key in self.gl_tex: return self.gl_tex[key]
        arr = self.load_cpu(name)
        if arr is None:
            self.gl_tex[key] = None; return None
        h, w = arr.shape[:2]
        tid = gl.glGenTextures(1)
        gl.glBindTexture(gl.GL_TEXTURE_2D, tid)
        gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_REPEAT)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_REPEAT)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA, w, h, 0, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, np.ascontiguousarray(arr).tobytes())
        try:
            gl.glGenerateMipmap(gl.GL_TEXTURE_2D)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR_MIPMAP_LINEAR)
        except Exception:
            pass
        has_alpha = bool((arr[:, :, 3] != 255).any())
        self.gl_tex[key] = (tid, has_alpha)
        return self.gl_tex[key]


# ----------------------------------------------------------------------------------------------
class SceneCache:
    """World-space triangle data of every IRObject, rebuilt on demand."""

    def __init__(self):
        self.mesh_np: Dict[int, tuple] = {}
        self.obj_tris: Dict[int, np.ndarray] = {}        # id(obj) -> (T,3,3) GL space
        self.obj_key: Dict[int, tuple] = {}
        self.batches: Dict[str, tuple] = {}              # material -> (pos, uv, col, classcol)
        self.aabb_min = np.zeros((0, 3)); self.aabb_max = np.zeros((0, 3)); self.aabb_obj: List[IRObject] = []
        self.wire_all: Optional[np.ndarray] = None

    def clear(self):
        self.__init__()

    def _mesh_arrays(self, o: IRObject):
        m = o.mesh; k = id(m)
        hit = self.mesh_np.get(k)
        if hit and hit[0] is m and hit[1] == len(m.faces):
            return hit[2:]
        verts = np.array(m.verts, dtype=np.float64).reshape(-1, 3)
        faces = np.array(m.faces, dtype=np.int64).reshape(-1, 3)
        F = len(faces)
        uv = np.zeros((F, 3, 2), np.float32); col = np.ones((F, 3, 3), np.float32)
        for i in range(F):
            if i < len(m.face_uvs) and m.face_uvs[i]: uv[i] = m.face_uvs[i]
            if i < len(m.face_colors) and m.face_colors[i]: col[i] = m.face_colors[i]
        names = np.array(m.face_mats if m.face_mats else ["default"] * F, dtype=object)
        self.mesh_np[k] = (m, len(m.faces), verts, faces, uv, col, names)
        return verts, faces, uv, col, names

    def rebuild(self, doc: Document, hidden_rooms: set, mode: int, show_portals: bool = False):
        """mode 0 textured / 1 collision map / 2 wire"""
        per_mat: Dict[str, list] = {}
        mins, maxs, objs = [], [], []
        self.obj_tris = {}
        for o in doc.ir.objects:
            if o.room in hidden_rooms or not o.mesh.faces: continue
            if o.portal and not show_portals: continue       # portals are invisible helpers in the game
            verts, faces, uv, col, names = self._mesh_arrays(o)
            if len(verts) == 0: continue
            M = np.array(o.matrix, dtype=np.float64)
            w = (verts * o.scale) @ M + np.array(o.pos)
            w[:, 2] *= -1.0
            tris = w[faces]                                   # (F,3,3)
            self.obj_tris[id(o)] = tris
            mins.append(tris.reshape(-1, 3).min(0)); maxs.append(tris.reshape(-1, 3).max(0)); objs.append(o)
            if o.portal: cc = (0.9, 0.2, 0.9)
            elif o.surface in SURFACE_COLORS: cc = SURFACE_COLORS[o.surface]
            elif o.collision: cc = (0.45, 0.8, 0.45)
            else: cc = (0.6, 0.6, 0.65)
            for mn in dict.fromkeys(names.tolist()):
                sel = np.nonzero(names == mn)[0]
                lst = per_mat.setdefault(mn, [[], [], [], []])
                lst[0].append(tris[sel].reshape(-1, 3)); lst[1].append(uv[sel].reshape(-1, 2)); lst[2].append(col[sel].reshape(-1, 3))
                lst[3].append(np.tile(np.array(cc, np.float32), (len(sel) * 3, 1)))
        self.batches = {k: tuple(np.ascontiguousarray(np.concatenate(v), dtype=np.float32) for v in lst) for k, lst in per_mat.items()}
        self.aabb_min = np.array(mins).reshape(-1, 3); self.aabb_max = np.array(maxs).reshape(-1, 3); self.aabb_obj = objs

    # ---- picking
    def pick_object(self, o_, d_) -> Tuple[Optional[IRObject], float]:
        if not len(self.aabb_obj): return None, math.inf
        inv = 1.0 / np.where(np.abs(d_) < 1e-12, 1e-12, d_)
        t1 = (self.aabb_min - o_) * inv; t2 = (self.aabb_max - o_) * inv
        tmin = np.minimum(t1, t2).max(1); tmax = np.maximum(t1, t2).min(1)
        cand = np.nonzero((tmax >= np.maximum(tmin, 0)))[0]
        cand = cand[np.argsort(tmin[cand])]
        best_t = math.inf; best = None
        for i in cand:
            if tmin[i] > best_t: break
            t = ray_tris(o_, d_, self.obj_tris[id(self.aabb_obj[i])])
            if t is not None and t < best_t: best_t = t; best = self.aabb_obj[i]
        return best, best_t


def ray_tris(o, d, tris) -> Optional[float]:
    v0 = tris[:, 0]; e1 = tris[:, 1] - v0; e2 = tris[:, 2] - v0
    p = np.cross(d, e2); det = (e1 * p).sum(1)
    ok = np.abs(det) > 1e-12
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    tv = o - v0
    u = (tv * p).sum(1) * inv
    q = np.cross(tv, e1)
    v = (q * d).sum(1) * inv
    t = (q * e2).sum(1) * inv
    hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 0)
    return float(t[hit].min()) if hit.any() else None


# ----------------------------------------------------------------------------------------------
class Viewport(glcanvas.GLCanvas):
    def __init__(self, parent, doc: Document, status=None):
        attribs = [glcanvas.WX_GL_RGBA, glcanvas.WX_GL_DOUBLEBUFFER, glcanvas.WX_GL_DEPTH_SIZE, 24, 0]
        super().__init__(parent, attribList=attribs, style=wx.WANTS_CHARS)
        self.ctx = glcanvas.GLContext(self)
        self.doc = doc; self.status = status
        self.tex = TextureManager(); self.cache = SceneCache()
        self.target = np.zeros(3); self.yaw = 35.0; self.pitch = 35.0; self.dist = 120.0
        self.mode = 0; self.tool = TOOL_MOVE
        self.show = {"points": True, "lights": True, "grid": True, "ai": True, "selection": True, "portals": False}
        self.hidden_rooms: set = set()
        self.snap = 0.0
        self.dirty_cache = True; self.gl_ready = False
        self.drag = None; self.last = (0, 0); self.size = wx.Size(1, 1)
        self.on_pick_info = None
        doc.listeners.append(self.on_doc)
        for ev, fn in ((wx.EVT_PAINT, self.on_paint), (wx.EVT_SIZE, self.on_size), (wx.EVT_LEFT_DOWN, self.on_ldown), (wx.EVT_LEFT_UP, self.on_lup),
                       (wx.EVT_MOTION, self.on_motion), (wx.EVT_MOUSEWHEEL, self.on_wheel), (wx.EVT_RIGHT_DOWN, self.on_rdown), (wx.EVT_RIGHT_UP, self.on_rup),
                       (wx.EVT_MIDDLE_DOWN, self.on_rdown), (wx.EVT_MIDDLE_UP, self.on_rup), (wx.EVT_KEY_DOWN, self.on_key), (wx.EVT_ERASE_BACKGROUND, lambda e: None)):
            self.Bind(ev, fn)

    # ---------------- document events
    def on_doc(self, what):
        if what in ("scene", "loaded", "view"):
            self.dirty_cache = True
        if what == "loaded":
            self.cache.clear(); self.hidden_rooms = set()
            self.tex.set_dirs(self.doc.texture_dirs) if self.gl_ready else setattr(self, "_pending_tex", True)
            self.frame_all()
        self.Refresh(False)

    # ---------------- camera
    def eye(self) -> np.ndarray:
        y, p = math.radians(self.yaw), math.radians(self.pitch)
        return self.target + self.dist * np.array([math.cos(p) * math.sin(y), math.sin(p), math.cos(p) * math.cos(y)])

    def view_matrix(self) -> np.ndarray:
        eye = self.eye(); f = self.target - eye; f /= np.linalg.norm(f)
        up = np.array([0, 1.0, 0]); s = np.cross(f, up); s /= np.linalg.norm(s) + 1e-12; u = np.cross(s, f)
        M = np.eye(4); M[0, :3] = s; M[1, :3] = u; M[2, :3] = -f
        T = np.eye(4); T[:3, 3] = -eye
        return M @ T

    def proj_matrix(self) -> np.ndarray:
        w, h = max(self.size.width, 1), max(self.size.height, 1)
        fov = math.radians(50.0); n = max(0.05, self.dist * 0.01); f = max(3000.0, self.dist * 20)
        t = 1.0 / math.tan(fov / 2)
        P = np.zeros((4, 4)); P[0, 0] = t / (w / h); P[1, 1] = t; P[2, 2] = (f + n) / (n - f); P[2, 3] = 2 * f * n / (n - f); P[3, 2] = -1
        return P

    def ray(self, x, y):
        w, h = self.size.width, self.size.height
        nx = 2.0 * x / w - 1.0; ny = 1.0 - 2.0 * y / h
        inv = np.linalg.inv(self.proj_matrix() @ self.view_matrix())
        a = inv @ np.array([nx, ny, -1, 1.0]); b = inv @ np.array([nx, ny, 1, 1.0])
        a = a[:3] / a[3]; b = b[:3] / b[3]
        d = b - a; d /= np.linalg.norm(d)
        return self.eye(), d

    def project(self, p):
        c = self.proj_matrix() @ self.view_matrix() @ np.array([p[0], p[1], p[2], 1.0])
        if c[3] <= 1e-9: return None
        return ((c[0] / c[3] * 0.5 + 0.5) * self.size.width, (0.5 - c[1] / c[3] * 0.5) * self.size.height)

    def frame_all(self):
        pts = [o.pos for o in self.doc.ir.objects if o.mesh.verts]
        if not pts: pts = [n.pos for n in self.doc.ir.points if n.kind != "null"]
        if not pts: self.target = np.zeros(3); self.dist = 60; return
        a = np.array(pts); lo, hi = np.percentile(a, 2, axis=0), np.percentile(a, 98, axis=0)
        c = (lo + hi) / 2; self.target = to_gl(c); self.dist = max(30.0, float(np.linalg.norm(hi - lo)) * 0.9)

    def frame_selection(self):
        sel = self.doc.selection
        if not sel: return self.frame_all()
        a = np.array([n.pos for n in sel]); c = a.mean(0); self.target = to_gl(c)
        ext = 6.0
        for n in sel:
            if isinstance(n, IRObject) and id(n) in self.cache.obj_tris:
                t = self.cache.obj_tris[id(n)].reshape(-1, 3); ext = max(ext, float(np.linalg.norm(t.max(0) - t.min(0))))
        self.dist = ext * 1.6
        self.Refresh(False)

    # ---------------- GL setup
    def init_gl(self):
        gl.glClearColor(0.16, 0.18, 0.22, 1)
        gl.glEnable(gl.GL_DEPTH_TEST)
        gl.glDepthFunc(gl.GL_LEQUAL)
        gl.glEnable(gl.GL_BLEND); gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        self.gl_ready = True
        if getattr(self, "_pending_tex", False) or not self.tex.index:
            self.tex.set_dirs(self.doc.texture_dirs)

    def on_size(self, e):
        self.size = self.GetClientSize()
        if self.gl_ready and self.IsShownOnScreen():
            self.SetCurrent(self.ctx)
        self.Refresh(False)

    def on_paint(self, e):
        wx.PaintDC(self)
        if not self.IsShownOnScreen(): return
        self.SetCurrent(self.ctx)
        if not self.gl_ready: self.init_gl()
        self.size = self.GetClientSize()
        self.render()
        self.SwapBuffers()

    # ---------------- rendering
    def render(self):
        w, h = max(self.size.width, 1), max(self.size.height, 1)
        gl.glViewport(0, 0, w, h)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
        if self.dirty_cache:
            self.cache.rebuild(self.doc, self.hidden_rooms, self.mode, self.show['portals']); self.dirty_cache = False
        gl.glMatrixMode(gl.GL_PROJECTION); gl.glLoadMatrixd(self.proj_matrix().T)
        gl.glMatrixMode(gl.GL_MODELVIEW); gl.glLoadMatrixd(self.view_matrix().T)
        if self.show["grid"]: self.draw_grid()
        self.draw_geometry()
        self.draw_points_lights()
        if self.show["selection"]: self.draw_selection()
        self.draw_gizmo()
        gl.glFlush()

    def draw_grid(self):
        gl.glDisable(gl.GL_TEXTURE_2D); gl.glDisable(gl.GL_DEPTH_TEST)
        c = self.target; step = 10.0 if self.dist < 400 else 50.0; n = 30
        x0 = round(c[0] / step) * step; z0 = round(c[2] / step) * step
        gl.glBegin(gl.GL_LINES)
        for i in range(-n, n + 1):
            a = 0.35 if i % 5 == 0 else 0.15
            gl.glColor4f(0.8, 0.8, 0.9, a)
            gl.glVertex3f(x0 + i * step, 0, z0 - n * step); gl.glVertex3f(x0 + i * step, 0, z0 + n * step)
            gl.glVertex3f(x0 - n * step, 0, z0 + i * step); gl.glVertex3f(x0 + n * step, 0, z0 + i * step)
        gl.glEnd()
        gl.glBegin(gl.GL_LINES)
        gl.glColor4f(1, 0.3, 0.3, 0.9); gl.glVertex3f(-500, 0, 0); gl.glVertex3f(500, 0, 0)
        gl.glColor4f(0.3, 0.5, 1, 0.9); gl.glVertex3f(0, 0, -500); gl.glVertex3f(0, 0, 500)
        gl.glEnd()
        gl.glEnable(gl.GL_DEPTH_TEST)

    def draw_geometry(self):
        gl.glEnableClientState(gl.GL_VERTEX_ARRAY)
        mats = self.doc.ir.materials
        gl.glDisable(gl.GL_CULL_FACE)
        if self.mode == 2:
            gl.glPolygonMode(gl.GL_FRONT_AND_BACK, gl.GL_LINE); gl.glDisable(gl.GL_TEXTURE_2D)
        gl.glEnableClientState(gl.GL_COLOR_ARRAY)
        for name, (pos, uv, col, ccol) in self.cache.batches.items():
            m = mats.get(name); t = None
            if self.mode == 0 and m is not None:
                t = self.tex.get(m.texture)
            gl.glVertexPointer(3, gl.GL_FLOAT, 0, pos)
            if self.mode == 0:
                shade = np.clip(col * 1.5, 0, 1) if m is None or m.mode != 0 else col
                if t:
                    gl.glEnable(gl.GL_TEXTURE_2D); gl.glBindTexture(gl.GL_TEXTURE_2D, t[0])
                    gl.glEnableClientState(gl.GL_TEXTURE_COORD_ARRAY); gl.glTexCoordPointer(2, gl.GL_FLOAT, 0, uv)
                    gl.glColorPointer(3, gl.GL_FLOAT, 0, np.ascontiguousarray(shade, np.float32))
                    gl.glEnable(gl.GL_ALPHA_TEST); gl.glAlphaFunc(gl.GL_GREATER, 0.4 if (m and m.flags & 1) else 0.02)
                else:
                    gl.glDisable(gl.GL_TEXTURE_2D); gl.glDisableClientState(gl.GL_TEXTURE_COORD_ARRAY)
                    base = np.array(m.color_a, np.float32) * 1.4 if (m and not m.texture) else np.array((1.0, 0.2, 1.0), np.float32)   # magenta = texture missing
                    gl.glColorPointer(3, gl.GL_FLOAT, 0, np.ascontiguousarray(np.clip(shade * base * 1.3, 0, 1), np.float32))
            elif self.mode == 1:
                gl.glDisable(gl.GL_TEXTURE_2D); gl.glDisableClientState(gl.GL_TEXTURE_COORD_ARRAY)
                gl.glColorPointer(3, gl.GL_FLOAT, 0, ccol)
            else:
                gl.glColorPointer(3, gl.GL_FLOAT, 0, ccol)
            gl.glDrawArrays(gl.GL_TRIANGLES, 0, len(pos))
            gl.glDisable(gl.GL_ALPHA_TEST)
        gl.glDisableClientState(gl.GL_TEXTURE_COORD_ARRAY); gl.glDisableClientState(gl.GL_COLOR_ARRAY)
        gl.glDisable(gl.GL_TEXTURE_2D)
        gl.glPolygonMode(gl.GL_FRONT_AND_BACK, gl.GL_FILL)
        gl.glDisableClientState(gl.GL_VERTEX_ARRAY)

    def draw_points_lights(self):
        gl.glDisable(gl.GL_TEXTURE_2D)
        sel = set(id(n) for n in self.doc.selection)
        size_k = max(0.5, self.dist * 0.012)
        ai_pts = []
        gl.glLineWidth(1.5)
        gl.glBegin(gl.GL_LINES)
        if self.show["points"]:
            for p in self.doc.ir.points:
                if p.room in self.hidden_rooms: continue
                c = KIND_COLORS.get(p.kind, (1, 1, 1)); s = size_k * (0.5 if p.kind == "ai" else 1.0)
                if id(p) in sel: c = (1.0, 0.5, 0.0); s *= 1.4
                gl.glColor3f(*c)
                x, y, z = p.pos[0], p.pos[1], -p.pos[2]
                for dx, dy, dz in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
                    gl.glVertex3f(x - dx * s, y - dy * s, z - dz * s); gl.glVertex3f(x + dx * s, y + dy * s, z + dz * s)
                # facing arrow along local +Z
                f = np.array(p.matrix[2]); f = np.array([f[0], f[1], -f[2]])
                gl.glVertex3f(x, y, z); gl.glVertex3f(x + f[0] * s * 2.5, y + f[1] * s * 2.5, z + f[2] * s * 2.5)
                if p.kind == "ai": ai_pts.append((x, y, z))
        if self.show["lights"]:
            for l in self.doc.ir.lights:
                if l.room in self.hidden_rooms: continue
                c = (1.0, 0.5, 0.0) if id(l) in sel else (*[min(1, x + 0.2) for x in l.color],)
                gl.glColor3f(*c); x, y, z = l.pos[0], l.pos[1], -l.pos[2]; s = size_k * 0.8
                for dx, dy, dz in ((1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0), (1, -1, 0), (0, 1, 1), (0, 1, -1)):
                    gl.glVertex3f(x - dx * s, y - dy * s, z - dz * s); gl.glVertex3f(x + dx * s, y + dy * s, z + dz * s)
        if self.show["ai"] and len(ai_pts) > 1:
            a = np.array(ai_pts); gl.glColor4f(0.2, 0.9, 1.0, 0.35)
            d = np.linalg.norm(a[:, None] - a[None, :], axis=2)
            ii, jj = np.nonzero(np.triu(d < 30.0, 1))
            for i, j in zip(ii, jj):
                gl.glVertex3f(*a[i]); gl.glVertex3f(*a[j])
        gl.glEnd()
        gl.glLineWidth(1.0)

    def draw_selection(self):
        gl.glDisable(gl.GL_TEXTURE_2D)
        gl.glPolygonMode(gl.GL_FRONT_AND_BACK, gl.GL_LINE)
        gl.glEnable(gl.GL_POLYGON_OFFSET_LINE); gl.glPolygonOffset(-1, -1)
        gl.glColor3f(1.0, 0.55, 0.0)
        gl.glEnableClientState(gl.GL_VERTEX_ARRAY)
        for n in self.doc.selection:
            t = self.cache.obj_tris.get(id(n))
            if t is not None:
                arr = np.ascontiguousarray(t.reshape(-1, 3), np.float32)
                gl.glVertexPointer(3, gl.GL_FLOAT, 0, arr); gl.glDrawArrays(gl.GL_TRIANGLES, 0, len(arr))
        gl.glDisableClientState(gl.GL_VERTEX_ARRAY)
        gl.glDisable(gl.GL_POLYGON_OFFSET_LINE)
        gl.glPolygonMode(gl.GL_FRONT_AND_BACK, gl.GL_FILL)

    def pivot(self) -> Optional[np.ndarray]:
        if not self.doc.selection: return None
        return to_gl(np.mean([n.pos for n in self.doc.selection], axis=0))

    def gizmo_len(self) -> float:
        return self.dist * 0.14

    def draw_gizmo(self):
        p = self.pivot()
        if p is None or self.tool == TOOL_SELECT: return
        L = self.gizmo_len()
        gl.glDisable(gl.GL_DEPTH_TEST); gl.glLineWidth(3.0)
        gl.glBegin(gl.GL_LINES)
        for axis, col in ((0, (1, 0.2, 0.2)), (1, (0.2, 1, 0.2)), (2, (0.3, 0.5, 1))):
            gl.glColor3f(*col); gl.glVertex3f(*p); q = p.copy(); q[axis] += L; gl.glVertex3f(*q)
        gl.glEnd()
        gl.glPointSize(9.0); gl.glBegin(gl.GL_POINTS)
        for axis, col in ((0, (1, 0.2, 0.2)), (1, (0.2, 1, 0.2)), (2, (0.3, 0.5, 1))):
            gl.glColor3f(*col); q = p.copy(); q[axis] += L; gl.glVertex3f(*q)
        gl.glEnd()
        gl.glLineWidth(1.0); gl.glEnable(gl.GL_DEPTH_TEST)

    # ---------------- offscreen capture (used by tests / screenshots)
    def capture(self) -> np.ndarray:
        self.SetCurrent(self.ctx)
        if not self.gl_ready: self.init_gl()
        self.size = self.GetClientSize()
        self.render()
        w, h = self.size.width, self.size.height
        gl.glPixelStorei(gl.GL_PACK_ALIGNMENT, 1)
        data = gl.glReadPixels(0, 0, w, h, gl.GL_RGB, gl.GL_UNSIGNED_BYTE)
        arr = np.frombuffer(data, np.uint8).reshape(h, w, 3)[::-1]
        return arr

    # ---------------- picking
    def pick(self, x, y):
        o, d = self.ray(x, y)
        obj, t_obj = self.cache.pick_object(o, d)
        best = obj; best_t = t_obj
        # points / lights: nearest to ray within a screen-size dependent radius
        for lst, flag in ((self.doc.ir.points, "points"), (self.doc.ir.lights, "lights")):
            if not self.show[flag]: continue
            for n in lst:
                if n.room in self.hidden_rooms: continue
                p = to_gl(n.pos); v = p - o; t = float(v @ d)
                if t <= 0: continue
                dist = float(np.linalg.norm(v - t * d))
                if dist < max(0.4, t * 0.015) and t < best_t + 5.0:
                    best = n; best_t = t
                    if best_t < t_obj: break
        return best

    # ---------------- interaction
    def on_ldown(self, e):
        self.SetFocus(); self.CaptureMouse()
        x, y = e.GetPosition(); self.last = (x, y)
        if e.AltDown():
            self.drag = ("orbit",); return
        h = self.hit_gizmo(x, y) if self.tool != TOOL_SELECT else None
        if h is not None and self.doc.selection:
            self.begin_transform(h, x, y); return
        n = self.pick(x, y)
        if n is not None:
            if e.ControlDown() or e.ShiftDown():
                sel = list(self.doc.selection)
                if n in sel: sel.remove(n)
                else: sel.append(n)
                self.doc.set_selection(sel)
            elif n not in self.doc.selection:
                self.doc.set_selection([n])
            if self.tool != TOOL_SELECT and not e.ShiftDown() and not e.ControlDown():
                self.begin_transform(("plane",) if self.tool == TOOL_MOVE else ("free",), x, y)
            else:
                self.drag = ("click",)
        else:
            if not (e.ControlDown() or e.ShiftDown()): self.doc.set_selection([])
            self.drag = ("click",)
        self.Refresh(False)

    def on_lup(self, e):
        if self.HasCapture(): self.ReleaseMouse()
        if self.drag and self.drag[0] in ("move", "rotate", "scale"):
            self.end_transform()
        self.drag = None

    def on_rdown(self, e):
        self.SetFocus(); self.CaptureMouse(); self.last = tuple(e.GetPosition())
        self.drag = ("pan",) if (e.ShiftDown() or e.MiddleDown()) else ("orbit",)

    def on_rup(self, e):
        if self.HasCapture(): self.ReleaseMouse()
        self.drag = None

    def on_wheel(self, e):
        k = 0.88 if e.GetWheelRotation() > 0 else 1.14
        if e.ControlDown():
            o, d = self.ray(*e.GetPosition())
        self.dist = max(1.0, min(4000.0, self.dist * k)); self.Refresh(False)

    def on_motion(self, e):
        x, y = e.GetPosition(); dx, dy = x - self.last[0], y - self.last[1]
        if self.drag is None:
            if self.on_pick_info and not e.Dragging():
                pass
            self.last = (x, y); return
        kind = self.drag[0]
        if kind == "orbit":
            self.yaw -= dx * 0.4; self.pitch = max(-89.0, min(89.0, self.pitch + dy * 0.4))
        elif kind == "pan":
            eye = self.eye(); f = self.target - eye; f /= np.linalg.norm(f)
            s = np.cross(f, [0, 1.0, 0]); s /= np.linalg.norm(s) + 1e-12; u = np.cross(s, f)
            k = self.dist * 0.0016
            self.target = self.target - s * dx * k + u * dy * k
        elif kind in ("move", "rotate", "scale"):
            self.update_transform(x, y, e)
        self.last = (x, y); self.Refresh(False)

    # ---- gizmo hit test
    def hit_gizmo(self, x, y):
        p = self.pivot()
        if p is None: return None
        L = self.gizmo_len(); a = self.project(p)
        if a is None: return None
        best = None; bd = 12.0
        for axis in range(3):
            q = p.copy(); q[axis] += L; b = self.project(q)
            if b is None: continue
            d = dist_point_segment((x, y), a, b)
            if d < bd: bd = d; best = ("axis", axis)
        return best

    # ---- transform session
    def begin_transform(self, handle, x, y):
        sel = self.doc.selection
        self.t_nodes = list(sel)
        self.t_old = [(n.pos, n.matrix, n.scale) for n in sel]
        self.t_pivot = self.pivot().copy()
        o, d = self.ray(x, y)
        self.t_handle = handle
        if handle[0] == "axis":
            a = np.zeros(3); a[handle[1]] = 1.0
            self.t_axis = a; self.t_t0 = closest_line_param(o, d, self.t_pivot, a)
        elif handle[0] == "plane":
            self.t_t0 = ray_plane_y(o, d, self.t_pivot[1])
        self.t_start = (x, y)
        kind = {"axis": "move", "plane": "move", "free": {TOOL_ROTATE: "rotate", TOOL_SCALE: "scale"}.get(self.tool, "move")}[handle[0]]
        if self.tool == TOOL_ROTATE and handle[0] in ("axis", "plane", "free"): kind = "rotate"
        if self.tool == TOOL_SCALE and handle[0] in ("axis", "plane", "free"): kind = "scale"
        self.drag = (kind,); self.t_moved = False

    def snapv(self, v):
        return round(v / self.snap) * self.snap if self.snap > 0 else v

    def update_transform(self, x, y, e):
        o, d = self.ray(x, y)
        kind = self.drag[0]; self.t_moved = True
        if kind == "move":
            if self.t_handle[0] == "axis":
                t = closest_line_param(o, d, self.t_pivot, self.t_axis); delta = self.t_axis * (t - self.t_t0)
            else:
                t = ray_plane_y(o, d, self.t_pivot[1])
                if t is None or self.t_t0 is None: return
                delta = t - self.t_t0
            tgt = self.t_pivot + delta
            if self.snap > 0:
                tgt = np.array([self.snapv(c) for c in tgt]); delta = tgt - self.t_pivot
            dcc = (delta[0], delta[1], -delta[2])
            for n, (p, m, s) in zip(self.t_nodes, self.t_old):
                n.pos = (p[0] + dcc[0], p[1] + dcc[1], p[2] + dcc[2])
        elif kind == "rotate":
            ang = (x - self.t_start[0]) * 0.5
            if self.snap > 0 or e.ControlDown(): ang = round(ang / 5.0) * 5.0
            pc = from_gl(self.t_pivot)
            Q = yaw_matrix(ang); qa = np.array(Q)
            for n, (p, m, s) in zip(self.t_nodes, self.t_old):
                n.matrix = mat_mul_rows(m, Q)
                if len(self.t_nodes) > 1:
                    v = np.array([p[0] - pc[0], p[1] - pc[1], p[2] - pc[2]]) @ qa
                    n.pos = (pc[0] + v[0], pc[1] + v[1], pc[2] + v[2])
        elif kind == "scale":
            k = math.exp((x - self.t_start[0]) * 0.01)
            for n, (p, m, s) in zip(self.t_nodes, self.t_old):
                n.scale = max(0.01, s * k)
        self.dirty_cache = True

    def end_transform(self):
        if not self.t_moved: return
        new = [(n.pos, n.matrix, n.scale) for n in self.t_nodes]
        old = self.t_old
        nodes = self.t_nodes
        self.doc.dirty = True
        self.doc.undo_stack.append(("Transform", lambda: self._apply(nodes, new), lambda: self._apply(nodes, old)))
        self.doc.redo_stack.clear()
        self.doc.notify("scene")

    def _apply(self, nodes, states):
        for n, (p, m, s) in zip(nodes, states): n.pos, n.matrix, n.scale = p, m, s

    # ---- keys
    def on_key(self, e):
        k = e.GetKeyCode()
        if k == ord("F"): self.frame_selection()
        elif k == ord("G"): self.tool = TOOL_MOVE; self.doc.notify("tool")
        elif k == ord("R"): self.tool = TOOL_ROTATE; self.doc.notify("tool")
        elif k == ord("S") and not e.ControlDown(): self.tool = TOOL_SCALE; self.doc.notify("tool")
        elif k == ord("Q"): self.tool = TOOL_SELECT; self.doc.notify("tool")
        elif k == wx.WXK_DELETE: self.doc.delete_nodes(self.doc.selection)
        elif k == wx.WXK_ESCAPE: self.doc.set_selection([])
        elif k == wx.WXK_HOME: self.frame_all()
        elif k in (ord("1"), ord("2"), ord("3")): self.mode = k - ord("1"); self.dirty_cache = True
        elif k == ord("T"): self.pitch = 89.0
        elif k == wx.WXK_NUMPAD0 or k == ord("0"): self.yaw, self.pitch = 35.0, 35.0
        elif k == ord("D") and e.ControlDown(): self.doc.duplicate(self.doc.selection)
        elif k == ord("Z") and e.ControlDown(): self.doc.undo()
        elif k == ord("Y") and e.ControlDown(): self.doc.redo()
        else:
            e.Skip(); return
        self.Refresh(False)


# ----------------------------------------------------------------------------------------------
def dist_point_segment(p, a, b) -> float:
    p = np.array(p, float); a = np.array(a, float); b = np.array(b, float)
    ab = b - a; l2 = float(ab @ ab)
    if l2 < 1e-9: return float(np.linalg.norm(p - a))
    t = max(0.0, min(1.0, float((p - a) @ ab) / l2))
    return float(np.linalg.norm(p - (a + t * ab)))


def closest_line_param(o, d, p, a) -> float:
    """parameter t of the point p + t*a (unit a) closest to the ray o + s*d"""
    w = p - o; b = float(a @ d); dd = float(a @ w); e = float(d @ w)
    den = 1.0 - b * b
    if abs(den) < 1e-6: return 0.0
    return (b * e - dd) / den


def ray_plane_y(o, d, y):
    if abs(d[1]) < 1e-6: return None
    t = (y - o[1]) / d[1]
    return o + d * t if t > 0 else None
