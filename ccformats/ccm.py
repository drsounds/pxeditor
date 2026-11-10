"""
CcMaterial description files (.ccm) - plain text, one material per line:

    ' CcMaterial Description configuration file
    ' <Texture Filename> <Material Type> [Friction] [Energy Absorbation]
    avspr01                        Tree        0.000  0.900  0.000

Columns: texture name (padded to 31), type name (padded to 12), friction, energy absorption, extra.
The same values live inside the .ccf material chunk 0x2153 (type id, friction, absorb, extra);
the text file is the artist-facing source that the original tool chain merged into the .ccf.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List
from .constants import MATERIAL_TYPES, MATERIAL_TYPE_IDS

HEADER = ("' CcMaterial Description configuration file\r\n"
          "' <Texture Filename> <Material Type> [Friction] [Energy Absorbation]\r\n"
          "'\r\n"
          "' Example of Material Types: Water, Glass, Steel, Mud, Tree\r\n")


@dataclass
class MaterialDesc:
    texture: str
    type_name: str = "None"
    friction: float = 0.0
    absorb: float = 0.9
    extra: float = 0.0

    @property
    def type_id(self) -> int:
        return MATERIAL_TYPE_IDS.get(self.type_name.lower(), 0)


@dataclass
class MaterialTable:
    entries: List[MaterialDesc] = field(default_factory=list)

    @staticmethod
    def parse(text: str) -> "MaterialTable":
        t = MaterialTable()
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("'"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            nums = [float(x) for x in parts[2:5]] + [0.0, 0.9, 0.0][len(parts) - 2:]
            t.entries.append(MaterialDesc(parts[0], parts[1], nums[0], nums[1], nums[2]))
        return t

    @staticmethod
    def load(path: str) -> "MaterialTable":
        with open(path, "r", encoding="latin-1") as f:
            return MaterialTable.parse(f.read())

    def to_text(self) -> str:
        lines = [HEADER.rstrip("\r\n")]
        for e in self.entries:
            lines.append(f"{e.texture:<31}{e.type_name:<12}{e.friction:.3f}  {e.absorb:.3f}  {e.extra:.3f}")
        return "\n".join(lines) + "\n"

    def save(self, path: str):
        with open(path, "w", encoding="latin-1", newline="") as f:
            f.write(self.to_text())

    def by_texture(self) -> Dict[str, MaterialDesc]:
        return {e.texture.lower(): e for e in self.entries}

    @staticmethod
    def from_scene(scene) -> "MaterialTable":
        """Build a table from the materials of a ccf.Scene (one row per texture)."""
        t = MaterialTable(); seen = set()
        for m in scene.materials:
            tex = m.texture or m.name or "untextured"
            if tex.lower() in seen:
                continue
            seen.add(tex.lower()); p = m.physics
            t.entries.append(MaterialDesc(tex, MATERIAL_TYPES.get(p.mtype, f"Custom{p.mtype}"), p.friction, p.absorb, p.extra))
        t.entries.sort(key=lambda e: e.texture.lower())
        return t

    def apply_to_scene(self, scene):
        """Copy type / friction / absorb / extra onto every scene material with a matching texture."""
        rows = self.by_texture(); n = 0
        for m in scene.materials:
            if m.texture and m.texture.lower() in rows:
                r = rows[m.texture.lower()]; p = m.physics
                p.mtype, p.friction, p.absorb, p.extra = r.type_id, r.friction, r.absorb, r.extra; n += 1
        return n


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        t = MaterialTable.load(p)
        print(p, len(t.entries), "entries;", {e.type_name for e in t.entries})
