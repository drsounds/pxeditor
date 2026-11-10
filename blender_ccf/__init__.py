bl_info = {
    "name": "Pickup Express CCF / OBJ",
    "author": "pickup-express-tools",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "File > Import / Export",
    "description": "Import and export Pickup Express (cc.dll) .ccf scenes, with gameplay types encoded in node names",
    "category": "Import-Export",
}

import os
import math
import bpy
from bpy.props import StringProperty, BoolProperty, IntProperty, FloatProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper
from mathutils import Matrix, Vector

from .ccformats import ccf, model, gti, objio, bsp, ccm
from .ccformats.constants import MATERIAL_TYPES, MATERIAL_TYPE_IDS, NULL_TYPES, PACKAGES

ROOT_COLLECTION = "(root room)"


# ----------------------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------------------
def _m3(m):
    return Matrix(((m[0][0], m[0][1], m[0][2]), (m[1][0], m[1][1], m[1][2]), (m[2][0], m[2][1], m[2][2])))


def _world_matrix(node) -> Matrix:
    """Engine pos / rotation / uniform scale -> Blender 4x4 world matrix."""
    R = _m3(model.cc_to_blender_matrix(node.matrix))
    M = (R @ Matrix.Diagonal((node.scale, node.scale, node.scale))).to_4x4()
    M.translation = Vector(model.cc_to_blender_vec(node.pos))
    return M


def _get_collection(name: str):
    nm = name or ROOT_COLLECTION
    col = bpy.data.collections.get(nm)
    if col is None:
        col = bpy.data.collections.new(nm)
        bpy.context.scene.collection.children.link(col)
    return col


def _image_for(texture: str, dirs):
    """Load a .gti texture as a Blender image (cached by name)."""
    if not texture:
        return None
    img = bpy.data.images.get(texture)
    if img is not None:
        return img
    path = None
    for d in dirs:
        for fn in (texture + ".gti", texture.lower() + ".gti"):
            p = os.path.join(d, fn)
            if os.path.isfile(p):
                path = p; break
        if path: break
        if os.path.isdir(d):
            for fn in os.listdir(d):
                if fn.lower() == texture.lower() + ".gti":
                    path = os.path.join(d, fn); break
        if path: break
    if not path:
        return None
    try:
        g = gti.GtiFile.load(path)
        a = g.to_rgba_np()
        h, w = a.shape[:2]
        img = bpy.data.images.new(texture, w, h, alpha=True)
        import numpy as np
        px = (a[::-1].astype(np.float32) / 255.0).reshape(-1)      # Blender images are bottom-up
        img.pixels.foreach_set(px)
        img.pack()
        img.update()
        return img
    except Exception as e:                                                     # pragma: no cover
        print("ccf: texture", texture, "failed:", e)
        return None


