"""Explicit glTF Y-up / manufacturing Z-up conversion at file boundaries.

glTF 2.0: https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#coordinate-system-and-units
Never infer anatomical up from the most stable face or the longest dimension.
"""
from pathlib import Path
import numpy as np
import trimesh

GLTF_TO_PRINT = np.array([[1., 0., 0., 0.], [0., 0., -1., 0.],
                          [0., 1., 0., 0.], [0., 0., 0., 1.]])
PRINT_TO_GLTF = GLTF_TO_PRINT.T
FRAME_KEY = "am_coordinate_frame"


def load_print_scene(path: Path, *, process: bool = False) -> trimesh.Scene:
    """Bake scene node transforms, then map a format's world frame exactly once.

    STL is already in print coordinates. Unmarked GLB/glTF follows the format
    standard (+Y up). An explicit legacy +Z marker is honoured for migration;
    new GLB exports always use the standard +Y world frame.
    """
    path = Path(path)
    scene = trimesh.load_scene(path, process=process)
    if path.suffix.lower() in {".glb", ".gltf"}:
        marker = scene.metadata.get(FRAME_KEY, {})
        up = marker.get("world_up", "+Y")
        if up == "+Y":
            scene.apply_transform(GLTF_TO_PRINT)
        elif up != "+Z":
            raise ValueError("unsupported_declared_model_up_axis")
        scene.metadata[FRAME_KEY] = {"world_up": "+Z", "source_world_up": up,
                                     "conversion_applied": up != "+Z", "version": 1}
    return scene


def export_print_glb(mesh: trimesh.Trimesh) -> bytes:
    """Serialize with glTF's Y-up orientation, retaining project mm values."""
    scene = trimesh.Scene(mesh.copy())
    scene.apply_transform(PRINT_TO_GLTF)
    scene.metadata[FRAME_KEY] = {"world_up": "+Y", "print_up": "+Z", "version": 1,
                                 "coordinate_values_unit": "millimeter", "height_axis_in_print_frame": "Z"}
    return trimesh.exchange.gltf.export_glb(scene)


def require_current_normalized_frame(path: Path) -> None:
    """Old cached normalization receipts did not establish anatomical up."""
    scene = trimesh.load_scene(path, process=False)
    marker = scene.metadata.get(FRAME_KEY, {})
    if marker.get("version") != 1 or marker.get("world_up") != "+Y" or marker.get("print_up") != "+Z":
        raise ValueError("cached_normalization_has_no_verified_coordinate_frame; regenerate in a new task directory")
