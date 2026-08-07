"""Generate Blender scenes of increasing render cost.

Run inside Blender, not in the project venv:
    blender -b -P scenes/make_scenes.py
"""

import json
import os

import bpy

SHOTS = [
    {"shot": "SH001", "samples": 16, "frames": [1, 2, 3]},
    {"shot": "SH002", "samples": 64, "frames": [1, 2, 3]},
    {"shot": "SH003", "samples": 256, "frames": [1, 2, 3]},
]

# Geometry is identical across shots and deliberately light. An earlier version
# varied subdivision level per shot, which made render cost geometry-bound: at
# subdivision 6 a frame cost ~25s regardless of sample count, so dropping to a
# quarter of the samples bought about 4%. A proxy quality tier is only a real
# scheduling lever if sample count is what the renderer actually spends its time
# on, so cost is varied by samples alone.
SUBDIVISIONS = 3

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))


def build_scene(samples: int) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)

    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=SUBDIVISIONS, radius=1.0)
    sphere = bpy.context.active_object
    material = bpy.data.materials.new(name="Shot")
    material.use_nodes = True
    # A rough dielectric costs real samples to resolve. A flat diffuse surface
    # converges almost immediately and would hide the sample count again.
    material.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.45
    sphere.data.materials.append(material)

    # A floor gives the sampler bounce light to integrate rather than empty space.
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, -1.2))

    bpy.ops.object.light_add(type="AREA", location=(4, -4, 6))
    light = bpy.context.active_object.data
    light.energy = 3000
    light.size = 6.0          # a large emitter means soft shadows, which need samples

    bpy.ops.object.camera_add(location=(6, -6, 4), rotation=(1.1, 0, 0.8))
    bpy.context.scene.camera = bpy.context.active_object

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = False
    # Adaptive sampling treats `samples` as a ceiling and stops early, so the
    # rendered count is not the requested count and a "quarter the samples"
    # proxy tier would buy an unpredictable amount. Off, the lever is linear.
    scene.cycles.use_adaptive_sampling = False
    scene.render.resolution_x = 480
    scene.render.resolution_y = 270
    scene.frame_start = 1
    scene.frame_end = 3


def main() -> None:
    manifest = []
    for entry in SHOTS:
        build_scene(entry["samples"])
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
