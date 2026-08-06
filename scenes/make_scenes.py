"""Generate Blender scenes of increasing render cost.

Run inside Blender, not in the project venv:
    blender -b -P scenes/make_scenes.py
"""

import json
import os

import bpy

SHOTS = [
    {"shot": "SH001", "samples": 16, "subdivisions": 2, "frames": [1, 2, 3]},
    {"shot": "SH002", "samples": 64, "subdivisions": 4, "frames": [1, 2, 3]},
    {"shot": "SH003", "samples": 256, "subdivisions": 6, "frames": [1, 2, 3]},
]

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))


def build_scene(samples: int, subdivisions: int) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)

    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=subdivisions, radius=1.0)
    obj = bpy.context.active_object
    modifier = obj.modifiers.new(name="Subdiv", type="SUBSURF")
    modifier.render_levels = 2

    bpy.ops.object.light_add(type="AREA", location=(4, -4, 6))
    bpy.context.active_object.data.energy = 800

    bpy.ops.object.camera_add(location=(6, -6, 4), rotation=(1.1, 0, 0.8))
    bpy.context.scene.camera = bpy.context.active_object

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = False
    scene.render.resolution_x = 480
    scene.render.resolution_y = 270
    scene.frame_start = 1
    scene.frame_end = 3


def main() -> None:
    manifest = []
    for entry in SHOTS:
        build_scene(entry["samples"], entry["subdivisions"])
        path = os.path.join(OUT_DIR, f"{entry['shot']}.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        manifest.append(
            {
                "shot": entry["shot"],
                "scene": f"scenes/{entry['shot']}.blend",
                "samples": entry["samples"],
                "frames": entry["frames"],
            }
        )

    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)


main()
