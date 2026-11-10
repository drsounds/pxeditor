"""
Constants and enumerations for the Pickup Express / cc.dll file formats.

Everything here was recovered from cc.dll (CcRoom::LoadScene / SaveScene), Pickup.exe and the
shipped data files.  Names marked "(guess)" are descriptive labels for fields whose exact engine
meaning is not proven; their raw values are still read/written faithfully.
"""

# ---------------------------------------------------------------------------------------------
# CCF chunk tags
# ---------------------------------------------------------------------------------------------
CHUNK_TAGS = {
    0x0001: "ROOT",
    0x1000: "ROOMS",
    0x1100: "ROOM",
    0x1200: "STATIC_BSP",
    0x1201: "PORTAL_BSP",
    0x2000: "MATERIALS",
    0x2100: "MATERIAL",
    0x2110: "MATERIAL_TEXTURE",
    0x2140: "MATERIAL_COLORS",
    0x2150: "MATERIAL_FLAGS",
    0x2152: "MATERIAL_PHYSICS_V2",
    0x2153: "MATERIAL_PHYSICS",
    0x3000: "BLUEPRINTS",
    0x3100: "MESH",
    0x3110: "VERTEX",
    0x3120: "POLYGON",
    0x4000: "INSTANCES",
    0x4100: "OBJECT",
    0x4101: "OBJECT_DYNAMIC_BSP",
    0x4200: "NULL",
    0x4210: "NULL_DATA",
    0x4300: "LIGHT",
    0x4310: "LIGHT_PARAMS_V1",
    0x4311: "LIGHT_PARAMS",
    0x4320: "LIGHT_FLARE",
    0x4330: "LIGHT_SPOT",
    0x4500: "VERTEX_EXTRA",        # written by the 3ds max exporter, ignored by cc.dll (always 0)
    0xF010: "NAME",
    0xF020: "STRING",
    0xF030: "COLOR",
    0xF040: "VECTOR",
    0xF050: "MATRIX",
    0xF060: "UV_TRIPLE",
    0xF070: "ROTATION",
    0xF090: "POLY_PAINT",
    0xF0A0: "MESH_VAMPIRE_MODE",
    0xF0B0: "INT_B0",
    0xF0B1: "INT_B1",
    0xF0B2: "MESH_BACKDROP",
    0xF0C0: "BSP_NODE",
    0xF0C1: "BSP_POLY",
    0xF0D0: "MESH_SPHERE",
}
TAG_BY_NAME = {v: k for k, v in CHUNK_TAGS.items()}

CCF_MAGIC = b"CcFf"
CCF_VERSION = 0x98092901          # 1998-09-29 rev 1
GTI_MAGIC = b"GtIm"
GTI_VERSION = 4
TCFG_MAGIC = b"PET0"
VCFG_MAGIC = b"PEV7"

# ---------------------------------------------------------------------------------------------
# Entity kinds (what a CCF can contain)
# ---------------------------------------------------------------------------------------------
ENTITY_TYPES = {
    "room": 0x1100,
    "material": 0x2100,
    "mesh": 0x3100,       # blueprint geometry
    "object": 0x4100,     # mesh instance placed in a room
    "null": 0x4200,       # helper point (spawn / pickup / drop / fuel / ai / camera / wheel ...)
    "light": 0x4300,
}

# ---------------------------------------------------------------------------------------------
# Null point types  (first 4 bytes of the null's 0x4210 user data)
# ---------------------------------------------------------------------------------------------
NULL_TYPES = {
    "SPWN": "spawn",      # player / AI vehicle spawn point.      data = tag only (4 bytes)
    "PKP1": "pickup",     # package pickup point.                 data = tag + u8 + list_a[257] + list_b[256]
    "PKP0": "pickup_v0",  # older pickup variant (accepted by Pickup.exe)
    "DRP1": "drop",       # package drop-off (delivery) point.    data = tag + u8 + list_a[257] + list_b[256]
    "FUEL": "fuel",       # gas station refuel point.             data = tag only
    "AIP0": "ai",         # AI path node; the game links nodes that have line of sight (BSP trace)
}
NULL_TYPE_TAGS = {v: k for k, v in NULL_TYPES.items()}
NULL_TYPE_TAGS["pickup_v0"] = "PKP0"

