"""Build dist/pickup_express_ccf.zip (Blender extension) and install a dev copy into the add-on folder.

  python build_blender_addon.py            build zip only
  python build_blender_addon.py --install  also copy to %APPDATA%/Blender Foundation/Blender/<ver>/extensions/user_default
"""
import os, shutil, sys, zipfile, glob
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "blender_ccf")
LIB = os.path.join(HERE, "ccformats")
STAGE = os.path.join(HERE, "build", "pickup_express_ccf")


def stage():
    shutil.rmtree(STAGE, ignore_errors=True)
    shutil.copytree(SRC, STAGE, ignore=shutil.ignore_patterns("__pycache__", "ccformats"))
    shutil.copytree(LIB, os.path.join(STAGE, "ccformats"), ignore=shutil.ignore_patterns("__pycache__"))


def zip_it():
    os.makedirs(os.path.join(HERE, "dist"), exist_ok=True)
    out = os.path.join(HERE, "dist", "pickup_express_ccf.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(STAGE):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, STAGE))
    return out


if __name__ == "__main__":
    stage(); print("zip:", zip_it())
    if "--install" in sys.argv:
        for d in glob.glob(os.path.join(os.environ.get("APPDATA", ""), "Blender Foundation", "Blender", "*")):
            dst = os.path.join(d, "extensions", "user_default", "pickup_express_ccf")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.rmtree(dst, ignore_errors=True); shutil.copytree(STAGE, dst); print("installed", dst)
