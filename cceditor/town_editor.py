"""
Town editor - a GUI for Pickup Express town configs (.tcfg).

    python -m cceditor.town_editor [file.tcfg] [--game <game folder>] [--screenshot out.png]

Left:   all town configs found in the game folder (and any file opened with File > Open).
Centre: the minimap of the town (Images-<mode>/map.gti) with the level geometry and the spawn / pickup / drop /
        fuel points of the .ccf next to the config drawn on top, placed exactly as the game places them.
        Drag with the left mouse button to move the overlay (map offset), use the mouse wheel to resize it
        (map scale; hold Shift for X only, Ctrl for Y only).
Right:  every field of the config.  Hover a field for a description and the range used by the shipped towns.

The game folder is taken from --game, the PICKUP_GAME environment variable, the folder of the opened file or
the default install locations, in that order.
"""
from __future__ import annotations
import glob, os, sys, threading
from typing import Dict, List, Optional, Tuple

import numpy as np
import wx
import wx.propgrid as pg

from ccformats import ccf, gti, model
from ccformats.tcfg import TownConfig

TITLE = "Pickup Express - Town Editor"
GAME_DIRS = [r"C:\Program Files\Addgames\Pickup Express", r"C:\Program Files (x86)\Addgames\Pickup Express",
             os.path.join(os.path.expanduser("~"), "Documents", "Addgames", "Pickup Express")]
MODES = ("express", "stunt", "bomb")
MAX_CLOUD = 60000                     # level vertices drawn on the minimap
POINT_COLORS = {"spawn": (60, 220, 80), "pickup": (255, 220, 40), "drop": (255, 90, 60), "fuel": (70, 170, 255)}

# name, label, kind, tooltip
FIELDS = [
    ("Game modes", [
        ("available_express", "Available in Express", "bool", "Town is listed when the game mode is Express."),
        ("available_stunt", "Available in Stunt", "bool", "Town is listed in the game mode that uses the *-stunt files."),
        ("available_bomb", "Available in Bomb", "bool", "Town is listed in the game mode that uses the *-bomb files."),
    ]),
    ("Minimap", [
        ("scale_x", "Scale X", "float", "Map pixels per world unit, horizontally."),
        ("scale_y", "Scale Y", "float", "Map pixels per world unit, vertically."),
        ("offset_x", "Offset X", "float", "Map pixel of the world origin, horizontally."),
        ("offset_y", "Offset Y", "float", "Map pixel of the world origin, vertically."),
        ("map_flip_x", "Flip X", "bool", "Mirror the map horizontally (negates scale X)."),
        ("map_flip_y", "Flip Y", "bool", "Mirror the map vertically (negates scale Y)."),
        ("map_swap_axes", "Swap axes", "bool", "Off: map X follows world Z and map Y follows world X.\nOn: map X follows world X and map Y follows world Z."),
    ]),
    ("World", [
        ("view_distance", "View distance", "float", "Far clipping distance of the camera, in world units."),
        ("fuel_price", "Fuel price", "float", "Money charged per unit of fuel at gas stations (inferred from the refuel code)."),
        ("music_track", "CD music track", "int", "CD audio track played in this town; 0 plays nothing."),
    ]),
    ("Info", [
        ("description", "Description", "str", "Free text, at most 63 characters (Latin-1)."),
    ]),
]
KINDS = {name: kind for _, rows in FIELDS for name, _, kind, _ in rows}


def get_field(c: TownConfig, name: str):
    if name == "scale_x": return c.map_scale[0]
    if name == "scale_y": return c.map_scale[1]
    if name == "offset_x": return c.map_offset[0]
    if name == "offset_y": return c.map_offset[1]
    return getattr(c, name)


def set_field(c: TownConfig, name: str, value):
    kind = KINDS[name]
    if kind == "bool": value = 1 if value else 0
    elif kind == "int": value = max(-2 ** 31, min(2 ** 31 - 1, int(value)))
    elif kind == "float": value = float(value)
    else: value = str(value).encode("latin-1", "replace")[:63].decode("latin-1")
    if name == "scale_x": c.map_scale = (value, c.map_scale[1])
    elif name == "scale_y": c.map_scale = (c.map_scale[0], value)
    elif name == "offset_x": c.map_offset = (value, c.map_offset[1])
    elif name == "offset_y": c.map_offset = (c.map_offset[0], value)
    else: setattr(c, name, value)


