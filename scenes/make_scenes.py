"""Generate Blender scenes of increasing render cost.

Run inside Blender, not in the project venv:
    blender -b -P scenes/make_scenes.py

Two jobs, and the second is not decoration. The scenes must cost measurably
different amounts to render, because the forecaster's whole premise is that
shots are not interchangeable. They must also be *legible at 132 pixels wide*,
because the board puts the last rendered frame in every row and a grid of grey
blobs quietly undersells the one claim that matters here: these are real frames
off a real farm, not mock data.

So each shot gets its own subject, its own colour, and its own backdrop. You can
tell them apart in a thumbnail, which is exactly what a coordinator scanning
dailies needs to do.
"""

import json
import math
import os

import bpy

SHOTS = [
    {
        "shot": "SH001", "samples": 16, "subdivisions": 3, "frames": [1, 2, 3],
        "subject": "monkey",
        "colour": (0.95, 0.45, 0.10, 1.0),      # amber
        "backdrop": (0.05, 0.14, 0.16, 1.0),    # deep teal
        "key": (1.0, 0.85, 0.65),
    },
    {
        "shot": "SH002", "samples": 64, "subdivisions": 4, "frames": [1, 2, 3],
        "subject": "torus",
        "colour": (0.85, 0.12, 0.45, 1.0),      # magenta
        "backdrop": (0.06, 0.07, 0.16, 1.0),    # midnight blue
        "key": (0.75, 0.8, 1.0),
    },
    {
        "shot": "SH003", "samples": 256, "subdivisions": 5, "frames": [1, 2, 3],
        "subject": "sphere",
        "colour": (0.10, 0.75, 0.80, 1.0),      # cyan
        "backdrop": (0.16, 0.09, 0.04, 1.0),    # warm dark
        "key": (1.0, 0.95, 0.9),
    },
]

OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def add_subject(kind: str, subdivisions: int):
    if kind == "monkey":
        bpy.ops.mesh.primitive_monkey_add(size=2.2)
    elif kind == "torus":
        bpy.ops.mesh.primitive_torus_add(major_radius=1.3, minor_radius=0.45,
                                         major_segments=64, minor_segments=24)
    else:
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=subdivisions, radius=1.3)

    obj = bpy.context.active_object
    obj.rotation_euler = (0.25, 0.0, 0.6)
    modifier = obj.modifiers.new(name="Subdiv", type="SUBSURF")
    modifier.render_levels = 2
    bpy.ops.object.shade_smooth()
    return obj


def paint(obj, rgba):
    material = bpy.data.materials.new(name=f"{obj.name}_mat")
    material.use_nodes = True
    bsdf = material.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Roughness"].default_value = 0.32
    bsdf.inputs["Metallic"].default_value = 0.15
    obj.data.materials.append(material)


def set_backdrop(rgba):
    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = rgba
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 1.0
    bpy.context.scene.world = world


def build_scene(entry) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)

    subject = add_subject(entry["subject"], entry["subdivisions"])
    paint(subject, entry["colour"])
    set_backdrop(entry["backdrop"])

    # Key light warm and close, so the subject separates from its backdrop even
    # when the whole frame is 132 pixels wide.
    bpy.ops.object.light_add(type="AREA", location=(3.5, -3.5, 4.5))
    key = bpy.context.active_object
    key.data.energy = 900
    key.data.size = 4.0
    key.data.color = entry["key"]

    # A dim rim from behind to keep the silhouette readable.
    bpy.ops.object.light_add(type="AREA", location=(-3.0, 3.0, 2.0))
    rim = bpy.context.active_object
    rim.data.energy = 260
    rim.data.size = 3.0

    bpy.ops.object.camera_add(location=(0, -5.4, 1.6),
                              rotation=(math.radians(80), 0, 0))
    bpy.context.scene.camera = bpy.context.active_object

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = entry["samples"]
    scene.cycles.use_denoising = False
    scene.render.resolution_x = 480
    scene.render.resolution_y = 270
    scene.frame_start = 1
    scene.frame_end = 3

    # A slow turn across the three frames, so consecutive frames of one shot are
    # visibly different and progress reads on the board.
    subject.rotation_euler = (0.25, 0.0, 0.6)
    subject.keyframe_insert(data_path="rotation_euler", frame=1)
    subject.rotation_euler = (0.25, 0.0, 1.4)
    subject.keyframe_insert(data_path="rotation_euler", frame=3)


def main() -> None:
    manifest = []
    for entry in SHOTS:
        build_scene(entry)
        path = os.path.join(OUT_DIR, f"{entry['shot']}.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        manifest.append({
            "shot": entry["shot"],
            "scene": f"scenes/{entry['shot']}.blend",
            "samples": entry["samples"],
            "frames": entry["frames"],
        })

    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)


main()