def _make_material(m: model.IRMaterial, dirs):
    mat = bpy.data.materials.new(m.name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    mat.diffuse_color = (*m.color_a, 1.0)
    img = _image_for(m.texture, dirs)
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (*m.color_a, 1.0)
        bsdf.inputs["Roughness"].default_value = 1.0
        if img:
            tex = nt.nodes.new("ShaderNodeTexImage"); tex.image = img; tex.interpolation = "Linear"
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
            if m.flags & 1:                      # alpha-tested (fences, trees ...)
                nt.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
    mat["cc_texture"] = m.texture or ""
    mat["cc_mode"] = m.mode; mat["cc_blend"] = m.blend; mat["cc_flags"] = m.flags
    mat["cc_type"] = MATERIAL_TYPES.get(m.mtype, str(m.mtype))
    mat["cc_friction"] = m.friction; mat["cc_absorb"] = m.absorb; mat["cc_extra"] = m.extra
    mat["cc_color_b"] = list(m.color_b); mat["cc_f"] = m.f
    return mat


# ----------------------------------------------------------------------------------------------
# import
# ----------------------------------------------------------------------------------------------
class IMPORT_SCENE_OT_ccf(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.pickup_ccf"
    bl_label = "Import Pickup Express CCF"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".ccf"
    filter_glob: StringProperty(default="*.ccf", options={"HIDDEN"})
    load_textures: BoolProperty(name="Load textures", default=True, description="Decode .gti textures found in the Textures folders")
    import_points: BoolProperty(name="Spawn/pickup/drop/AI points", default=True)
    import_lights: BoolProperty(name="Lights", default=True)
    texture_dir: StringProperty(name="Extra texture folder", default="", subtype="DIR_PATH")

    def execute(self, context):
        sc = ccf.Scene.load(self.filepath)
        ir = model.scene_to_ir(sc)
        base = os.path.dirname(self.filepath)
        game = os.path.dirname(os.path.dirname(base)) if os.path.basename(os.path.dirname(base)) in ("Towns", "Vehicles", "Packages") else os.path.dirname(base)
        dirs = [self.texture_dir] if self.texture_dir else []
        dirs += [os.path.join(base, "Textures"), os.path.join(game, "General", "Textures"), os.path.join(game, "Towns", "Textures")]
        dirs = [d for d in dirs if d and os.path.isdir(d)]
        mats = {}
        for name, m in ir.materials.items():
            mats[name] = _make_material(m, dirs if self.load_textures else [])
        created = {}
        for o in ir.objects:
            created[o.key] = self._make_object(o, ir, mats)
        if self.import_points:
            for p in ir.points:
                created[p.key] = self._make_point(p)
        if self.import_lights:
            for l in ir.lights:
                created[l.key] = self._make_light(l)
        # hierarchy (keep world transform)
        for node in list(ir.objects) + list(ir.points) + list(ir.lights):
            ob = created.get(node.key)
            par = created.get(node.parent) if node.parent else None
            if ob is not None and par is not None:
                mw = ob.matrix_world.copy()
                ob.parent = par
                ob.matrix_parent_inverse = par.matrix_world.inverted()
        self.report({"INFO"}, f"Imported {len(ir.objects)} objects, {len(ir.points)} points, {len(ir.lights)} lights")
        return {"FINISHED"}

    def _link(self, ob, room):
        _get_collection(room).objects.link(ob)

    def _make_object(self, o: model.IRObject, ir, mats):
        mesh = bpy.data.meshes.new(o.name)
        im = o.mesh
        verts = [model.cc_to_blender_vec(v) for v in im.verts]
        mesh.from_pydata(verts, [], [tuple(f) for f in im.faces])
        # materials
        slot = {}
        for mn in dict.fromkeys(im.face_mats):
            slot[mn] = len(mesh.materials); mesh.materials.append(mats.get(mn))
        for i, p in enumerate(mesh.polygons):
            p.material_index = slot.get(im.face_mats[i], 0)
            p.use_smooth = True
        # uvs (engine v is top-down)
        uv = mesh.uv_layers.new(name="UVMap")
        col = mesh.color_attributes.new("cc_shade", "BYTE_COLOR", "CORNER") if any(c is not None for c in im.face_colors) else None
        li = 0
        for fi, p in enumerate(mesh.polygons):
            fuv = im.face_uvs[fi] if fi < len(im.face_uvs) else None
            fcol = im.face_colors[fi] if fi < len(im.face_colors) else None
            for k in range(3):
                if fuv: uv.data[li + k].uv = (fuv[k][0], 1.0 - fuv[k][1])
                if col is not None and fcol: col.data[li + k].color = (*fcol[k], 1.0)
            li += 3
        mesh.update(); mesh.validate()
        ob = bpy.data.objects.new(o.full_name, mesh)
        ob.matrix_world = _world_matrix(o)
        ob["cc_room"] = o.room; ob["cc_prefix"] = o.prefix or ""
        ob["cc_static"] = o.static; ob["cc_hidden"] = o.hidden; ob["cc_show"] = o.show
        if o.portal: ob["cc_portal_room"] = o.portal_room
        ob.display_type = "WIRE" if o.portal else "TEXTURED"
        if o.surface == MATERIAL_TYPE_IDS["water"]:
            ob.color = (0.2, 0.4, 1.0, 1.0)
        self._link(ob, o.room)
        return ob

    def _make_point(self, p: model.IRPoint):
        ob = bpy.data.objects.new(p.full_name, None)
        ob.empty_display_type = {"spawn": "SINGLE_ARROW", "pickup": "CUBE", "drop": "SPHERE", "fuel": "CONE", "ai": "PLAIN_AXES"}.get(p.kind, "ARROWS")
        ob.empty_display_size = 1.5 if p.kind != "ai" else 0.6
        ob.matrix_world = _world_matrix(p)
        ob["cc_room"] = p.room; ob["cc_prefix"] = p.prefix or ""
        if p.kind in ("pickup", "drop"):
            ob["cc_packages_a"] = "\n".join(p.packages_a); ob["cc_packages_b"] = "\n".join(p.packages_b); ob["cc_enabled"] = p.enabled
        if p.raw_data:
            ob["cc_raw"] = p.raw_data.hex()
        self._link(ob, p.room)
        return ob

    def _make_light(self, l: model.IRLight):
        ld = bpy.data.lights.new(l.name, "POINT" if l.ltype != 2 else "SPOT")
        ld.color = l.color; ld.energy = 100.0 * l.intensity
        ld.use_custom_distance = True; ld.cutoff_distance = max(l.range_b, 1.0)
        ob = bpy.data.objects.new(l.name, ld)
        ob.matrix_world = _world_matrix(l)
        ob["cc_room"] = l.room; ob["cc_prefix"] = l.prefix or ""; ob["cc_ltype"] = l.ltype; ob["cc_intensity"] = l.intensity
        ob["cc_range"] = [l.range_a, l.range_b]; ob["cc_lightmap"] = l.lightmap or ""
        ob["cc_spot"] = [*l.spot_dir, l.spot_a, l.spot_b]
        if l.flare: ob["cc_flare"] = list(l.flare)
        ob["cc_b0"] = l.flag_b0
        self._link(ob, l.room)
        return ob


# ----------------------------------------------------------------------------------------------
# export
# ----------------------------------------------------------------------------------------------
def _room_of(ob):
    r = ob.get("cc_room")
    if r is not None:
        return str(r)
    for col in ob.users_collection:
        if col.name != "Scene Collection":
            return "" if col.name == ROOT_COLLECTION else col.name
    return ""


def _decompose_for_cc(ob, bake_world=False):
    """Return (pos, matrix, scale, scale_vec_to_bake, bake_matrix) following the engine's uniform-scale limit."""
    mw = ob.matrix_world
    loc, rot, sca = mw.decompose()
    return loc, rot.to_matrix(), sca


def _surface_material(mat, dirs_out, textures: dict) -> model.IRMaterial:
    name = mat.name if mat else "default"
    tex = None
    if mat:
        tex = mat.get("cc_texture") or None
        if not tex and mat.use_nodes:
            for n in mat.node_tree.nodes:
                if n.type == "TEX_IMAGE" and n.image:
                    tex = os.path.splitext(n.image.name)[0]
                    textures[tex] = n.image
                    break
        elif tex and mat.use_nodes:
            for n in mat.node_tree.nodes:
                if n.type == "TEX_IMAGE" and n.image and os.path.splitext(n.image.name)[0] == tex:
                    textures[tex] = n.image
    m = model.IRMaterial(name, tex)
    if mat:
        d = mat.diffuse_color
        m.color_a = tuple(mat["cc_color_a"]) if "cc_color_a" in mat else (0.6, 0.6, 0.6) if tex else (d[0], d[1], d[2])
        m.color_b = tuple(mat["cc_color_b"]) if "cc_color_b" in mat else (1.0, 1.0, 1.0)
        m.mode = int(mat.get("cc_mode", 2 if tex else 1)); m.blend = int(mat.get("cc_blend", 1)); m.flags = int(mat.get("cc_flags", 0))
        t = mat.get("cc_type", "None")
        m.mtype = MATERIAL_TYPE_IDS.get(str(t).lower(), int(t) if str(t).isdigit() else 0)
        m.friction = float(mat.get("cc_friction", 0.0)); m.absorb = float(mat.get("cc_absorb", 0.9)); m.extra = float(mat.get("cc_extra", 0.0))
        m.f = float(mat.get("cc_f", 1.0))
    return m


def build_ir_from_blender(objects, depsgraph, textures: dict, apply_modifiers=True) -> model.IRScene:
    ir = model.IRScene()
    keyset = {ob.name for ob in objects}
    for ob in objects:
        pn = model.parse_name(ob.name)
        room = _room_of(ob); ir.ensure_room(room)
        loc, rot, sca = _decompose_for_cc(ob)
        parent = ob.parent.name if ob.parent and ob.parent.name in keyset else None
        common = dict(room=room, parent=parent, prefix=(ob.get("cc_prefix") or None), key=ob.name)
        pos = model.cc_to_blender_vec(tuple(loc))
        mrot = tuple(tuple(rot[i][j] for j in range(3)) for i in range(3))
        ccmat = model.blender_to_cc_matrix(mrot)
        if ob.type == "MESH":
            eval_ob = ob.evaluated_get(depsgraph) if apply_modifiers else ob
            me = eval_ob.to_mesh()
            try:
                me.calc_loop_triangles()
                uv_layer = me.uv_layers.active
                col_layer = me.color_attributes.get("cc_shade")
                mats = [s.material for s in ob.material_slots]
                im = model.IRMesh()
                sx, sy, sz = sca
                bake_all = (sx * sy * sz) < 0
                uniform = abs(sx - sy) < 1e-4 and abs(sy - sz) < 1e-4 and not bake_all
                if uniform:
                    scale = sx; sv = (1, 1, 1)
                else:
                    scale = 1.0; sv = (sx, sy, sz)
                for v in me.vertices:
                    c = v.co
                    if bake_all:
                        w = ob.matrix_world @ c
                        p = model.cc_to_blender_vec((w.x, w.y, w.z))
                    else:
                        p = model.cc_to_blender_vec((c.x * sv[0], c.y * sv[1], c.z * sv[2]))
                    im.verts.append(p)
                    n = v.normal
                    nn = model.cc_to_blender_vec((n.x, n.y, n.z))
                    im.normals.append(nn)
                if bake_all:
                    rot_mat = model.IDENT; pos = (0.0, 0.0, 0.0); scale = 1.0; ccmat = model.IDENT
                matnames = []
                for s_i, mt in enumerate(mats or [None]):
                    im_mat = _surface_material(mt, None, textures)
                    ir.materials.setdefault(im_mat.name, im_mat); matnames.append(im_mat.name)
                if not matnames: matnames = ["default"]; ir.material("default")
                for t in me.loop_triangles:
                    im.faces.append(tuple(t.vertices))
                    pi = t.polygon_index
                    mi = me.polygons[pi].material_index
                    im.face_mats.append(matnames[min(mi, len(matnames) - 1)])
                    if uv_layer:
                        im.face_uvs.append(tuple((uv_layer.data[l].uv[0], 1.0 - uv_layer.data[l].uv[1]) for l in t.loops))
                    else:
                        im.face_uvs.append(None)
                    if col_layer is not None:
                        im.face_colors.append(tuple(tuple(col_layer.data[l].color[:3]) for l in t.loops))
                    else:
                        im.face_colors.append(None)
                o = model.IRObject(name=pn.base, pos=pos, matrix=ccmat, scale=scale, mesh=im, collision=pn.collision,
                                   surface=pn.surface, portal=pn.portal, portal_room=str(ob.get("cc_portal_room", "")),
                                   backdrop=pn.backdrop, **common)
                o.static = int(ob.get("cc_static", 1)); o.hidden = int(ob.get("cc_hidden", 1)); o.show = int(ob.get("cc_show", 1))
                if pn.nocollision: o.collision = False
                ir.objects.append(o)
            finally:
                eval_ob.to_mesh_clear()
        elif ob.type == "EMPTY":
            kind = pn.point_kind or "null"
            p = model.IRPoint(name=pn.base, pos=pos, matrix=ccmat, scale=1.0, kind=kind, **common)
            p.enabled = int(ob.get("cc_enabled", 1))
            p.packages_a = [x for x in str(ob.get("cc_packages_a", "")).replace("\r", "").split("\n") if x]
            p.packages_b = [x for x in str(ob.get("cc_packages_b", "")).replace("\r", "").split("\n") if x]
            if ob.get("cc_raw"): p.raw_data = bytes.fromhex(ob["cc_raw"])
            ir.points.append(p)
        elif ob.type == "LIGHT":
            ld = ob.data
            l = model.IRLight(name=pn.base, pos=pos, matrix=ccmat, scale=1.0, **common)
            l.color = tuple(ld.color); l.intensity = float(ob.get("cc_intensity", ld.energy / 100.0))
            l.ltype = int(ob.get("cc_ltype", 2 if ld.type == "SPOT" else 1))
            if "cc_range" in ob: l.range_a, l.range_b = (float(x) for x in ob["cc_range"])
            elif ld.use_custom_distance: l.range_b = ld.cutoff_distance
            l.lightmap = ob.get("cc_lightmap") or None
            if "cc_flare" in ob: l.flare = tuple(float(x) for x in ob["cc_flare"])
            if "cc_spot" in ob:
                s = [float(x) for x in ob["cc_spot"]]; l.spot_dir = tuple(s[:3]); l.spot_a, l.spot_b = s[3], s[4]
            l.flag_b0 = int(ob.get("cc_b0", 1))
            ir.lights.append(l)
    return ir


def _png_from_blender_image(img):
    import numpy as np
    w, h = img.size
    a = np.empty(w * h * 4, np.float32); img.pixels.foreach_get(a)
    a = (a.reshape(h, w, 4)[::-1] * 255.0 + 0.5).astype(np.uint8)
    return a


def _pow2(a):
    import numpy as np
    h, w = a.shape[:2]
    pw = 1 << (w - 1).bit_length(); ph = 1 << (h - 1).bit_length()
    if (pw, ph) == (w, h): return a
    ys = (np.arange(ph) * h // ph); xs = (np.arange(pw) * w // pw)
    return a[ys][:, xs]


class EXPORT_SCENE_OT_ccf(bpy.types.Operator, ExportHelper):
    bl_idname = "export_scene.pickup_ccf"
    bl_label = "Export Pickup Express CCF"
    bl_options = {"PRESET"}
    filename_ext = ".ccf"
    filter_glob: StringProperty(default="*.ccf", options={"HIDDEN"})
    selection_only: BoolProperty(name="Selection only", default=False)
    apply_modifiers: BoolProperty(name="Apply modifiers", default=True)
    build_bsp: BoolProperty(name="Build collision BSP", default=True,
                            description="Rebuild the static/portal BSP trees. Required for collision; takes several seconds on big levels")
    export_textures: BoolProperty(name="Write .gti textures", default=True, description="Convert used images to .gti in a Textures folder next to the .ccf")
    write_ccm: BoolProperty(name="Write .ccm", default=True)

    def execute(self, context):
        objs = [o for o in (context.selected_objects if self.selection_only else context.scene.objects) if o.type in ("MESH", "EMPTY", "LIGHT")]
        textures = {}
        ir = build_ir_from_blender(objs, context.evaluated_depsgraph_get(), textures, self.apply_modifiers)
        ir.ensure_room("")
        sc = model.ir_to_scene(ir, build_bsp=self.build_bsp)
        sc.save(self.filepath)
        base = os.path.dirname(self.filepath)
        if self.export_textures and textures:
            tdir = os.path.join(base, "Textures"); os.makedirs(tdir, exist_ok=True)
            for name, img in textures.items():
                try:
                    a = _pow2(_png_from_blender_image(img))
                    gti.GtiFile.from_rgba(a).save(os.path.join(tdir, name + ".gti"))
                except Exception as e:
                    self.report({"WARNING"}, f"texture {name}: {e}")
        if self.write_ccm:
            ccm.MaterialTable.from_scene(sc).save(os.path.splitext(self.filepath)[0] + ".ccm")
        self.report({"INFO"}, f"Exported {len(ir.objects)} objects, {len(ir.points)} points, {len(ir.lights)} lights")
        return {"FINISHED"}


# ----------------------------------------------------------------------------------------------
# OBJ operators (same convention as the stand-alone exporter, usable without a game install)
# ----------------------------------------------------------------------------------------------
class EXPORT_SCENE_OT_ccobj(bpy.types.Operator, ExportHelper):
    bl_idname = "export_scene.pickup_obj"
    bl_label = "Export Pickup Express OBJ (with cc data)"
    filename_ext = ".obj"
    filter_glob: StringProperty(default="*.obj", options={"HIDDEN"})
    selection_only: BoolProperty(name="Selection only", default=False)

    def execute(self, context):
        objs = [o for o in (context.selected_objects if self.selection_only else context.scene.objects) if o.type in ("MESH", "EMPTY", "LIGHT")]
        textures = {}
        ir = build_ir_from_blender(objs, context.evaluated_depsgraph_get(), textures)
        st = objio.export_obj(ir, self.filepath, None, False)
        base = os.path.join(os.path.dirname(self.filepath), "textures"); os.makedirs(base, exist_ok=True)
        for name, img in textures.items():
            img2 = img.copy(); img2.filepath_raw = os.path.join(base, name + ".png"); img2.file_format = "PNG"; img2.save(); bpy.data.images.remove(img2)
        self.report({"INFO"}, str(st))
        return {"FINISHED"}


class IMPORT_SCENE_OT_ccobj(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.pickup_obj"
    bl_label = "Import Pickup Express OBJ (with cc data)"
    filename_ext = ".obj"
    filter_glob: StringProperty(default="*.obj", options={"HIDDEN"})

    def execute(self, context):
        ir = objio.import_obj(self.filepath)
        base = os.path.dirname(self.filepath)
        imp = IMPORT_SCENE_OT_ccf.__new__(IMPORT_SCENE_OT_ccf)
        mats = {}
        for name, m in ir.materials.items():
            mat = _make_material(m, [])
            if m.texture:
                for cand in (os.path.join(base, "textures", m.texture + ".png"), os.path.join(base, m.texture + ".png")):
                    if os.path.isfile(cand):
                        try:
                            img = bpy.data.images.load(cand)
                            tex = mat.node_tree.nodes.new("ShaderNodeTexImage"); tex.image = img
                            mat.node_tree.links.new(tex.outputs["Color"], mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
                        except Exception: pass
                        break
            mats[name] = mat
        created = {}
        for o in ir.objects: created[o.key] = IMPORT_SCENE_OT_ccf._make_object(imp, o, ir, mats)
        for p in ir.points: created[p.key] = IMPORT_SCENE_OT_ccf._make_point(imp, p)
        for l in ir.lights: created[l.key] = IMPORT_SCENE_OT_ccf._make_light(imp, l)
        return {"FINISHED"}


class OBJECT_OT_cc_set_type(bpy.types.Operator):
    """Add or replace the gameplay suffix of the selected objects' names"""
    bl_idname = "object.pickup_set_type"
    bl_label = "Set Pickup Express type"
    bl_options = {"REGISTER", "UNDO"}
    suffix: StringProperty(default="-collision")

    def execute(self, context):
        for ob in context.selected_objects:
            pn = model.parse_name(ob.name)
            if ob.type == "EMPTY":
                ob.name = model.build_name(pn.base, point_kind=self.suffix.lstrip("-"))
            else:
                if self.suffix == "-none":
                    ob.name = pn.base
                else:
                    surf = MATERIAL_TYPE_IDS.get(self.suffix.lstrip("-"), 0)
                    ob.name = model.build_name(pn.base, collision=self.suffix == "-collision", surface=surf,
                                               portal=self.suffix == "-portal", backdrop=self.suffix == "-backdrop")
        return {"FINISHED"}


class VIEW3D_PT_cc(bpy.types.Panel):
    bl_label = "Pickup Express"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Pickup"

    def draw(self, context):
        l = self.layout
        ob = context.active_object
        l.label(text="Surface / collision (mesh)")
        row = l.row(align=True)
        for suf, txt in (("-collision", "Collision"), ("-water", "Water"), ("-concrete", "Concrete"), ("-tree", "Tree"), ("-none", "None")):
            row.operator("object.pickup_set_type", text=txt).suffix = suf
        row = l.row(align=True)
        for suf, txt in (("-portal", "Portal"), ("-backdrop", "Backdrop")):
            row.operator("object.pickup_set_type", text=txt).suffix = suf
        l.label(text="Game points (empty)")
        row = l.row(align=True)
        for suf, txt in (("spawn", "Spawn"), ("pickup", "Pickup"), ("drop", "Drop"), ("fuel", "Fuel"), ("ai", "AI")):
            row.operator("object.pickup_set_type", text=txt).suffix = suf
        if ob:
            pn = model.parse_name(ob.name)
            box = l.box()
            box.label(text=f"{pn.base}   collision={pn.collision} surface={MATERIAL_TYPES.get(pn.surface, '-')} point={pn.point_kind}")
            box.prop(ob, '["cc_room"]', text="Room") if "cc_room" in ob else box.label(text="room: from collection")
            if pn.point_kind in ("pickup", "drop"):
                if "cc_packages_a" in ob: box.prop(ob, '["cc_packages_a"]', text="Packages")


def menu_import(self, context):
    self.layout.operator(IMPORT_SCENE_OT_ccf.bl_idname, text="Pickup Express (.ccf)")
    self.layout.operator(IMPORT_SCENE_OT_ccobj.bl_idname, text="Pickup Express OBJ with cc data (.obj)")


def menu_export(self, context):
    self.layout.operator(EXPORT_SCENE_OT_ccf.bl_idname, text="Pickup Express (.ccf)")
    self.layout.operator(EXPORT_SCENE_OT_ccobj.bl_idname, text="Pickup Express OBJ with cc data (.obj)")


classes = (IMPORT_SCENE_OT_ccf, EXPORT_SCENE_OT_ccf, IMPORT_SCENE_OT_ccobj, EXPORT_SCENE_OT_ccobj, OBJECT_OT_cc_set_type, VIEW3D_PT_cc)


def register():
    for c in classes: bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    for c in reversed(classes): bpy.utils.unregister_class(c)
