#!/usr/bin/env python
"""
pxtool - command line front end for the Pickup Express format library.

  pxtool.py info     <file.ccf|gti|tcfg|vcfg|ccm>
  pxtool.py ccf2obj  <in.ccf> <out.obj> [--game "C:/Program Files/Addgames/Pickup Express"]
  pxtool.py obj2ccf  <in.obj> <out.ccf> [--no-bsp] [--textures <dir with png/tga> --gti-out <Textures dir>]
  pxtool.py bsp      <in.ccf> <out.ccf>          rebuild static + portal BSP trees
  pxtool.py gti2png  <in.gti> <out.png>
  pxtool.py png2gti  <in.png> <out.gti>
  pxtool.py ccm      <in.ccf> <out.ccm>          write the material table of a scene
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ccformats import ccf, model, objio, bsp, gti, tcfg, vcfg, ccm

DEFAULT_GAME = r"C:\Program Files\Addgames\Pickup Express"


def texture_dirs(ccf_path, game):
    d = os.path.dirname(os.path.abspath(ccf_path))
    dirs = [os.path.join(d, "Textures")]
    if game:
        dirs += [os.path.join(game, "General", "Textures")]
    return [x for x in dirs if os.path.isdir(x)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("info", "gti2png", "png2gti", "ccm", "bsp", "ccf2obj", "obj2ccf"):
        p = sub.add_parser(name)
        p.add_argument("src");
        if name != "info": p.add_argument("dst")
        p.add_argument("--game", default=DEFAULT_GAME if os.path.isdir(DEFAULT_GAME) else None)
        p.add_argument("--no-bsp", action="store_true"); p.add_argument("--textures"); p.add_argument("--gti-out")
        p.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    ext = os.path.splitext(a.src)[1].lower()
    if a.cmd == "info":
        if ext == ".ccf": print(ccf.dump(ccf.Scene.load(a.src), a.verbose))
        elif ext == ".gti": print(gti.GtiFile.load(a.src).images)
        elif ext == ".tcfg": print(tcfg.TownConfig.load(a.src))
        elif ext == ".vcfg": print(vcfg.VehicleConfig.load(a.src).as_dict())
        elif ext == ".ccm": print(ccm.MaterialTable.load(a.src).entries)
        else: print("unknown file type")
    elif a.cmd == "ccf2obj":
        ir = model.scene_to_ir(ccf.Scene.load(a.src))
        print(objio.export_obj(ir, a.dst, texture_dirs(a.src, a.game)))
    elif a.cmd == "obj2ccf":
        ir = objio.import_obj(a.src)
        if a.textures and a.gti_out:
            print("converted textures:", objio.convert_textures(ir, [], a.gti_out, [a.textures]))
        sc = model.ir_to_scene(ir, build_bsp=not a.no_bsp)
        sc.save(a.dst); print("wrote", a.dst, len(sc.objects), "objects")
    elif a.cmd == "bsp":
        sc = ccf.Scene.load(a.src); bsp.rebuild_all(sc); sc.save(a.dst); print("wrote", a.dst)
    elif a.cmd == "gti2png":
        gti.GtiFile.load(a.src).to_pil().save(a.dst)
    elif a.cmd == "png2gti":
        from PIL import Image
        gti.GtiFile.from_pil(Image.open(a.src)).save(a.dst)
    elif a.cmd == "ccm":
        ccm.MaterialTable.from_scene(ccf.Scene.load(a.src)).save(a.dst)


if __name__ == "__main__":
    main()
