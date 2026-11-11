# Pickup Express (cc.dll) file formats

Recovered by disassembling `cc.dll` (`CcRoom::LoadScene`, `CcRoom::SaveScene`, `CcChunkReadStream`)
and `Pickup.exe`, and verified by byte-exact round trips of every shipped file
(52 `.ccf`, 691 `.gti`, 9 `.tcfg`, 11 `.vcfg`; run `python -m unittest discover tests`).

## Engine
`cc.dll` exports a C++ engine: `Cc*` (scene: World, Room, Mesh, Object, Null, Light, Material, BSP,
RigidBody, Camera), `Gt*` (graphics: Device, Image, Texture, Font) and `Ph*` (collision: PhLine, PhSphere,
PhOBB against BSP trees).  Renderers are separate DLLs (`gtSoftware`, `gt3dfx`, `gtDirect3D`).
Coordinates: left-handed, Y up.  Front faces wind so that `cross(v1-v0, v2-v1)` points away from the viewer.

## .ccf  (scene)
`"CcFf"` `u32 0x98092901`, then a chunk tree.  Chunk = `u16 tag, u32 size` (size includes the 6 byte header).
A body is a fixed head followed by child chunks until the chunk ends.

| tag | meaning | head fields |
|---|---|---|
| 0x0001 | root | |
| 0x1000 / 0x1100 | rooms / room | name(f010), i32 ref; children 0x1200 static BSP, 0x1201 portal BSP |
| 0x2000 / 0x2100 | materials / material | name, ref; 0x2110 texture(f020), 0x2140 colours(2×f030, f32), 0x2150 flags(u8 mode,u8 blend,i32 flags), 0x2153 physics(i32 type,f32 friction,f32 absorb,f32 extra) |
| 0x3000 | blueprints | 0x3100 mesh, 0x4200 null, 0x4300 light |
| 0x3100 | mesh | name, ref, u8 backdrop, u8 portal, i32 parent, f040 pos, f32 scale, f070 rotation; 0x3110 vertices (f040 pos, optional f040 normal), 0x3120 polygons (3×i32 index, i32 material ref, f090 paint, f060 uv), f0a0 vampire mode, f0b2 |
| 0x4000 | instances | 0x4100 object, 0x4200 null, 0x4300 light |
| 0x4100 | object | name, ref, i32 mesh, i32 room, i32 parent, u8 hidden, i32 portalType(-1 none/0 portal), i32 portalRoom, pos, scale, rotation; f0b0 static, **f0b1 collision**, 0x4101 dynamic BSP |
| 0x4200 | null | name, ref, i32 room, i32 parent, pos, scale, rotation; 0x4210 user data (i32 length + bytes) |
| 0x4300 | light | like null; 0x4311 params (u8 type,f32 intensity,f030 colour,f32 a,f32 b,[f020 lightmap]), 0x4320 flare, 0x4330 spot, f0b0 |
| 0xf0xx | leaves | f010 name pair of f020 strings, f020 string (u32 len incl. NUL), f030 colour, f040 vec3, f050 3×3 rows, f060 3×uv, f070 rotation, f090 paint, f0c0 BSP node, f0c1 BSP polygon |

Transforms are **world space** (`world = v * scale @ R + pos`, rows of `R` are the basis vectors); `parent` is a
hierarchy link only.  Every mesh/null/light exists twice in shipped files: once as blueprint (room 0) and once
as instance.  Object `f0b1 = 1` puts the polygons into the room's static BSP; materials with type Water (15) in
a collidable object make the vehicle respawn.  Portal objects (`portalType 0`) are invisible and link two rooms.

### BSP
`0x1200` static / `0x1201` portal list holds f0c0 nodes: `i32 hasNeg [node] i32 hasPos [node] f040 normal f040 point {f0c1 polys}`.
A polygon is `5×f040 (cross(e1,e2), unit normal, v0, e1=v1-v0, e2=v2-v1), i32 polygon index, i32 object ref`.
`CcRoom::CreateStaticBsp` is never called by the engine at load time, so levels must ship their BSP;
`ccformats.bsp` rebuilds it (numpy, ~9 s for a whole town).

### Null user data (first 4 bytes)
`SPWN` spawn, `FUEL` gas station, `AIP0` AI path node, `PKP1` pickup / `DRP1` drop (518 bytes: tag, u8 enabled,
char[257] package list A, char[256] list B, names separated by CRLF).

## .gti  (texture)
`"GtIm" u32 4`, `CRC0` chunks, `Imag` chunks: `u32 format,w,h,paletteEntries,mipLevels`, BGRA palette, mip data
(top-down rows).  Each file stores a palettised (P8/AP88) and a true colour (RGB888/ARGB8888) image.  Formats are the
`GT_FMT_*` enum in `constants.TEXTURE_FORMATS`.  Mip chains stop at 8 px.

## .ccm  (material table, text)
`<texture> <type> <friction> <absorption> <extra>` per line, `'` comments.  Types seen: None, Tree, Concrete, Water.
Same values live in the .ccf material (0x2153: None 0, Tree 2, Concrete 10, Water 15).

## .tcfg  (town config, 102 bytes) / .vcfg  (vehicle config, 193 bytes)
See `ccformats/tcfg.py` and `ccformats/vcfg.py`.  Minimap: `pixel_x = z*scale_x + off_x`, `pixel_y = x*scale_y + off_y`; three flag bytes in the `.tcfg` negate either scale or swap the two world axes (`TownConfig.map_point`).
Fields whose engine meaning is not proven carry neutral names.

## Node-name suffix conventions (OBJ and Blender)
`-collision`, `-water` (collidable, material Water, respawn), `-concrete`, `-tree`, `-portal`, `-backdrop`
for meshes; `-spawn -pickup -drop -fuel -ai` for empties.  A trailing `.001` is ignored.