#: nulls that carry a package list (518 byte record)
NULL_PACKAGE_RECORD_SIZE = 518
NULL_LIST_A_OFFSET = 5        # char[257]
NULL_LIST_B_OFFSET = 262      # char[256]

#: Special null names looked up by Pickup.exe
SPECIAL_NULL_NAMES = {
    "backdrop": "backdrop.ccf anchor (sky dome is parented here)",
    "root": "vehicle / package root node",
    "wheel0": "vehicle wheel front-left (guess)",
    "wheel1": "vehicle wheel front-right (guess)",
    "wheel2": "vehicle wheel rear-left (guess)",
    "wheel3": "vehicle wheel rear-right (guess)",
    "frontcam": "vehicle front camera position",
    "backcam": "vehicle chase camera position",
}
#: names starting with this prefix are animated sprite-frame nulls
ANIM_NULL_PREFIX = "ANIMSF"

# ---------------------------------------------------------------------------------------------
# Material physics types (0x2153 first int). Values proven by matching .ccm against .ccf
# ---------------------------------------------------------------------------------------------
MATERIAL_TYPES = {
    0: "None",
    2: "Tree",
    10: "Concrete",
    15: "Water",          # driving onto water respawns the vehicle
}
MATERIAL_TYPE_IDS = {v.lower(): k for k, v in MATERIAL_TYPES.items()}
#: Types mentioned in the .ccm header comment but never used in shipped data: Glass, Steel, Mud.
#: Their numeric ids are unknown; the editor treats unknown ids as "Custom <n>".

#: 0x2150 material flags
MATERIAL_MODE = {           # (guess) CcMaterial+0x50 - shading / texture mode
    0: "textured_fullbright",   # colour A = 1,1,1 ; blend 0
    1: "textured_lit",
    2: "textured_lit_vertexcolor",
}
MATERIAL_FLAG_BITS = {      # CcMaterial+4
    0x1: "alpha_test",      # (guess) used on fences / trees / balconies with cut-out textures
    0x2: "two_sided",       # (guess) always set together with 0x1 in shipped data (0x3)
}

# ---------------------------------------------------------------------------------------------
# Polygon paint (0xF090)
# ---------------------------------------------------------------------------------------------
PAINT_TYPES = {
    1: "flat",              # one colour per polygon          (CcPolyPaintFlat)
    3: "gouraud",           # one colour per polygon vertex   (CcPolyPaintTri)
    4: "gouraud_f",         # same, float-precision variant   (CcPolyPaintTriF)
}

# ---------------------------------------------------------------------------------------------
# Object (0x4100) flags
# ---------------------------------------------------------------------------------------------
OBJECT_FLAGS = {
    "f0b0": "static (guess) - 1 for all town geometry, 0 for vehicles/backdrop",
    "f0b1": "collision - 1: polygons are included in the room's static BSP (collidable); 0: decoration only",
    "hidden": "CcObject+0x165 (guess) - 1 for most town geometry",
}
PORTAL_TYPE_NONE = -1
PORTAL_TYPE_PORTAL = 0       # object is a portal polygon set linking room_ref <-> portal_room_ref

# ---------------------------------------------------------------------------------------------
# Mesh flags
# ---------------------------------------------------------------------------------------------
MESH_FLAGS = {
    "flag_a": "bit0: backdrop mesh (loaded only when LoadScene flag 0x200 is set)",
    "flag_b": "bit0: portal mesh (loaded only when LoadScene flag 0x100 is set)",
}
MESH_VAMPIRE_MODES = {0: "normal", 1: "vampire"}     # CcMesh::SetVampireMode (shadow/visibility mode, guess)

