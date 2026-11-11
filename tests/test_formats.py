"""Run:  python -m unittest discover tests   (game data tests are skipped when the game is not installed)"""
import os, sys, glob, unittest, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ccformats import ccf, gti, model, objio, bsp, tcfg, vcfg, ccm
from ccformats.constants import MATERIAL_TYPE_IDS

GAME = os.environ.get("PICKUP_GAME", r"C:\Program Files\Addgames\Pickup Express")
HAVE_GAME = os.path.isdir(GAME)


def box_ir(name="floor-collision"):
    ir = model.IRScene(); ir.ensure_room("")
    m = model.IRMesh(verts=[(-5, 0, -5), (5, 0, -5), (5, 0, 5), (-5, 0, 5)], faces=[(0, 2, 1), (0, 3, 2)],
                     face_mats=["grass", "grass"], face_uvs=[((0, 0), (1, 1), (1, 0)), ((0, 0), (0, 1), (1, 1))], face_colors=[None, None])
    pn = model.parse_name(name)
    ir.objects.append(model.IRObject(name=pn.base, mesh=m, collision=pn.collision, surface=pn.surface, key=pn.base))
    ir.material("grass")
    ir.points.append(model.IRPoint(name="s1", kind="spawn", pos=(0, 1, 0), key="s1"))
    ir.points.append(model.IRPoint(name="p1", kind="pickup", pos=(2, 1, 2), key="p1", packages_a=["tv", "dvd"]))
    return ir


class NameTests(unittest.TestCase):
    def test_suffixes(self):
        p = model.parse_name("lake-water.001"); self.assertEqual((p.base, p.surface, p.collision), ("lake", MATERIAL_TYPE_IDS["water"], True))
        p = model.parse_name("road-collision"); self.assertTrue(p.collision); self.assertEqual(p.surface, 0)
        p = model.parse_name("start-spawn"); self.assertEqual(p.point_kind, "spawn")
        self.assertEqual(model.build_name("lake", surface=15), "lake-water")


class SyntheticTests(unittest.TestCase):
    def test_build_and_reload(self):
        sc = model.ir_to_scene(box_ir(), build_bsp=True)
        data = sc.write_bytes(); sc2 = ccf.Scene.from_bytes(data)
        self.assertEqual(sc2.write_bytes(), data)
        o = sc2.objects[0]; self.assertEqual(o.flag_b1, 1)
        room = [r for r in sc2.rooms if r.static_bsp and r.static_bsp.roots][0]
        self.assertEqual(len(room.static_bsp.roots[0].all_polys()), 2)

    def test_water_material_clone(self):
        sc = model.ir_to_scene(box_ir("lake-water"), build_bsp=False)
        mt = sc.materials[0]; self.assertEqual(mt.physics.mtype, MATERIAL_TYPE_IDS["water"])

    def test_obj_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.obj"); objio.export_obj(box_ir("floor-collision"), p, None, False)
            ir = objio.import_obj(p)
            self.assertEqual((len(ir.objects), len(ir.points)), (1, 2))
            self.assertTrue(ir.objects[0].collision)
            self.assertEqual([x.kind for x in ir.points], ["spawn", "pickup"])
            self.assertEqual(ir.points[1].packages_a, ["tv", "dvd"])
            self.assertEqual(sorted((round(v[0]), round(v[2])) for v in ir.objects[0].mesh.verts), [(-5, -5), (-5, 5), (5, -5), (5, 5)])

    def test_gti_encode(self):
        import numpy as np
        a = np.random.default_rng(0).integers(0, 255, (32, 32, 4), dtype=np.uint8); a[:, :, 3] = 255
        g = gti.GtiFile.from_rgba(a); g2 = gti.GtiFile.from_bytes(g.to_bytes())
        self.assertTrue((g2.to_rgba_np() == a).all())


@unittest.skipUnless(HAVE_GAME, "game data not installed")
class GameDataTests(unittest.TestCase):
    def test_ccf_byte_exact(self):
        files = glob.glob(os.path.join(GAME, "General", "*.ccf")) + glob.glob(os.path.join(GAME, "Packages", "*", "*.ccf")) + glob.glob(os.path.join(GAME, "Vehicles", "*", "*.ccf"))
        self.assertGreater(len(files), 30)
        for f in files:
            d = open(f, "rb").read(); self.assertEqual(ccf.Scene.from_bytes(d).write_bytes(), d, f)

    def test_gti_byte_exact(self):
        for f in glob.glob(os.path.join(GAME, "General", "Textures", "*.gti")):
            d = open(f, "rb").read(); self.assertEqual(gti.GtiFile.from_bytes(d).to_bytes(), d)

    def test_configs(self):
        for f in glob.glob(os.path.join(GAME, "Towns", "*", "*.tcfg")):
            d = open(f, "rb").read(); self.assertEqual(tcfg.TownConfig.from_bytes(d).to_bytes(), d, f)
        for f in glob.glob(os.path.join(GAME, "Vehicles", "*", "*.vcfg")):
            d = open(f, "rb").read(); self.assertEqual(vcfg.VehicleConfig.from_bytes(d).to_bytes(), d, f)

    def test_ccm(self):
        for f in glob.glob(os.path.join(GAME, "Towns", "*", "*-*.ccm")):
            self.assertGreater(len(ccm.MaterialTable.load(f).entries), 0)

    def test_ir_roundtrip_vehicle(self):
        sc = ccf.Scene.load(os.path.join(GAME, "Vehicles", "Toyota", "Hilux.ccf"))
        ir = model.scene_to_ir(sc); sc2 = model.ir_to_scene(ir, build_bsp=False)
        self.assertEqual((len(sc.objects), len(sc.nulls)), (len(sc2.objects), len(sc2.nulls)))


if __name__ == "__main__":
    unittest.main()
