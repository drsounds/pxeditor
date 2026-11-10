"""Pickup Express level editor (wxPython + OpenGL).

    python -m cceditor [file.ccf] [--screenshot out.png]
"""
from __future__ import annotations
import copy, os, sys, math
import numpy as np
import wx
import wx.propgrid as pg

from ccformats import ccf, model, objio, tcfg as tcfgmod
from ccformats.constants import MATERIAL_TYPES, MATERIAL_TYPE_IDS, PACKAGES, LIGHT_TYPES, NULL_TYPES, MATERIAL_MODE
from ccformats.model import IRObject, IRPoint, IRLight, IRMaterial
from .doc import Document, euler_to_matrix, matrix_to_euler
from .glview import Viewport, TOOL_SELECT, TOOL_MOVE, TOOL_ROTATE, TOOL_SCALE, from_gl

DEFAULT_GAME = r"C:\Program Files\Addgames\Pickup Express"
POINT_KINDS = ["null", "spawn", "pickup", "drop", "fuel", "ai"]
SURFACE_CHOICES = [(0, "None")] + [(k, v) for k, v in MATERIAL_TYPES.items() if k != 0]


class Frame(wx.Frame):
    def __init__(self, path=None):
        super().__init__(None, title="Pickup Express Editor", size=(1500, 900))
        self.doc = Document()
        self.doc.listeners.append(self.on_doc)
        self._sync = False
        self._tree_sig = None
        self.CreateStatusBar(3)
        self.build_ui()
        self.build_menu()
        self.build_toolbar()
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.update_title()
        if path: wx.CallAfter(self.open_path, path)

    # ------------------------------------------------------------------ UI
    def build_ui(self):
        self.split = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)
        left = wx.SplitterWindow(self.split, style=wx.SP_LIVE_UPDATE)
        # tree + rooms
        tp = wx.Panel(left)
        self.search = wx.SearchCtrl(tp); self.search.Bind(wx.EVT_TEXT, lambda e: self.rebuild_tree(force=True))
        self.tree = wx.TreeCtrl(tp, style=wx.TR_HAS_BUTTONS | wx.TR_MULTIPLE | wx.TR_HIDE_ROOT | wx.TR_LINES_AT_ROOT)
        s = wx.BoxSizer(wx.VERTICAL); s.Add(self.search, 0, wx.EXPAND | wx.ALL, 2); s.Add(self.tree, 1, wx.EXPAND); tp.SetSizer(s)
        self.rooms_list = wx.CheckListBox(left)
        self.rooms_list.Bind(wx.EVT_CHECKLISTBOX, self.on_room_toggle)
        left.SplitHorizontally(tp, self.rooms_list, -170); left.SetSashGravity(1.0)
        self.tree.Bind(wx.EVT_TREE_SEL_CHANGED, self.on_tree_sel)
        self.tree.Bind(wx.EVT_TREE_ITEM_ACTIVATED, lambda e: self.view.frame_selection())
        # viewport + notebook
        right = wx.SplitterWindow(self.split, style=wx.SP_LIVE_UPDATE)
        self.view = Viewport(right, self.doc, self.GetStatusBar())
        self.nb = wx.Notebook(right)
        self.props = pg.PropertyGrid(self.nb, style=pg.PG_SPLITTER_AUTO_CENTER | pg.PG_BOLD_MODIFIED)
        self.props.Bind(pg.EVT_PG_CHANGED, self.on_prop_changed)
        self.nb.AddPage(self.props, "Properties")
        mp = wx.Panel(self.nb)
        self.mat_list = wx.ListBox(mp, style=wx.LB_SINGLE)
        self.mat_grid = pg.PropertyGrid(mp, style=pg.PG_SPLITTER_AUTO_CENTER | pg.PG_BOLD_MODIFIED)
        ms = wx.BoxSizer(wx.VERTICAL); ms.Add(self.mat_list, 1, wx.EXPAND); ms.Add(self.mat_grid, 2, wx.EXPAND); mp.SetSizer(ms)
        self.mat_list.Bind(wx.EVT_LISTBOX, self.on_mat_select)
        self.mat_grid.Bind(pg.EVT_PG_CHANGED, self.on_mat_changed)
        self.nb.AddPage(mp, "Materials")
        tp2 = wx.Panel(self.nb)
        self.town_grid = pg.PropertyGrid(tp2, style=pg.PG_SPLITTER_AUTO_CENTER)
        self.town_grid.Bind(pg.EVT_PG_CHANGED, self.on_town_changed)
        b = wx.Button(tp2, label="Create town config (.tcfg)"); b.Bind(wx.EVT_BUTTON, self.on_make_tcfg)
        ts = wx.BoxSizer(wx.VERTICAL); ts.Add(b, 0, wx.EXPAND | wx.ALL, 3); ts.Add(self.town_grid, 1, wx.EXPAND); tp2.SetSizer(ts)
        self.nb.AddPage(tp2, "Town")
        right.SplitVertically(self.view, self.nb, -330); right.SetSashGravity(1.0)
        self.split.SplitVertically(left, right, 260)
        self.view.on_pick_info = None

    def build_menu(self):
        mb = wx.MenuBar()
        f = wx.Menu()
        self._m(f, "&New\tCtrl+N", self.on_new); self._m(f, "&Open CCF...\tCtrl+O", self.on_open)
        self._m(f, "Open &town from game folder...", self.on_open_town)
        f.AppendSeparator()
        self._m(f, "&Save\tCtrl+S", self.on_save); self._m(f, "Save &As...\tCtrl+Shift+S", self.on_save_as)
        self.chk_bsp = f.AppendCheckItem(wx.ID_ANY, "Rebuild collision BSP on save"); self.chk_bsp.Check(True)
        f.AppendSeparator()
        self._m(f, "&Import OBJ (merge)...", self.on_import_obj); self._m(f, "Import OBJ (replace scene)...", self.on_import_obj_replace)
        self._m(f, "&Export OBJ...", self.on_export_obj)
        f.AppendSeparator(); self._m(f, "E&xit", lambda e: self.Close())
        mb.Append(f, "&File")
        e = wx.Menu()
        self._m(e, "&Undo\tCtrl+Z", lambda ev: self.doc.undo()); self._m(e, "&Redo\tCtrl+Y", lambda ev: self.doc.redo()); e.AppendSeparator()
        self._m(e, "&Duplicate\tCtrl+D", lambda ev: self.doc.duplicate(self.doc.selection))
        self._m(e, "&Delete\tDel", lambda ev: self.doc.delete_nodes(self.doc.selection))
        self._m(e, "Select &all\tCtrl+A", lambda ev: self.doc.set_selection(self.doc.all_nodes()))
        self._m(e, "Select same &room", self.on_select_room)
        mb.Append(e, "&Edit")
        a = wx.Menu()
        for kind in POINT_KINDS:
            self._m(a, f"Add {kind} point", lambda ev, k=kind: self.add_point(k))
        self._m(a, "Add light", lambda ev: self.add_light())
        a.AppendSeparator()
        self._m(a, "Add box (collision)", lambda ev: self.add_box(True)); self._m(a, "Add box (decoration)", lambda ev: self.add_box(False))
        self._m(a, "Add water plane", lambda ev: self.add_water())
        mb.Append(a, "&Add")
        v = wx.Menu()
        self._m(v, "Textured\t1", lambda ev: self.set_mode(0)); self._m(v, "Collision map (collision / water / decoration)\t2", lambda ev: self.set_mode(1))
        self._m(v, "Wireframe\t3", lambda ev: self.set_mode(2)); v.AppendSeparator()
        self.v_items = {}
        for key, label in (("grid", "Grid"), ("points", "Game points"), ("lights", "Lights"), ("ai", "AI links"), ("selection", "Selection outline"), ("portals", "Portal polygons")):
            it = v.AppendCheckItem(wx.ID_ANY, label); it.Check(key != "portals"); self.v_items[key] = it
            self.Bind(wx.EVT_MENU, lambda ev, k=key, i=it: self.toggle_show(k, i), it)
        v.AppendSeparator(); self._m(v, "Frame selection\tF", lambda ev: self.view.frame_selection()); self._m(v, "Frame all\tHome", lambda ev: self.view.frame_all())
        mb.Append(v, "&View")
        t = wx.Menu()
        self._m(t, "&Validate level...", self.on_validate); self._m(t, "Rebuild BSP now (test)", self.on_bsp_now)
        self._m(t, "Re&load textures", lambda ev: (self.view.tex.set_dirs(self.doc.texture_dirs), self.view.Refresh()))
        mb.Append(t, "&Tools")
        h = wx.Menu(); self._m(h, "Controls", self.on_help); self._m(h, "About", lambda ev: wx.MessageBox("Pickup Express level editor\nFormats recovered from cc.dll", "About"))
        mb.Append(h, "&Help")
        self.SetMenuBar(mb)

    def _m(self, menu, label, fn):
        it = menu.Append(wx.ID_ANY, label); self.Bind(wx.EVT_MENU, fn, it); return it

    def build_toolbar(self):
        tb = self.CreateToolBar(wx.TB_HORIZONTAL | wx.TB_TEXT | wx.TB_NOICONS)
        self.tool_ids = {}
        for t, label in ((TOOL_SELECT, "Select (Q)"), (TOOL_MOVE, "Move (G)"), (TOOL_ROTATE, "Rotate (R)"), (TOOL_SCALE, "Scale (S)")):
            i = wx.NewIdRef(); tb.AddRadioTool(i, label, wx.Bitmap(1, 1), shortHelp=label); self.tool_ids[t] = i
            self.Bind(wx.EVT_TOOL, lambda ev, tt=t: self.set_tool(tt), id=i)
        tb.AddSeparator()
        for m, label in ((0, "Textured"), (1, "Collision"), (2, "Wire")):
            i = wx.NewIdRef(); tb.AddRadioTool(i, label, wx.Bitmap(1, 1)); self.Bind(wx.EVT_TOOL, lambda ev, mm=m: self.set_mode(mm), id=i)
        tb.AddSeparator()
        tb.AddControl(wx.StaticText(tb, label=" Snap "))
        self.snap_choice = wx.Choice(tb, choices=["off", "0.25", "0.5", "1", "2", "5", "10"]); self.snap_choice.SetSelection(0)
        self.snap_choice.Bind(wx.EVT_CHOICE, lambda e: setattr(self.view, "snap", [0, .25, .5, 1, 2, 5, 10][self.snap_choice.GetSelection()]))
        tb.AddControl(self.snap_choice)
        tb.Realize()
        self.toolbar = tb

    # ------------------------------------------------------------------ actions
    def update_title(self):
        n = os.path.basename(self.doc.path) if self.doc.path else "untitled"
        self.SetTitle(f"{n}{'*' if self.doc.dirty else ''} - Pickup Express Editor")

    def set_tool(self, t):
        self.view.tool = t; self.view.Refresh(False)

    def set_mode(self, m):
        self.view.mode = m; self.view.dirty_cache = True; self.view.Refresh(False)

    def toggle_show(self, key, item):
        self.view.show[key] = item.IsChecked(); self.view.dirty_cache = True; self.view.Refresh(False)

    def confirm_discard(self) -> bool:
        if not self.doc.dirty: return True
        r = wx.MessageBox("Discard unsaved changes?", "Unsaved changes", wx.YES_NO | wx.ICON_QUESTION)
        return r == wx.YES

    def on_close(self, e):
        if e.CanVeto() and not self.confirm_discard(): e.Veto(); return
        e.Skip()

    def on_new(self, e):
        if not self.confirm_discard(): return
        self.doc.load_ir(model.IRScene(), None); self.doc.ir.ensure_room("")
        self.doc.dirty = False; self.update_title()

    def on_open(self, e):
        if not self.confirm_discard(): return
        start = os.path.join(DEFAULT_GAME, "Towns") if os.path.isdir(DEFAULT_GAME) else ""
        with wx.FileDialog(self, "Open CCF", start, wildcard="CCF scenes (*.ccf)|*.ccf", style=wx.FD_OPEN) as d:
            if d.ShowModal() == wx.ID_OK: self.open_path(d.GetPath())

    def on_open_town(self, e):
        if not self.confirm_discard(): return
        root = DEFAULT_GAME
        with wx.DirDialog(self, "Pickup Express game folder", root) as d:
            if d.ShowModal() != wx.ID_OK: return
            root = d.GetPath()
        towns = []
        tdir = os.path.join(root, "Towns")
        for t in sorted(os.listdir(tdir)) if os.path.isdir(tdir) else []:
            for fn in sorted(os.listdir(os.path.join(tdir, t))):
                if fn.lower().endswith(".ccf") and fn.lower() != "backdrop.ccf": towns.append(os.path.join(tdir, t, fn))
        with wx.SingleChoiceDialog(self, "Level", "Open town", [os.path.relpath(t, tdir) for t in towns]) as d:
            if d.ShowModal() == wx.ID_OK: self.open_path(towns[d.GetSelection()])

    def open_path(self, path):
        with wx.BusyCursor():
            try:
                self.doc.load(path)
            except Exception as ex:
                wx.MessageBox(f"Could not load {path}\n{ex}", "Error", wx.ICON_ERROR); return
        self.SetStatusText(f"{path}: {len(self.doc.ir.objects)} objects, {len(self.doc.ir.points)} points, {len(self.doc.ir.lights)} lights", 0)

    def on_save(self, e):
        if not self.doc.path: return self.on_save_as(e)
        self.do_save(self.doc.path)

    def on_save_as(self, e):
        with wx.FileDialog(self, "Save CCF", wildcard="CCF scenes (*.ccf)|*.ccf", style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as d:
            if d.ShowModal() == wx.ID_OK: self.do_save(d.GetPath())

    def do_save(self, path):
        self.doc.rebuild_bsp_on_save = self.chk_bsp.IsChecked()
        dlg = wx.ProgressDialog("Saving", "Building scene...", maximum=100, parent=self, style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE)
        try:
            def prog(i, n, name): dlg.Update(int(100 * i / max(n, 1)), f"Object {i}/{n}")
            self.doc.save(path, progress=prog)
        except Exception as ex:
            import traceback; traceback.print_exc()
            wx.MessageBox(f"Save failed: {ex}", "Error", wx.ICON_ERROR)
        finally:
            dlg.Destroy()
        self.update_title()

    def on_import_obj(self, e, replace=False):
        with wx.FileDialog(self, "Import OBJ", wildcard="OBJ (*.obj)|*.obj", style=wx.FD_OPEN) as d:
            if d.ShowModal() != wx.ID_OK: return
            path = d.GetPath()
        with wx.BusyCursor():
            ir = objio.import_obj(path)
        if replace:
            if not self.confirm_discard(): return
            self.doc.load_ir(ir, self.doc.path)
            if not self.doc.texture_dirs:
                tdir = os.path.join(os.path.dirname(path), "textures")
                self.doc.texture_dirs = [tdir] if os.path.isdir(tdir) else []
        else:
            nodes = ir.objects + ir.points + ir.lights
            for n in nodes: n.key = self.doc.unique_key(n.key or n.name); n.parent = None
            for name, m in ir.materials.items(): self.doc.ir.materials.setdefault(name, m)
            doc = self.doc
            def do():
                doc.ir.objects.extend(ir.objects); doc.ir.points.extend(ir.points); doc.ir.lights.extend(ir.lights)
                for r in ir.rooms: doc.ir.ensure_room(r)
            def undo():
                for lst, mine in ((doc.ir.objects, ir.objects), (doc.ir.points, ir.points), (doc.ir.lights, ir.lights)):
                    for n in mine:
                        if n in lst: lst.remove(n)
            doc.push("Import OBJ", do, undo); doc.set_selection(nodes)

    def on_import_obj_replace(self, e): self.on_import_obj(e, True)

    def on_export_obj(self, e):
        with wx.FileDialog(self, "Export OBJ", wildcard="OBJ (*.obj)|*.obj", style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as d:
            if d.ShowModal() != wx.ID_OK: return
            path = d.GetPath()
        with wx.BusyCursor():
            st = objio.export_obj(self.doc.ir, path, self.doc.texture_dirs)
        wx.MessageBox(f"Exported {st}", "OBJ export")

    def on_help(self, e):
        wx.MessageBox("Viewport\n  Left click: select (Ctrl/Shift adds)   drag: move along ground (Move tool) or by axis handle\n"
                      "  Right/Middle drag: orbit    Shift+Right/Middle: pan    Wheel: zoom    Alt+Left: orbit\n"
                      "Keys: Q select, G move, R rotate (drag horizontally), S scale, F frame selection, Home frame all,\n"
                      "      1/2/3 textured/collision/wire, Del delete, Ctrl+D duplicate, Ctrl+Z/Y undo/redo, T top view\n\n"
                      "Names: -collision -water -concrete -tree -portal -backdrop (meshes), -spawn -pickup -drop -fuel -ai (points)", "Controls")

    # ---- add things
    def drop_pos(self):
        p = from_gl(self.view.target); return (p[0], 0.5, p[2])

    def current_room(self, pos=None):
        """Room of the selection, else of the object nearest to the drop position (rooms are spatial cells)."""
        sel = self.doc.selection
        if sel: return sel[0].room
        pos = pos or self.drop_pos()
        best = None; bd = float("inf")
        for o in self.doc.ir.objects:
            d = (o.pos[0] - pos[0]) ** 2 + (o.pos[2] - pos[2]) ** 2
            if d < bd: bd = d; best = o
        return best.room if best else ""

    def add_point(self, kind):
        base = {"null": "null", "ai": "ai"}.get(kind, kind)
        n = IRPoint(name=f"{base}{len([p for p in self.doc.ir.points if p.kind == kind]) + 1}", pos=self.drop_pos(), kind=kind, room=self.current_room())
        if kind in ("pickup", "drop"): n.packages_a = list(PACKAGES[:3])
        self.doc.add_node(n, f"Add {kind}")

    def add_light(self):
        self.doc.add_node(IRLight(name="light1", pos=(self.drop_pos()[0], 8.0, self.drop_pos()[2]), room=self.current_room()), "Add light")

    def _box_mesh(self, sx, sy, sz, matname):
        from ccformats.model import IRMesh
        m = IRMesh()
        x, y, z = sx / 2, sy / 2, sz / 2
        m.verts = [(-x, -y, -z), (x, -y, -z), (x, y, -z), (-x, y, -z), (-x, -y, z), (x, -y, z), (x, y, z), (-x, y, z)]
        quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (3, 7, 6, 2), (0, 4, 7, 3), (1, 2, 6, 5)]
        uv = ((0, 0), (0, 1), (1, 1), (1, 0))
        for q in quads:
            for tri, tuv in (((q[0], q[1], q[2]), (uv[0], uv[1], uv[2])), ((q[0], q[2], q[3]), (uv[0], uv[2], uv[3]))):
                m.faces.append(tri); m.face_mats.append(matname); m.face_uvs.append(tuv); m.face_colors.append(None)
        m.compute_normals()
        return m

    def add_box(self, collision):
        self.doc.ir.material("box")
        o = IRObject(name="box1", pos=self.drop_pos(), mesh=self._box_mesh(10, 5, 10, "box"), collision=collision, room=self.current_room())
        self.doc.add_node(o, "Add box")

    def add_water(self):
        mt = self.doc.ir.material("water")
        mt.mtype = MATERIAL_TYPE_IDS["water"]; mt.color_a = (0.3, 0.5, 1.0); mt.mode = 1
        p = self.drop_pos()
        o = IRObject(name="water1", pos=(p[0], 0.0, p[2]), mesh=self._box_mesh(60, 0.2, 60, "water"), collision=True, surface=MATERIAL_TYPE_IDS["water"], room=self.current_room())
        self.doc.add_node(o, "Add water")

    def on_select_room(self, e):
        if self.doc.selection:
            r = self.doc.selection[0].room
            self.doc.set_selection([n for n in self.doc.all_nodes() if n.room == r])

    # ---- tools
    def on_validate(self, e):
        msgs = []
        ir = self.doc.ir
        kinds = {}
        for p in ir.points: kinds.setdefault(p.kind, []).append(p)
        for k in ("spawn", "pickup", "drop", "fuel", "ai"):
            if k not in kinds: msgs.append(f"WARNING: no '{k}' points (game mode needs them)")
        names = {}
        for p in ir.points:
            if p.kind != "null": names.setdefault((p.kind, p.name.lower()), []).append(p)
        for (k, n), l in names.items():
            if len(l) > 1: msgs.append(f"Duplicate {k} point name '{n}' (the game warns and ignores duplicates)")
        for p in kinds.get("pickup", []) + kinds.get("drop", []):
            if not p.packages_a: msgs.append(f"{p.kind} '{p.name}' has no package list")
            for pk in p.packages_a + p.packages_b:
                if pk.lower() not in [x.lower() for x in PACKAGES]: msgs.append(f"{p.kind} '{p.name}': unknown package '{pk}'")
        have = {n.lower() for n in self.view.tex.names()}
        for m in ir.materials.values():
            if m.texture and have and m.texture.lower() not in have: msgs.append(f"Material '{m.name}': texture '{m.texture}.gti' not found")
        n_col = sum(1 for o in ir.objects if o.collision or o.surface or o.portal)
        msgs.append(f"{len(ir.objects)} objects ({n_col} collidable), {len(ir.points)} points, {len(ir.lights)} lights, {len(ir.rooms)} rooms")
        water = [o for o in ir.objects if o.surface == MATERIAL_TYPE_IDS["water"]]
        msgs.append(f"{len(water)} water objects (driving onto water respawns the vehicle)")
        wx.MessageBox("\n".join(msgs), "Validation")

    def on_bsp_now(self, e):
        import time
        t = time.time()
        with wx.BusyCursor():
            sc = model.ir_to_scene(self.doc.ir, build_bsp=True)
        wx.MessageBox("BSP + scene built in %.1fs" % (time.time() - t), "BSP")

    # ------------------------------------------------------------------ doc events
    def on_doc(self, what):
        if not self: return
        if what in ("loaded", "scene"):
            self.rebuild_tree(); self.rebuild_rooms(); self.rebuild_materials()
        if what in ("loaded",):
            self.rebuild_town()
        if what in ("selection", "loaded", "scene"):
            self.sync_tree_selection(); self.rebuild_props()
        if what == "tool": self.toolbar.ToggleTool(self.tool_ids[self.view.tool], True)
        self.update_title()
        if what == "scene" or what == "selection":
            self.SetStatusText(f"{len(self.doc.selection)} selected", 1)

    # ---- tree
    def node_label(self, n):
        if isinstance(n, IRObject): return n.full_name
        if isinstance(n, IRPoint): return f"{n.full_name}"
        return n.name

    def rebuild_tree(self, force=False):
        ir = self.doc.ir
        flt = self.search.GetValue().strip().lower()
        sig = (flt, tuple((id(n), n.name, n.room, getattr(n, "kind", ""), getattr(n, "collision", 0), getattr(n, "surface", 0)) for n in self.doc.all_nodes()))
        if sig == self._tree_sig and not force: return
        self._tree_sig = sig
        self._sync = True
        self.tree.Freeze(); self.tree.DeleteAllItems()
        root = self.tree.AddRoot("scene"); self.item_node = {}
        self.node_item = {}
        rooms = {}
        for o in ir.objects:
            if flt and flt not in o.full_name.lower(): continue
            rooms.setdefault(o.room, []).append(o)
        objs = self.tree.AppendItem(root, "Objects")
        for rn in sorted(rooms, key=lambda r: (r != "", r)):
            ri = self.tree.AppendItem(objs, rn or "(unnamed room)")
            for o in rooms[rn]:
                it = self.tree.AppendItem(ri, self.node_label(o)); self.item_node[it] = o; self.node_item[id(o)] = it
        pts = self.tree.AppendItem(root, "Points")
        bykind = {}
        for p in ir.points:
            if flt and flt not in p.full_name.lower(): continue
            bykind.setdefault(p.kind, []).append(p)
        for k in POINT_KINDS:
            if k in bykind:
                ki = self.tree.AppendItem(pts, f"{k} ({len(bykind[k])})")
                for p in bykind[k]:
                    it = self.tree.AppendItem(ki, p.name); self.item_node[it] = p; self.node_item[id(p)] = it
        li = self.tree.AppendItem(root, "Lights")
        for l in ir.lights:
            if flt and flt not in l.name.lower(): continue
            it = self.tree.AppendItem(li, l.name); self.item_node[it] = l; self.node_item[id(l)] = it
        for it in (objs, pts, li): self.tree.Expand(it)
        self.tree.Thaw()
        self._sync = False

    def sync_tree_selection(self):
        if self._sync: return
        self._sync = True
        self.tree.UnselectAll()
        first = None
        for n in self.doc.selection:
            it = self.node_item.get(id(n)) if hasattr(self, "node_item") else None
            if it and it.IsOk():
                self.tree.SelectItem(it, True)
                first = first or it
        if first: self.tree.EnsureVisible(first)
        self._sync = False

    def on_tree_sel(self, e):
        if self._sync or not self.tree: return
        nodes = [self.item_node[i] for i in self.tree.GetSelections() if i in self.item_node]
        self._sync = True
        self.doc.selection = nodes
        self._sync = False
        self.rebuild_props(); self.view.Refresh(False)
        self.SetStatusText(f"{len(nodes)} selected", 1)

    def rebuild_rooms(self):
        rooms = self.doc.ir.rooms
        if [self.rooms_list.GetString(i) for i in range(self.rooms_list.GetCount())] == [r or "(unnamed)" for r in rooms]: return
        self.rooms_list.Set([r or "(unnamed)" for r in rooms])
        for i in range(len(rooms)): self.rooms_list.Check(i, rooms[i] not in self.view.hidden_rooms)

    def on_room_toggle(self, e):
        rooms = self.doc.ir.rooms
        self.view.hidden_rooms = {rooms[i] for i in range(len(rooms)) if not self.rooms_list.IsChecked(i)}
        self.view.dirty_cache = True; self.view.Refresh(False)

    # ---- properties
    def rebuild_props(self):
        pgd = self.props
        pgd.Clear()
        sel = self.doc.selection
        if not sel:
            pgd.Append(pg.PropertyCategory("Nothing selected")); return
        n = sel[0]
        title = f"{type(n).__name__[2:]} '{n.name}'" + (f" (+{len(sel) - 1} more)" if len(sel) > 1 else "")
        pgd.Append(pg.PropertyCategory(title))
        pgd.Append(pg.StringProperty("Name", "name", n.name))
        rooms = [r or "" for r in self.doc.ir.rooms]
        pgd.Append(pg.EditEnumProperty("Room", "room", [r or "(unnamed)" for r in rooms], list(range(len(rooms))), n.room or "(unnamed)"))
        pgd.Append(pg.StringProperty("Prefix", "prefix", n.prefix or ""))
        pgd.Append(pg.PropertyCategory("Transform"))
        for i, ax in enumerate("XYZ"):
            pgd.Append(pg.FloatProperty(f"Position {ax}", f"pos{i}", n.pos[i]))
        eu = matrix_to_euler(n.matrix)
        for i, ax in enumerate("XYZ"):
            pgd.Append(pg.FloatProperty(f"Rotation {ax} (deg)", f"rot{i}", round(eu[i], 3)))
        pgd.Append(pg.FloatProperty("Scale", "scale", n.scale))
        pgd.Append(pg.StringProperty("Key", "key", n.key)).Enable(False) if False else None
        if isinstance(n, IRObject):
            pgd.Append(pg.PropertyCategory("Gameplay (node-name suffixes)"))
            pgd.Append(pg.BoolProperty("Collision (-collision)", "collision", n.collision)).SetAttribute("UseCheckbox", True)
            ch = pg.EnumProperty("Surface type", "surface", [v for _, v in SURFACE_CHOICES], [k for k, _ in SURFACE_CHOICES], n.surface)
            pgd.Append(ch)
            pgd.Append(pg.BoolProperty("Portal (-portal)", "portal", n.portal)).SetAttribute("UseCheckbox", True)
            pgd.Append(pg.EditEnumProperty("Portal target room", "portal_room", [r or "(unnamed)" for r in rooms], list(range(len(rooms))), n.portal_room or ""))
            pgd.Append(pg.BoolProperty("Backdrop (-backdrop)", "backdrop", n.backdrop)).SetAttribute("UseCheckbox", True)
            pgd.Append(pg.PropertyCategory("Engine flags"))
            pgd.Append(pg.IntProperty("static (f0b0)", "static", n.static)); pgd.Append(pg.IntProperty("hidden", "hidden", n.hidden)); pgd.Append(pg.IntProperty("show (f0b2)", "show", n.show))
            pgd.Append(pg.StringProperty("Mesh", "info", f"{len(n.mesh.verts)} verts, {len(n.mesh.faces)} faces")).Enable(False)
            pgd.Append(pg.StringProperty("Full name", "fullname", n.full_name)).Enable(False)
        elif isinstance(n, IRPoint):
            pgd.Append(pg.PropertyCategory("Point"))
            pgd.Append(pg.EnumProperty("Type", "kind", POINT_KINDS, list(range(len(POINT_KINDS))), POINT_KINDS.index(n.kind) if n.kind in POINT_KINDS else 0))
            if n.kind in ("pickup", "drop"):
                pgd.Append(pg.MultiChoiceProperty("Packages A", "packages_a", PACKAGES, n.packages_a))
                pgd.Append(pg.MultiChoiceProperty("Packages B", "packages_b", PACKAGES, n.packages_b))
                pgd.Append(pg.IntProperty("Enabled", "enabled", n.enabled))
        else:
            pgd.Append(pg.PropertyCategory("Light"))
            pgd.Append(pg.EnumProperty("Type", "ltype", [v for v in LIGHT_TYPES.values()], list(LIGHT_TYPES.keys()), n.ltype))
            pgd.Append(pg.FloatProperty("Intensity", "intensity", n.intensity))
            pgd.Append(pg.ColourProperty("Color", "color", wx.Colour(*[int(max(0, min(1, c)) * 255) for c in n.color])))
            pgd.Append(pg.FloatProperty("Range A", "range_a", n.range_a)); pgd.Append(pg.FloatProperty("Range B", "range_b", n.range_b))
            pgd.Append(pg.StringProperty("Lightmap", "lightmap", n.lightmap or ""))
        pgd.Refresh()

    def on_prop_changed(self, e):
        prop = e.GetProperty(); name = prop.GetName(); val = prop.GetValue()
        sel = list(self.doc.selection)
        if not sel: return
        old = [dict(n.__dict__) for n in sel]
        def mutate(n):
            if name == "name": n.name = str(val)
            elif name == "room": n.room = "" if str(prop.GetValueAsString()) == "(unnamed)" else str(prop.GetValueAsString()); self.doc.ir.ensure_room(n.room)
            elif name == "prefix": n.prefix = str(val) or None
            elif name.startswith("pos"):
                p = list(n.pos); p[int(name[3])] = float(val); n.pos = tuple(p)
            elif name.startswith("rot"):
                eu = list(matrix_to_euler(n.matrix)); eu[int(name[3])] = float(val); n.matrix = euler_to_matrix(*eu)
            elif name == "scale": n.scale = max(0.001, float(val))
            elif name in ("collision", "portal", "backdrop"): setattr(n, name, bool(val))
            elif name == "surface":
                n.surface = int(val)
                if n.surface: n.collision = True
            elif name == "portal_room": n.portal_room = "" if prop.GetValueAsString() == "(unnamed)" else prop.GetValueAsString()
            elif name in ("static", "hidden", "show", "enabled", "ltype"): setattr(n, name, int(val))
            elif name == "kind": n.kind = POINT_KINDS[int(val)]
            elif name in ("packages_a", "packages_b"): setattr(n, name, list(val))
            elif name in ("intensity", "range_a", "range_b"): setattr(n, name, float(val))
            elif name == "color":
                c = wx.Colour(); c = val if isinstance(val, wx.Colour) else wx.Colour(val); n.color = (c.Red() / 255, c.Green() / 255, c.Blue() / 255)
            elif name == "lightmap": n.lightmap = str(val) or None
        applied = {}
        def do():
            for n in sel: mutate(n)
        def undo():
            for n, d in zip(sel, old): n.__dict__.update(d)
        self.doc.push(f"Edit {name}", do, undo)
        if name in ("kind", "collision", "surface", "portal", "name"): self.rebuild_props()

    # ---- materials
    def rebuild_materials(self):
        names = sorted(self.doc.ir.materials)
        cur = [self.mat_list.GetString(i) for i in range(self.mat_list.GetCount())]
        if cur != names: self.mat_list.Set(names)

    def on_mat_select(self, e):
        name = self.mat_list.GetStringSelection(); m = self.doc.ir.materials.get(name)
        g = self.mat_grid; g.Clear()
        if not m: return
        g.Append(pg.StringProperty("Name", "name", m.name)).Enable(False)
        texs = self.view.tex.names()
        g.Append(pg.EditEnumProperty("Texture", "texture", texs, list(range(len(texs))), m.texture or ""))
        g.Append(pg.ColourProperty("Color A", "color_a", wx.Colour(*[int(max(0, min(1, c)) * 255) for c in m.color_a])))
        g.Append(pg.ColourProperty("Color B", "color_b", wx.Colour(*[int(max(0, min(1, c)) * 255) for c in m.color_b])))
        g.Append(pg.IntProperty("Mode", "mode", m.mode)); g.Append(pg.IntProperty("Blend", "blend", m.blend))
        g.Append(pg.BoolProperty("Alpha test (flag 1)", "f1", bool(m.flags & 1))); g.Append(pg.BoolProperty("Two sided (flag 2)", "f2", bool(m.flags & 2)))
        g.Append(pg.EnumProperty("Surface type", "mtype", [v for _, v in SURFACE_CHOICES], [k for k, _ in SURFACE_CHOICES], m.mtype))
        g.Append(pg.FloatProperty("Friction", "friction", m.friction)); g.Append(pg.FloatProperty("Energy absorption", "absorb", m.absorb)); g.Append(pg.FloatProperty("Extra", "extra", m.extra))

    def on_mat_changed(self, e):
        m = self.doc.ir.materials.get(self.mat_list.GetStringSelection())
        if not m: return
        prop = e.GetProperty(); name = prop.GetName(); val = prop.GetValue(); old = copy.copy(m)
        def do():
            if name == "texture": m.texture = prop.GetValueAsString() or None
            elif name in ("color_a", "color_b"):
                c = val if isinstance(val, wx.Colour) else wx.Colour(val); setattr(m, name, (c.Red() / 255, c.Green() / 255, c.Blue() / 255))
            elif name in ("mode", "blend", "mtype"): setattr(m, name, int(val))
            elif name == "f1": m.flags = (m.flags & ~1) | (1 if val else 0)
            elif name == "f2": m.flags = (m.flags & ~2) | (2 if val else 0)
            else: setattr(m, name, float(val))
        def undo(): m.__dict__.update(old.__dict__)
        self.doc.push("Edit material", do, undo)
        self.view.tex.clear_gl()

    # ---- town
    def rebuild_town(self):
        g = self.town_grid; g.Clear(); c = self.doc.town_cfg
        if c is None:
            g.Append(pg.PropertyCategory("No .tcfg next to this level")); return
        g.Append(pg.PropertyCategory(os.path.basename(self.doc.town_cfg_path or "")))
        for key, label in (("available_express", "Available: Express"), ("available_bomb", "Available: Bomb"), ("available_stunt", "Available: Stunt")):
            g.Append(pg.IntProperty(label, key, getattr(c, key)))
        for i, n in enumerate(("Map scale X", "Map scale Y")): g.Append(pg.FloatProperty(n, f"ms{i}", c.map_scale[i]))
        for i, n in enumerate(("Map offset X", "Map offset Y")): g.Append(pg.FloatProperty(n, f"mo{i}", c.map_offset[i]))
        g.Append(pg.IntProperty("Count", "count", c.count))
        for i in range(3): g.Append(pg.IntProperty(f"Flag {i}", f"fl{i}", c.flags[i]))
        g.Append(pg.FloatProperty("Value A", "value_a", c.value_a)); g.Append(pg.FloatProperty("Value B", "value_b", c.value_b))
        g.Append(pg.StringProperty("Description", "description", c.description))

    def on_town_changed(self, e):
        c = self.doc.town_cfg; prop = e.GetProperty(); n = prop.GetName(); v = prop.GetValue()
        if c is None: return
        if n.startswith("ms"): s = list(c.map_scale); s[int(n[2])] = float(v); c.map_scale = tuple(s)
        elif n.startswith("mo"): s = list(c.map_offset); s[int(n[2])] = float(v); c.map_offset = tuple(s)
        elif n.startswith("fl"): s = list(c.flags); s[int(n[2])] = int(v); c.flags = tuple(s)
        elif n == "description": c.description = str(v)
        elif n in ("value_a", "value_b"): setattr(c, n, float(v))
        else: setattr(c, n, int(v))
        self.doc.dirty = True; self.update_title()

    def on_make_tcfg(self, e):
        if self.doc.town_cfg is None:
            self.doc.town_cfg = tcfgmod.TownConfig()
            self.doc.dirty = True
        self.rebuild_town()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    shot = None; path = None; mode = None; yaw = pitch = dist = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--screenshot": shot = argv[i + 1]; i += 1
        elif a == "--mode": mode = int(argv[i + 1]); i += 1
        elif a == "--cam": yaw, pitch, dist = (float(x) for x in argv[i + 1].split(",")); i += 1
        else: path = a
        i += 1
    app = wx.App(False)
    fr = Frame(path)
    fr.Show()
    if shot:
        def grab():
            from PIL import Image
            v = fr.view
            if mode is not None: v.mode = mode; v.dirty_cache = True
            if yaw is not None: v.yaw, v.pitch, v.dist = yaw, pitch, dist
            arr = v.capture()
            Image.fromarray(arr).save(shot)
            fr.doc.dirty = False
            fr.Close(True)
        wx.CallLater(2500, grab)
    app.MainLoop()


if __name__ == "__main__":
    main()