# ---------------------------------------------------------------------------------------------
# Lights
# ---------------------------------------------------------------------------------------------
LIGHT_TYPES = {
    0: "omni",
    1: "point_attenuated",    # all shipped lights use 1 with a=0.0 b=20.0 (range)
    2: "spot",
    3: "directional",
}

# ---------------------------------------------------------------------------------------------
# Textures
# ---------------------------------------------------------------------------------------------
TEXTURE_FORMATS = {i: n for i, n in enumerate(
    "NONE P4 AP44 P8 AP88 RGB565 ARGB4444 RGB888 ARGB8888 RGB332 RGB555 ARGB1555 ARGB8332 "
    "DXT1 DXT2 DXT3 DXT4 DXT5 BGR565 BGRA4444 BGR888 BGRA8888 BGR233 BGR555 BGRA5551 BGRA2338 "
    "ABGR4444 ABGR8888 ABGR1555 ABGR8233 RGBA4444 RGBA8888 RGBA5551 RGBA3328 ARGB4565".split())}
TEXTURE_FORMAT_IDS = {v: k for k, v in TEXTURE_FORMATS.items()}
TEXTURE_SEARCH_PATHS = ["Towns/{town}/Textures", "General/Textures", "Vehicles/{vehicle}/Textures", "Packages/{package}/Textures", "Posters"]

# ---------------------------------------------------------------------------------------------
# Game modes / towns / tcfg
# ---------------------------------------------------------------------------------------------
GAME_MODES = {1: "express", 2: "bomb", 3: "stunt"}     # STRING_EXPRESS_CONTEST / STRING_HOT_GOODS / STRING_STUNT_RACE
TOWN_FILE_SUFFIX = {"express": "express", "bomb": "bomb", "stunt": "stunt"}   # Towns/<Town>/<town>-<mode>.ccf/.ccm/.tcfg
TOWN_FOLDERS = ["Docks", "Chinatown", "Downtown", "Suburb"]
TOWN_IMAGES = ["preview", "icon128", "icon256", "map"]            # Towns/<Town>/Images-<mode>/*.gti

# ---------------------------------------------------------------------------------------------
# Name suffix conventions used by the Blender / OBJ importers and exporters
# ---------------------------------------------------------------------------------------------
NAME_SUFFIXES = {
    "-collision": "object is collidable (object.flag_b1 = 1, included in static BSP)",
    "-water": "collidable water surface (material type Water = 15, vehicle respawns)",
    "-portal": "portal polygon set (mesh.flag_b = 1, object.portal_type = 0)",
    "-backdrop": "sky/backdrop mesh (mesh.flag_a = 1)",
    "-nocollision": "explicit decoration only (default when no suffix is given)",
}
SUFFIX_COLLISION = "-collision"
SUFFIX_WATER = "-water"
SUFFIX_PORTAL = "-portal"
SUFFIX_BACKDROP = "-backdrop"
SUFFIX_NOCOLLISION = "-nocollision"

#: suffixes that make an empty / null a typed game point:  "start-spawn", "shop1-pickup", ...
POINT_SUFFIXES = {
    "-spawn": "spawn",
    "-pickup": "pickup",
    "-drop": "drop",
    "-fuel": "fuel",
    "-ai": "ai",
}

# ---------------------------------------------------------------------------------------------
# Known packages (Packages/<name>/<name>.ccf) - used in pickup point package lists
# ---------------------------------------------------------------------------------------------
PACKAGES = ["GBshots", "aftonbladet", "aqua", "bilbatteri", "bocadillos", "crunchy", "dexgum", "dextrosol",
            "dvd", "estrella", "heinz", "korv", "lazer", "mer", "mp3", "pandvd", "philips", "pippi2", "polar",
            "puma_A", "puma_B", "sonymd", "tv", "video", "wheel"]