def find_game_dir(path: Optional[str]) -> Optional[str]:
    """The folder that holds Pickup.exe / Towns, searched upwards from `path`, then in the usual places."""
    cur = os.path.dirname(os.path.abspath(path)) if path else None
    for _ in range(4):
        if not cur: break
        if os.path.isdir(os.path.join(cur, "Towns")): return cur
        cur = os.path.dirname(cur)
    for d in [os.environ.get("PICKUP_GAME")] + GAME_DIRS:
        if d and os.path.isdir(os.path.join(d, "Towns")): return d
    return None


def mode_of(path: str) -> Optional[str]:
    """'downtown-express.tcfg' -> 'express'."""
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    tail = stem.rsplit("-", 1)[-1]
    return tail if tail in MODES else None


def load_level(ccf_path: str):
    """World (x, z) of the level vertices (subsampled) and the gameplay points of a .ccf."""
    ir = model.scene_to_ir(ccf.Scene.load(ccf_path))
    chunks = []
    for o in ir.objects:
        if o.backdrop or o.portal or not o.mesh.verts: continue
        v = np.asarray(o.mesh.verts, dtype=np.float64) * o.scale @ np.asarray(o.matrix, dtype=np.float64) + np.asarray(o.pos)
        chunks.append(v[:, (0, 2)])
    cloud = np.concatenate(chunks) if chunks else np.zeros((0, 2))
    if len(cloud) > MAX_CLOUD: cloud = cloud[::len(cloud) // MAX_CLOUD + 1]
    points = [(p.kind, p.pos[0], p.pos[2]) for p in ir.points if p.kind in POINT_COLORS]
    return cloud, points


# ----------------------------------------------------------------------------------------------
class MapPanel(wx.Panel):
    """The minimap image with the level drawn on top through the config's map transform."""

    def __init__(self, parent, frame: "TownFrame"):
        super().__init__(parent, style=wx.FULL_REPAINT_ON_RESIZE)
        self.frame = frame
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.image: Optional[wx.Image] = None
        self.cloud = np.zeros((0, 2)); self.points: List[Tuple[str, float, float]] = []
        self.note = ""
        self.drag: Optional[Tuple[wx.Point, Tuple[float, float]]] = None
        self.Bind(wx.EVT_PAINT, self.on_paint)
        self.Bind(wx.EVT_LEFT_DOWN, self.on_down); self.Bind(wx.EVT_LEFT_UP, self.on_up)
        self.Bind(wx.EVT_MOTION, self.on_move); self.Bind(wx.EVT_MOUSEWHEEL, self.on_wheel)
        self.Bind(wx.EVT_MOUSE_CAPTURE_LOST, lambda e: setattr(self, "drag", None))

    def map_size(self) -> Tuple[int, int]:
        return (self.image.GetWidth(), self.image.GetHeight()) if self.image else (256, 128)

    def layout(self, size) -> Tuple[float, float, float]:
        """Zoom factor and top-left corner of the map image inside a panel of `size`."""
        w, h = self.map_size()
        k = max(0.5, min((size[0] - 40) / w, (size[1] - 60) / h))
        return k, (size[0] - w * k) / 2, (size[1] - h * k) / 2

    def render(self, dc: wx.DC, size):
        dc.SetBackground(wx.Brush(wx.Colour(40, 42, 48))); dc.Clear()
        k, x0, y0 = self.layout(size)
        w, h = self.map_size()
        if self.image:
            dc.DrawBitmap(wx.Bitmap(self.image.Scale(max(1, int(w * k)), max(1, int(h * k)), wx.IMAGE_QUALITY_NEAREST)), int(x0), int(y0), True)
        dc.SetPen(wx.Pen(wx.Colour(150, 150, 160))); dc.SetBrush(wx.TRANSPARENT_BRUSH)
        dc.DrawRectangle(int(x0) - 1, int(y0) - 1, int(w * k) + 2, int(h * k) + 2)
        cfg = self.frame.cfg
        if cfg is not None:
            if len(self.cloud):
                px, py = cfg.map_point(self.cloud[:, 0], self.cloud[:, 1])
                pts = np.stack([px * k + x0, py * k + y0], axis=1)
                ok = (pts[:, 0] >= 0) & (pts[:, 0] < size[0]) & (pts[:, 1] >= 0) & (pts[:, 1] < size[1])
                dc.SetPen(wx.Pen(wx.Colour(0, 255, 255)))
                dc.DrawPointList(np.unique(pts[ok].astype(np.int32), axis=0).tolist())
            for kind, x, z in self.points:
                px, py = cfg.map_point(x, z)
                dc.SetPen(wx.Pen(wx.BLACK)); dc.SetBrush(wx.Brush(wx.Colour(*POINT_COLORS[kind])))
                dc.DrawCircle(int(px * k + x0), int(py * k + y0), 4)
            ox, oy = cfg.map_offset[0] * k + x0, cfg.map_offset[1] * k + y0      # world origin
            dc.SetPen(wx.Pen(wx.WHITE, 1)); dc.DrawLine(int(ox) - 7, int(oy), int(ox) + 8, int(oy)); dc.DrawLine(int(ox), int(oy) - 7, int(ox), int(oy) + 8)
        dc.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        x = 8
        for kind, col in POINT_COLORS.items():
            dc.SetPen(wx.Pen(wx.BLACK)); dc.SetBrush(wx.Brush(wx.Colour(*col))); dc.DrawCircle(x + 5, 14, 4)
            dc.SetTextForeground(wx.Colour(220, 220, 220)); dc.DrawText(kind, x + 13, 6); x += 24 + dc.GetTextExtent(kind)[0]
        dc.DrawText("+ world origin", x, 6)
        if self.note: dc.DrawText(self.note, 8, size[1] - 22)

    def on_paint(self, e):
        self.render(wx.AutoBufferedPaintDC(self), self.GetClientSize())

    # dragging moves the world origin (offset); the wheel scales around the cursor
    def on_down(self, e):
        if self.frame.cfg is None: return
        self.drag = (e.GetPosition(), self.frame.cfg.map_offset); self.frame.begin_edit()
        self.CaptureMouse()

    def on_move(self, e):
        if not (self.drag and e.Dragging() and e.LeftIsDown()): return
        k, _, _ = self.layout(self.GetClientSize())
        d = e.GetPosition() - self.drag[0]
        self.frame.cfg.map_offset = (round(self.drag[1][0] + d.x / k, 2), round(self.drag[1][1] + d.y / k, 2))
        self.frame.live_update()

    def on_up(self, e):
        if self.HasCapture(): self.ReleaseMouse()
        if self.drag: self.drag = None; self.frame.end_edit()

    def on_wheel(self, e):
        c = self.frame.cfg
        if c is None or self.drag: return
        f = 1.02 ** (e.GetWheelRotation() / max(1, e.GetWheelDelta()))
        fx = 1.0 if e.ControlDown() else f; fy = 1.0 if e.ShiftDown() else f
        k, x0, y0 = self.layout(self.GetClientSize())
        mx, my = (e.GetX() - x0) / k, (e.GetY() - y0) / k               # cursor in map pixels stays put
        self.frame.begin_edit()
        c.map_scale = (c.map_scale[0] * fx, c.map_scale[1] * fy)
        c.map_offset = (mx - (mx - c.map_offset[0]) * fx, my - (my - c.map_offset[1]) * fy)
        self.frame.live_update(); self.frame.end_edit()


# ----------------------------------------------------------------------------------------------
class TownFrame(wx.Frame):
    def __init__(self, path: Optional[str] = None, game: Optional[str] = None):
        super().__init__(None, title=TITLE, size=(1300, 800))
        self.cfg: Optional[TownConfig] = None
        self.orig: Optional[TownConfig] = None
        self.path: Optional[str] = None
        self.dirty = False
        self.undo: List[bytes] = []; self.redo: List[bytes] = []
        self._before: Optional[bytes] = None
        self.library: Dict[str, TownConfig] = {}
        self.files: List[str] = []
        self.levels: Dict[str, tuple] = {}          # .ccf path -> (cloud, points), filled by a worker thread
        self._loading = False

        self.CreateStatusBar()
        self.build_menu()
        split = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)
        left = wx.Panel(split)
        ls = wx.BoxSizer(wx.VERTICAL)
        self.listbox = wx.ListBox(left, style=wx.LB_SINGLE)
        ls.Add(wx.StaticText(left, label="Town configs"), 0, wx.ALL, 4)
        ls.Add(self.listbox, 1, wx.EXPAND | wx.ALL, 2)
        left.SetSizer(ls)
        split2 = wx.SplitterWindow(split, style=wx.SP_LIVE_UPDATE)
        self.map = MapPanel(split2, self)
        self.grid = pg.PropertyGrid(split2, style=pg.PG_SPLITTER_AUTO_CENTER | pg.PG_TOOLTIPS)
        split.SplitVertically(left, split2, 230)
        split2.SplitVertically(self.map, self.grid, -380)
        split2.SetSashGravity(1.0)
        split.SetMinimumPaneSize(120); split2.SetMinimumPaneSize(200)
        # negative sash positions are unreliable before the frame has its final size
        def place_sash(e):
            e.Skip()
            if self.IsShown() and split2.GetClientSize().width > 600:
                split2.Unbind(wx.EVT_SIZE, handler=place_sash)
                wx.CallAfter(lambda: self and split2.SetSashPosition(split2.GetClientSize().width - 380))
        split2.Bind(wx.EVT_SIZE, place_sash)
        self.build_grid()

        self.listbox.Bind(wx.EVT_LISTBOX, self.on_pick)
        self.grid.Bind(pg.EVT_PG_CHANGED, self.on_changed)
        self.Bind(wx.EVT_CLOSE, self.on_close)

        self.scan(game or find_game_dir(path))
        if path: self.open_file(path)
        elif self.files: self.open_file(self.files[0])

    # ------------------------------------------------------------------ menus
    def build_menu(self):
        mb = wx.MenuBar()
        for title, items in (
            ("&File", [("New...\tCtrl+N", self.on_new), ("Open...\tCtrl+O", self.on_open), ("Save\tCtrl+S", self.on_save),
                       ("Save As...\tCtrl+Shift+S", self.on_save_as), ("Revert", self.on_revert), (None, None),
                       ("Select game folder...", self.on_game_dir), (None, None), ("Quit\tCtrl+Q", lambda e: self.Close())]),
            ("&Edit", [("Undo\tCtrl+Z", self.on_undo), ("Redo\tCtrl+Y", self.on_redo), (None, None),
                       ("Fit level to minimap", self.on_fit)]),
        ):
            m = wx.Menu()
            for label, fn in items:
                if label is None: m.AppendSeparator(); continue
                self.Bind(wx.EVT_MENU, fn, m.Append(wx.ID_ANY, label))
            mb.Append(m, title)
        self.SetMenuBar(mb)

    # ------------------------------------------------------------------ grid
    def build_grid(self):
        g = self.grid
        make = {"bool": lambda l, n: pg.BoolProperty(l, n, False), "int": lambda l, n: pg.IntProperty(l, n, 0),
                "float": lambda l, n: pg.FloatProperty(l, n, 0.0), "str": lambda l, n: pg.StringProperty(l, n, "")}
        for cat, rows in FIELDS:
            g.Append(pg.PropertyCategory(cat))
            for name, label, kind, _ in rows:
                g.Append(make[kind](label, name))
                if kind == "bool": g.SetPropertyAttribute(name, "UseCheckbox", True)
                if kind == "str": g.SetPropertyAttribute(name, "MaxLength", 63)
        g.Enable(False)

    def fill_grid(self):
        self._loading = True
        for name, kind in KINDS.items():
            v = get_field(self.cfg, name)
            self.grid.SetPropertyValue(name, bool(v) if kind == "bool" else v)
        self._loading = False
        self.refresh_marks()

    def refresh_marks(self):
        for _, rows in FIELDS:
            for name, _, kind, tip in rows:
                if kind in ("int", "float") and self.library:
                    vals = [get_field(c, name) for c in self.library.values()]
                    tip += f"\nshipped range: {min(vals):g} .. {max(vals):g}"
                p = self.grid.GetProperty(name)
                self.grid.SetPropertyHelpString(p, tip)
                changed = self.orig is not None and get_field(self.cfg, name) != get_field(self.orig, name)
                p.SetBackgroundColour(wx.Colour(255, 240, 180) if changed else wx.NullColour)
        self.grid.Refresh()

    # ------------------------------------------------------------------ editing
    def snapshot(self) -> bytes:
        return self.cfg.to_bytes()

    def restore(self, data: bytes):
        keep = self.cfg.desc_raw
        self.cfg = TownConfig.from_bytes(data); self.cfg.desc_raw = keep
        self.fill_grid(); self.map.Refresh(False)

    def begin_edit(self):
        self._before = self.snapshot()

    def end_edit(self):
        """Close an edit started with begin_edit(): one undo step if anything changed."""
        if self._before is not None and self.snapshot() != self._before:
            self.undo.append(self._before); self.redo.clear(); self.set_dirty(True)
        self._before = None

    def live_update(self):
        self.fill_grid(); self.map.Refresh(False)

    def on_changed(self, e):
        if self._loading or self.cfg is None: return
        p = e.GetProperty()
        self.begin_edit()
        set_field(self.cfg, p.GetName(), p.GetValue())
        self.end_edit(); self.live_update()

    def on_undo(self, e):
        if self.undo and self.cfg: self.redo.append(self.snapshot()); self.restore(self.undo.pop()); self.set_dirty(True)

    def on_redo(self, e):
        if self.redo and self.cfg: self.undo.append(self.snapshot()); self.restore(self.redo.pop()); self.set_dirty(True)

    def on_fit(self, e):
        """Choose scale and offset so the whole level is centred in the minimap image (keeps flip / swap)."""
        c = self.cfg
        if c is None or not len(self.map.cloud):
            wx.MessageBox("No level geometry is loaded for this town config.", TITLE, wx.ICON_INFORMATION); return
        w, h = self.map.map_size()
        ab = self.map.cloud if c.map_swap_axes else self.map.cloud[:, ::-1]      # columns: map x source, map y source
        lo, hi = ab.min(axis=0), ab.max(axis=0)
        span = np.maximum(hi - lo, 1e-6); mid = (lo + hi) / 2
        s = float(min(w / span[0], h / span[1]) * 0.95)
        sx = -s if c.map_flip_x else s; sy = -s if c.map_flip_y else s
        self.begin_edit()
        c.map_scale = (round(s, 5), round(s, 5))
        c.map_offset = (round(float(w / 2 - mid[0] * sx), 2), round(float(h / 2 - mid[1] * sy), 2))
        self.end_edit(); self.live_update()

    # ------------------------------------------------------------------ files
    def scan(self, game: Optional[str]):
        self.game = game
        self.files = sorted(glob.glob(os.path.join(game, "Towns", "*", "*.tcfg")), key=str.lower) if game else []
        self.library = {}
        for f in self.files:
            try: self.library[f] = TownConfig.load(f)
            except Exception: pass
        self.refresh_list()

    def refresh_list(self):
        self.listbox.Set(["%s  (%s)" % (os.path.splitext(os.path.basename(f))[0], os.path.basename(os.path.dirname(f))) for f in self.files])
        if self.path in self.files: self.listbox.SetSelection(self.files.index(self.path))

    def set_dirty(self, d: bool):
        self.dirty = d
        self.SetTitle("%s - %s%s" % (TITLE, os.path.basename(self.path or "(new)"), " *" if d else ""))

    def confirm_discard(self) -> bool:
        if not self.dirty: return True
        return wx.MessageBox("Discard unsaved changes?", TITLE, wx.YES_NO | wx.ICON_QUESTION) == wx.YES

    def on_pick(self, e):
        i = self.listbox.GetSelection()
        if i >= 0 and self.files[i] != self.path and self.confirm_discard(): self.open_file(self.files[i])
        elif self.path in self.files: self.listbox.SetSelection(self.files.index(self.path))

    def show_config(self, cfg: TownConfig, path: Optional[str], dirty: bool):
        self.cfg = cfg
        self.orig = TownConfig.from_bytes(cfg.to_bytes()); self.orig.desc_raw = cfg.desc_raw
        self.path = path; self.undo.clear(); self.redo.clear()
        if path and path not in self.files: self.files.append(path)
        self.refresh_list()
        self.grid.Enable(True); self.fill_grid()
        self.load_preview()
        self.set_dirty(dirty)
        self.SetStatusText(path or "new town config - use File > Save As")

    def open_file(self, path: str):
        path = os.path.abspath(path)
        try:
            cfg = TownConfig.load(path)
        except Exception as ex:
            wx.MessageBox(f"Could not read {path}:\n{ex}", TITLE, wx.ICON_ERROR); return
        self.show_config(cfg, path, False)

    def load_preview(self):
        """Minimap image and level overlay for the current file; the level is parsed in the background."""
        m = self.map
        m.image = None; m.cloud = np.zeros((0, 2)); m.points = []; m.note = ""
        if self.path:
            mode = mode_of(self.path)
            img = os.path.join(os.path.dirname(self.path), "Images-%s" % mode, "map.gti") if mode else None
            if img and os.path.isfile(img):
                try:
                    rgba = np.ascontiguousarray(gti.GtiFile.load(img).to_rgba_np())
                    m.image = wx.Image(rgba.shape[1], rgba.shape[0], rgba[:, :, :3].tobytes(), rgba[:, :, 3].tobytes())
                except Exception as ex:
                    m.note = f"map.gti could not be read: {ex}"
            else:
                m.note = "no Images-<mode>/map.gti for this file"
            level = os.path.splitext(self.path)[0] + ".ccf"
            if level in self.levels:
                m.cloud, m.points = self.levels[level]
            elif os.path.isfile(level):
                m.note = "loading level geometry..."
                threading.Thread(target=self._load_level, args=(level,), daemon=True).start()
            else:
                m.note = (m.note + "; " if m.note else "") + "no %s next to this file, so no level overlay" % os.path.basename(level)
        m.Refresh(False)

    def _load_level(self, level: str):
        try: result = load_level(level)
        except Exception as ex: result = ex
        wx.CallAfter(self._level_loaded, level, result)

    def _level_loaded(self, level: str, result):
        if not self:            # window closed while the worker was running
            return
        if not isinstance(result, Exception): self.levels[level] = result
        if self.path and os.path.splitext(self.path)[0] + ".ccf" == level:
            if isinstance(result, Exception): self.map.note = f"level could not be read: {result}"
            else: self.map.cloud, self.map.points = result; self.map.note = ""
            self.map.Refresh(False)

    def on_new(self, e):
        if self.confirm_discard(): self.show_config(TownConfig(), None, True)

    def on_open(self, e):
        if not self.confirm_discard(): return
        with wx.FileDialog(self, "Open town config", wildcard="Town config (*.tcfg)|*.tcfg", style=wx.FD_OPEN) as d:
            if d.ShowModal() == wx.ID_OK: self.open_file(d.GetPath())

    def on_game_dir(self, e):
        with wx.DirDialog(self, "Select the Pickup Express folder (the one with Towns inside)", self.game or "") as d:
            if d.ShowModal() != wx.ID_OK: return
            if not os.path.isdir(os.path.join(d.GetPath(), "Towns")):
                wx.MessageBox("That folder has no Towns folder.", TITLE, wx.ICON_WARNING); return
            keep = self.path
            self.scan(d.GetPath())
            if keep and keep not in self.files: self.files.append(keep); self.refresh_list()

    def write(self, path: str) -> bool:
        try:
            self.cfg.save(path)
        except PermissionError:
            wx.MessageBox("That location is write-protected.\nUse File > Save As to save the config elsewhere and copy it in with administrator rights.", TITLE, wx.ICON_WARNING)
            return False
        except Exception as ex:
            wx.MessageBox(f"Save failed: {ex}", TITLE, wx.ICON_ERROR); return False
        path = os.path.abspath(path)
        moved = path != self.path
        self.path = path; self.orig = TownConfig.from_bytes(self.cfg.to_bytes()); self.orig.desc_raw = self.cfg.desc_raw
        self.library[path] = self.orig
        if path not in self.files: self.files.append(path)
        self.refresh_list(); self.set_dirty(False); self.refresh_marks()
        if moved: self.load_preview()
        self.SetStatusText("saved " + path)
        return True

    def on_save(self, e):
        if not self.cfg: return
        if not self.path or not self.write(self.path): self.on_save_as(e)

    def on_save_as(self, e):
        if not self.cfg: return
        with wx.FileDialog(self, "Save town config", defaultDir=os.path.dirname(self.path) if self.path else "",
                           defaultFile=os.path.basename(self.path or "town-express.tcfg"), wildcard="Town config (*.tcfg)|*.tcfg",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as d:
            if d.ShowModal() == wx.ID_OK: self.write(d.GetPath())

    def on_revert(self, e):
        if self.path and self.confirm_discard(): self.open_file(self.path)

    def on_close(self, e):
        if e.CanVeto() and not self.confirm_discard(): e.Veto(); return
        self.Destroy()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    shot = game = path = None
    for flag in ("--screenshot", "--game"):
        if flag in argv:
            i = argv.index(flag)
            if flag == "--screenshot": shot = argv[i + 1]
            else: game = argv[i + 1]
            del argv[i:i + 2]
    if argv: path = argv[0]
    app = wx.App(False)
    fr = TownFrame(path, game); fr.Show()
    if shot:
        def grab():
            size = fr.map.GetClientSize()
            bmp = wx.Bitmap(size.width, size.height)
            dc = wx.MemoryDC(bmp); fr.map.render(dc, size); dc.SelectObject(wx.NullBitmap)
            bmp.SaveFile(shot, wx.BITMAP_TYPE_PNG)          # saves the map view only
            fr.dirty = False; fr.Close(True)
        wx.CallLater(6000, grab)
    app.MainLoop()


if __name__ == "__main__":
    main()
