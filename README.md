# pickup-express-tools

Tools for **Pickup Express** (Addgames, 2000) and its `cc.dll` engine.

| part | what |
|---|---|
| `ccformats/` | Python API: `ccf` (chunk tree), `model` (high level scene IR), `gti`, `ccm`, `tcfg`, `vcfg`, `bsp`, `objio`, `constants` (type tables such as `MATERIAL_TYPES`, `NULL_TYPES`, `NAME_SUFFIXES`) |
| `cceditor/` | wxPython + OpenGL level editor: `python -m cceditor [level.ccf]` |
| `cceditor/car_editor.py` | wxPython car editor for `.vcfg` vehicle configs: `python -m cceditor.car_editor [car.vcfg]` |
| `cceditor/town_editor.py` | wxPython town editor for `.tcfg` town configs (minimap alignment preview): `python -m cceditor.town_editor [town.tcfg]` |
| `blender_ccf/` | Blender 4.2+ add-on (import/export `.ccf`, OBJ with game data). Build: `python build_blender_addon.py [--install]` -> `dist/pickup_express_ccf.zip` |
| `pxtool.py` | CLI: `ccf2obj`, `obj2ccf`, `bsp`, `gti2png`, `png2gti`, `ccm`, `info` |
| `docs/FORMATS.md` | format notes |

```
pip install -r requirements.txt
python pxtool.py ccf2obj "C:/Program Files/Addgames/Pickup Express/Towns/Docks/docks-express.ccf" docks.obj
python pxtool.py obj2ccf docks.obj docks-new.ccf        # rebuilds collision BSP
python -m cceditor "C:/Program Files/Addgames/Pickup Express/Towns/Downtown/downtown-express.ccf"
```
The game folder is read-only for normal users: save levels elsewhere and copy them in.

## Name suffixes (Blender nodes and OBJ objects)
`road-collision` collidable, `lake-water` collidable water (vehicle respawns), `wall-concrete`, `tree-tree`,
`door-portal`, `sky-backdrop`; empties: `start-spawn`, `shop-pickup`, `home-drop`, `gas-fuel`, `node-ai`.
Rooms: Blender collections / OBJ `# cc room=` comments.  Pickup/drop package lists: custom properties
`cc_packages_a` / `cc_packages_b` (one package name per line, names in `constants.PACKAGES`).

## Editor
Left: scene tree + room visibility.  Centre: viewport (RMB orbit, Shift+RMB pan, wheel zoom, click select,
G/R/S/Q tools with axis handles, F frame, 1/2/3 textured / collision map / wire, Del, Ctrl+D, Ctrl+Z/Y).
Right: properties (suffix flags as checkboxes), materials (type, friction, texture), town config (.tcfg).
Tools > Validate checks for missing spawn/pickup/drop/fuel/AI points, duplicates, unknown packages and missing textures.
Saving rebuilds the BSP (menu option) and writes the `.ccf`, `.ccm` and `.tcfg`.

## Status / limits
* Verified: byte-exact round trip of all shipped `.ccf/.gti/.tcfg/.vcfg`; Blender import/export of Docks and Hilux; BSP trees self-consistent.
* Not verified: loading generated levels in the game itself (not run here). Meanings marked "guess" in `constants.py` are unproven.
* Rendering is OpenGL (fixed function); there is no Vulkan backend.
