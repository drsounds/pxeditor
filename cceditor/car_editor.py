"""
Car editor - a GUI for Pickup Express vehicle configs (.vcfg).

    python -m cceditor.car_editor [file.vcfg] [--screenshot out.png]

Left:   all vehicles found in the game folder (and any file opened with File > Open).
Centre: the vehicle model (the .ccf next to the .vcfg) with the config vectors drawn on top.
Right:  every field of the config.  Fields whose engine meaning is not proven are labelled "(?)"; hover a field
        for its raw engine offset, the range used by the shipped vehicles and the value in the compare vehicle.
"""
from __future__ import annotations
import glob, os, sys
from typing import Dict, List, Optional

import numpy as np
import wx
import wx.propgrid as pg
from OpenGL import GL as gl

from ccformats.vcfg import VehicleConfig, SCALAR_FIELDS, VECTOR_FIELDS
from .doc import Document
from .glview import Viewport

DEFAULT_GAME = r"C:\Program Files\Addgames\Pickup Express"

# label, tooltip.  (?) = meaning inferred from the shipped values only.
SCALAR_INFO = {
    "s_5c": ("Mass (?)", "2000 for most cars, 1500 for the Celica, 5000 for the bus and Camry"),
    "s_60": ("Gravity / downforce (?)", "-30 to -45"),
    "s_dc": ("Engine force A (?)", ""),
    "s_e0": ("Engine force B (?)", ""),
    "s_e4": ("Engine damping (?)", ""),
    "s_70": ("Drag / grip factor (?)", "2 for cars, 30 for the bus"),
    "s_74": ("Top speed (?)", "33-41 for cars, 70 for the bus"),
    "s_b4": ("Suspension stiffness F (?)", ""),
    "s_b8": ("Suspension stiffness R (?)", ""),
    "s_bc": ("Suspension damping (?)", ""),
    "s_c0": ("Tyre grip scale (?)", ""),
    "s_c4": ("Wheel radius / travel (?)", ""),
    "s_c8": ("Steering lock (?)", ""),
    "s_cc": ("Steering speed (?)", ""),
    "s_d0": ("Roll influence (?)", "negative in all vehicles"),
    "s_d4": ("Friction (?)", ""),
    "s_d8": ("Brake force (?)", ""),
    "i_48": ("Gears / drive type (?)", "integer"),
    "i_4c": ("Setting 4c (?)", "integer 0-5"),
    "i_50": ("Setting 50 (?)", "integer 0-5"),
    "i_54": ("Setting 54 (?)", "integer"),
    "i_58": ("Setting 58 (?)", "integer"),
    "s_68": ("Setting 68 (?)", ""),
    "s_6c": ("Rev range / gear ratio (?)", "23-31 for cars, 100 for the bus"),
}
VECTOR_INFO = {
    "v_78": ("Body box half extents (?)", "x = half width, y = half height, z = half length; drawn orange"),
    "v_84": ("Vector 84 (?)", "almost always (0, 0.05, 0); drawn yellow"),
    "v_90": ("Vector 90 (?)", "roughly (4*half width, height, 2*half length); drawn magenta"),
    "v_9c": ("Centre of mass offset (?)", "drawn green"),
    "v_a8": ("Vector a8 (?)", "small offset; drawn cyan"),
}
VECTOR_COLORS = {"v_78": (1.0, 0.55, 0.1), "v_84": (1.0, 1.0, 0.2), "v_90": (1.0, 0.2, 0.9), "v_9c": (0.2, 1.0, 0.3), "v_a8": (0.2, 0.9, 1.0)}
INT_FIELDS = {n for n in SCALAR_FIELDS if n.startswith("i_")}


# ----------------------------------------------------------------------------------------------
class CarView(Viewport):
    """Viewport that also draws the config vectors around the vehicle's root null."""

    def __init__(self, parent, doc: Document, get_cfg):
        super().__init__(parent, doc)
        self.get_cfg = get_cfg
        self.root = np.zeros(3)
        self.show.update(points=True, lights=False, ai=False)

    def set_vehicle(self, path: Optional[str]):
        if path and os.path.exists(path):
            self.doc.load(path)
        else:
            self.doc.ir.objects.clear(); self.doc.ir.points.clear(); self.doc.ir.lights.clear(); self.doc.notify("loaded")
        root = next((p for p in self.doc.ir.points if p.name.lower() == "root"), None)
        self.root = np.array([root.pos[0], root.pos[1], root.pos[2]]) if root else np.zeros(3)
        self.target = np.array([self.root[0], self.root[1], -self.root[2]]); self.dist = 9.0; self.yaw = 145.0; self.pitch = 22.0
        self.Refresh(False)

    def draw_selection(self):
        super().draw_selection()
        cfg = self.get_cfg()
        if cfg is None: return
        gl.glDisable(gl.GL_TEXTURE_2D); gl.glDisable(gl.GL_DEPTH_TEST); gl.glLineWidth(2.0)
        ox, oy, oz = self.root[0], self.root[1], -self.root[2]
        gl.glBegin(gl.GL_LINES)
        hx, hy, hz = cfg.vectors[0]
        gl.glColor3f(*VECTOR_COLORS["v_78"])
        cs = [(sx * hx, sy * hy, sz * hz) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
        for i, a in enumerate(cs):
            for j, b in enumerate(cs):
                if i < j and sum(abs(a[k] - b[k]) > 0 for k in range(3)) == 1:
                    gl.glVertex3f(ox + a[0], oy + a[1], oz + a[2]); gl.glVertex3f(ox + b[0], oy + b[1], oz + b[2])
        s = 0.25
        for name, v in zip(VECTOR_FIELDS, cfg.vectors):
            if name == "v_78": continue
            gl.glColor3f(*VECTOR_COLORS[name])
            x, y, z = ox + v[0], oy + v[1], oz - v[2]
            for d in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
                gl.glVertex3f(x - d[0] * s, y - d[1] * s, z - d[2] * s); gl.glVertex3f(x + d[0] * s, y + d[1] * s, z + d[2] * s)
        gl.glEnd(); gl.glLineWidth(1.0); gl.glEnable(gl.GL_DEPTH_TEST)


# ----------------------------------------------------------------------------------------------
class CarFrame(wx.Frame):
    def __init__(self, path: Optional[str] = None):
        super().__init__(None, title="Pickup Express - Car Editor", size=(1400, 850))
        self.cfg: Optional[VehicleConfig] = None
        self.orig: Optional[VehicleConfig] = None
        self.path: Optional[str] = None
        self.dirty = False
        self.undo: List[bytes] = []; self.redo: List[bytes] = []
        self.library: Dict[str, VehicleConfig] = {}
        self.files: List[str] = []
        self.props: Dict[str, pg.PGProperty] = {}
        self._loading = False

        self.doc = Document()
        self.CreateStatusBar()
        self.build_menu()

        split = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)
        left = wx.Panel(split)
        ls = wx.BoxSizer(wx.VERTICAL)
        self.listbox = wx.ListBox(left, style=wx.LB_SINGLE)
        ls.Add(wx.StaticText(left, label="Vehicles"), 0, wx.ALL, 4)
        ls.Add(self.listbox, 1, wx.EXPAND | wx.ALL, 2)
        left.SetSizer(ls)
        split2 = wx.SplitterWindow(split, style=wx.SP_LIVE_UPDATE)
        self.view = CarView(split2, self.doc, lambda: self.cfg)
        right = wx.Panel(split2)
        rs = wx.BoxSizer(wx.VERTICAL)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(right, label="Compare with:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 4)
        self.compare = wx.Choice(right); row.Add(self.compare, 1, wx.ALL, 2)
        rs.Add(row, 0, wx.EXPAND)
        self.grid = pg.PropertyGrid(right, style=pg.PG_SPLITTER_AUTO_CENTER | pg.PG_TOOLTIPS)
        rs.Add(self.grid, 1, wx.EXPAND)
        right.SetSizer(rs)
        split.SplitVertically(left, split2, 190)
        split2.SplitVertically(self.view, right, -420)
        split2.SetSashGravity(1.0)
        self.build_grid()

        self.listbox.Bind(wx.EVT_LISTBOX, self.on_pick)
        self.compare.Bind(wx.EVT_CHOICE, lambda e: self.refresh_tooltips())
        self.grid.Bind(pg.EVT_PG_CHANGED, self.on_changed)
        self.Bind(wx.EVT_CLOSE, self.on_close)

        self.scan(DEFAULT_GAME if os.path.isdir(DEFAULT_GAME) else None)
        if path: self.open_file(path)
        elif self.files: self.listbox.SetSelection(0); self.open_file(self.files[0])

    # ------------------------------------------------------------------ menus
    def build_menu(self):
        mb = wx.MenuBar()
        fm = wx.Menu()
        items = [("Open...\tCtrl+O", self.on_open), ("Save\tCtrl+S", self.on_save), ("Save As...\tCtrl+Shift+S", self.on_save_as),
                 ("Duplicate as new vehicle...", self.on_duplicate), ("Revert", self.on_revert), (None, None), ("Quit\tCtrl+Q", lambda e: self.Close())]
        for label, fn in items:
            if label is None: fm.AppendSeparator(); continue
            it = fm.Append(wx.ID_ANY, label); self.Bind(wx.EVT_MENU, fn, it)
        mb.Append(fm, "&File")
        em = wx.Menu()
        for label, fn in (("Undo\tCtrl+Z", self.on_undo), ("Redo\tCtrl+Y", self.on_redo), ("Reset field to compare value", self.on_reset_to_compare),
                          ("Copy all values from compare vehicle", self.on_copy_compare)):
            it = em.Append(wx.ID_ANY, label); self.Bind(wx.EVT_MENU, fn, it)
        mb.Append(em, "&Edit")
        self.SetMenuBar(mb)

    # ------------------------------------------------------------------ grid
    def build_grid(self):
        g = self.grid
        g.Append(pg.PropertyCategory("General"))
        self.props["name"] = g.Append(pg.StringProperty("Name", "name", "")); self.props["flag"] = g.Append(pg.BoolProperty("Large vehicle / bus flag (?)", "flag", False))
        g.SetPropertyAttribute("flag", "UseCheckbox", True)
        g.Append(pg.PropertyCategory("Vectors"))
        for n in VECTOR_FIELDS:
            label = VECTOR_INFO[n][0]
            parent = g.Append(pg.StringProperty(label, n, "")); g.SetPropertyReadOnly(n)
            for ax in "xyz":
                p = pg.FloatProperty(ax.upper(), ax, 0.0)
                g.AppendIn(parent, p); self.props[f"{n}.{ax}"] = p
            g.Expand(n)
        g.Append(pg.PropertyCategory("Physics (floats)"))
        for n in SCALAR_FIELDS:
            if n in INT_FIELDS: continue
            self.props[n] = g.Append(pg.FloatProperty(SCALAR_INFO[n][0], n, 0.0))
        g.Append(pg.PropertyCategory("Settings (integers)"))
        for n in SCALAR_FIELDS:
            if n not in INT_FIELDS: continue
            self.props[n] = g.Append(pg.IntProperty(SCALAR_INFO[n][0], n, 0))
        # the two trailing floats are stored after the integers; they live in the float section above (s_68 / s_6c)

    def fill_grid(self):
        self._loading = True
        c = self.cfg
        self.grid.SetPropertyValue("name", c.name); self.grid.SetPropertyValue("flag", bool(c.flag))
        for n, v in zip(VECTOR_FIELDS, c.vectors):
            for ax, x in zip("xyz", v): self.grid.SetPropertyValue(f"{n}.{ax}", float(x))
        for n, v in zip(SCALAR_FIELDS, c.scalars):
            self.grid.SetPropertyValue(n, int(v) if n in INT_FIELDS else float(v))
        self._loading = False
        self.refresh_tooltips()

    def shipped_range(self, n: str):
        i = SCALAR_FIELDS.index(n)
        vals = [c.scalars[i] for c in self.library.values()]
        return (min(vals), max(vals)) if vals else (0, 0)

    def refresh_tooltips(self):
        other = self.library.get(self.compare.GetStringSelection())
        changed = []
        for n in SCALAR_FIELDS:
            p = self.props[n]
            lo, hi = self.shipped_range(n)
            tip = f"{n}  (engine offset 0x{n[2:]})\nshipped range: {lo:g} .. {hi:g}\n{SCALAR_INFO[n][1]}"
            i = SCALAR_FIELDS.index(n)
            if other: tip += f"\ncompare ({other.name}): {other.scalars[i]:g}"
            self.grid.SetPropertyHelpString(p, tip)
            self._mark(p, self.orig is not None and abs(self.cfg.scalars[i] - self.orig.scalars[i]) > 1e-9)
        for n in VECTOR_FIELDS:
            tip = f"{n}\n{VECTOR_INFO[n][1]}"
            if other: tip += "\ncompare ({}): {}".format(other.name, tuple(round(x, 3) for x in other.vectors[VECTOR_FIELDS.index(n)]))
            self.grid.SetPropertyHelpString(n, tip)
            vi = VECTOR_FIELDS.index(n)
            for ax in "xyz":
                self.grid.SetPropertyHelpString(self.props[f"{n}.{ax}"], tip)
                self._mark(self.props[f"{n}.{ax}"], self.orig is not None and abs(self.cfg.vectors[vi]["xyz".index(ax)] - self.orig.vectors[vi]["xyz".index(ax)]) > 1e-9)
        self._mark(self.props["name"], self.cfg.name != self.orig.name)
        self._mark(self.props["flag"], self.cfg.flag != self.orig.flag)

    def _mark(self, prop, changed: bool):
        prop.SetBackgroundColour(wx.Colour(255, 240, 180) if changed else wx.NullColour)

    # ------------------------------------------------------------------ editing
    def snapshot(self) -> bytes:
        return self.cfg.to_bytes()

    def restore(self, data: bytes):
        keep = self.cfg.name_raw
        self.cfg = VehicleConfig.from_bytes(data); self.cfg.name_raw = keep
        self.fill_grid(); self.view.Refresh(False)

    def push_undo(self, before: bytes):
        self.undo.append(before); self.redo.clear(); self.set_dirty(True)

    def on_changed(self, e):
        if self._loading or self.cfg is None: return
        before = self.snapshot()
        p = e.GetProperty(); n = p.GetName(); v = p.GetValue(); c = self.cfg
        if n == "name": c.name = str(v)[:31]
        elif n == "flag": c.flag = 1 if v else 0
        elif "." in n:
            key, ax = n.split("."); vi = VECTOR_FIELDS.index(key); vec = list(c.vectors[vi]); vec["xyz".index(ax)] = float(v); c.vectors[vi] = tuple(vec)
        else:
            c.scalars[SCALAR_FIELDS.index(n)] = int(v) if n in INT_FIELDS else float(v)
        if self.snapshot() != before: self.push_undo(before)
        self.refresh_tooltips(); self.view.Refresh(False)

    def on_undo(self, e):
        if self.undo and self.cfg: self.redo.append(self.snapshot()); self.restore(self.undo.pop()); self.set_dirty(True)

    def on_redo(self, e):
        if self.redo and self.cfg: self.undo.append(self.snapshot()); self.restore(self.redo.pop()); self.set_dirty(True)

    def on_reset_to_compare(self, e):
        other = self.library.get(self.compare.GetStringSelection()); p = self.grid.GetSelection()
        if not (other and p and self.cfg): return
        n = p.GetName(); before = self.snapshot()
        if n in SCALAR_FIELDS:
            i = SCALAR_FIELDS.index(n); self.cfg.scalars[i] = other.scalars[i]
        elif "." in n or n in VECTOR_FIELDS:
            key = n.split(".")[0]; vi = VECTOR_FIELDS.index(key); self.cfg.vectors[vi] = other.vectors[vi]
        else: return
        if self.snapshot() != before: self.push_undo(before)
        self.fill_grid(); self.view.Refresh(False)

    def on_copy_compare(self, e):
        other = self.library.get(self.compare.GetStringSelection())
        if not (other and self.cfg): return
        before = self.snapshot()
        self.cfg.vectors = list(other.vectors); self.cfg.scalars = list(other.scalars); self.cfg.flag = other.flag
        if self.snapshot() != before: self.push_undo(before)
        self.fill_grid(); self.view.Refresh(False)

    # ------------------------------------------------------------------ files
    def scan(self, game: Optional[str]):
        self.files = sorted(glob.glob(os.path.join(game, "Vehicles", "*", "*.vcfg"))) if game else []
        self.library = {}
        for f in self.files:
            try:
                c = VehicleConfig.load(f); self.library[c.name] = c
            except Exception: pass
        self.refresh_list()

    def refresh_list(self):
        self.listbox.Set([("%s  (%s)" % (os.path.splitext(os.path.basename(f))[0], os.path.basename(os.path.dirname(f)))) for f in self.files])
        names = list(self.library)
        self.compare.Set(names)
        if names: self.compare.SetSelection(0)

    def set_dirty(self, d: bool):
        self.dirty = d
        self.SetTitle("Pickup Express - Car Editor - %s%s" % (os.path.basename(self.path or "(new)"), " *" if d else ""))

    def confirm_discard(self) -> bool:
        if not self.dirty: return True
        return wx.MessageBox("Discard unsaved changes?", "Car Editor", wx.YES_NO | wx.ICON_QUESTION) == wx.YES

    def on_pick(self, e):
        i = self.listbox.GetSelection()
        if i >= 0 and self.confirm_discard(): self.open_file(self.files[i])
        elif self.path in self.files: self.listbox.SetSelection(self.files.index(self.path))

    def open_file(self, path: str):
        try:
            self.cfg = VehicleConfig.load(path)
        except Exception as ex:
            wx.MessageBox(f"Could not read {path}:\n{ex}", "Car Editor", wx.ICON_ERROR); return
        self.orig = VehicleConfig.from_bytes(self.cfg.to_bytes()); self.orig.name_raw = self.cfg.name_raw
        self.path = path; self.undo.clear(); self.redo.clear()
        if path in self.files: self.listbox.SetSelection(self.files.index(path))
        self.fill_grid()
        try: self.view.set_vehicle(os.path.splitext(path)[0] + ".ccf")
        except Exception as ex: self.SetStatusText(f"model preview failed: {ex}")
        self.set_dirty(False)
        self.SetStatusText(path)

    def on_open(self, e):
        if not self.confirm_discard(): return
        with wx.FileDialog(self, "Open vehicle config", wildcard="Vehicle config (*.vcfg)|*.vcfg", style=wx.FD_OPEN) as d:
            if d.ShowModal() == wx.ID_OK:
                self.open_file(d.GetPath())
                if d.GetPath() not in self.files: self.files.append(d.GetPath()); self.refresh_list(); self.listbox.SetSelection(self.listbox.GetCount() - 1)

    def write(self, path: str) -> bool:
        try:
            self.cfg.save(path)
        except PermissionError:
            wx.MessageBox("The game folder is write-protected.\nUse File > Save As to save the vehicle elsewhere and copy it in with administrator rights.", "Car Editor", wx.ICON_WARNING)
            return False
        except Exception as ex:
            wx.MessageBox(f"Save failed: {ex}", "Car Editor", wx.ICON_ERROR); return False
        self.path = path; self.orig = VehicleConfig.from_bytes(self.cfg.to_bytes()); self.orig.name_raw = self.cfg.name_raw
        self.set_dirty(False); self.refresh_tooltips(); self.SetStatusText("saved " + path)
        return True

    def on_save(self, e):
        if self.cfg and self.path:
            if not self.write(self.path): self.on_save_as(e)

    def on_save_as(self, e):
        if not self.cfg: return
        with wx.FileDialog(self, "Save vehicle config", defaultFile=os.path.basename(self.path or "vehicle.vcfg"), wildcard="Vehicle config (*.vcfg)|*.vcfg",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as d:
            if d.ShowModal() == wx.ID_OK: self.write(d.GetPath())

    def on_duplicate(self, e):
        if not self.cfg: return
        with wx.TextEntryDialog(self, "Name of the new vehicle (max 31 characters):", "Duplicate", self.cfg.name + " copy") as d:
            if d.ShowModal() != wx.ID_OK: return
            before = self.snapshot()
            self.cfg.name = d.GetValue()[:31]; self.cfg.name_raw = b""
            self.path = None; self.push_undo(before); self.fill_grid(); self.on_save_as(e)

    def on_revert(self, e):
        if self.path and self.confirm_discard(): self.open_file(self.path)

    def on_close(self, e):
        if e.CanVeto() and not self.confirm_discard(): e.Veto(); return
        self.Destroy()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    shot = None; path = None
    if "--screenshot" in argv:
        i = argv.index("--screenshot"); shot = argv[i + 1]; del argv[i:i + 2]
    if argv: path = argv[0]
    app = wx.App(False)
    fr = CarFrame(path); fr.Show()
    if shot:
        def grab():
            from PIL import Image
            Image.fromarray(fr.view.capture()).save(shot)   # saves the 3D view only
            fr.dirty = False; fr.Close(True)
        wx.CallLater(3000, grab)
    app.MainLoop()


if __name__ == "__main__":
    main()
