"""Preserve a reviewed source's upright direction through Bambu placement."""
from pathlib import Path
import numpy as np
import trimesh
from scipy.spatial import cKDTree
from am_print_executor.geometry_fidelity_gate import normalized_boolean_copy
from am_print_executor.rigid_multimaterial_project import parse_3mf_leaves


def inspect_upright_source_preserved(source: Path, project: Path, *, maximum_tilt_degrees: float = 1.0) -> dict:
    """Allow yaw/translation, but reject laying an upright figurine on its back.

    First prove that Bambu's local vertices differ only by translation from
    the input. Otherwise a baked rotation could hide behind an identity XML
    transform. This is a pose-preservation check, not anatomy recognition.
    """
    mesh = normalized_boolean_copy(trimesh.load(source, force="mesh", process=False))
    leaves = parse_3mf_leaves(project)
    if len(leaves) != 1:
        raise ValueError("upright_pose_check_requires_single_source_leaf")
    leaf = leaves[0]
    local = normalized_boolean_copy(trimesh.Trimesh(vertices=leaf["vertices_local"], faces=leaf["faces"], process=False))
    centered_source = mesh.vertices - mesh.vertices.mean(axis=0)
    centered_local = local.vertices - local.vertices.mean(axis=0)
    error = max(cKDTree(centered_source).query(centered_local)[0].max(),
                cKDTree(centered_local).query(centered_source)[0].max())
    if len(mesh.faces) != len(local.faces) or error > .001:
        raise ValueError("unproven_local_geometry_frame_after_orientation")
    matrix = np.asarray(leaf["matrix"], dtype=float)
    rotation = matrix[:3, :3]
    if np.max(np.abs(rotation @ rotation.T - np.eye(3))) > 1e-4 or abs(np.linalg.det(rotation)-1) > 1e-4:
        raise ValueError("orientation_is_not_a_proper_rigid_transform")
    mapped_up = np.array([0., 0., 1.]) @ rotation
    tilt = float(np.degrees(np.arccos(np.clip(mapped_up[2], -1, 1))))
    translation = np.eye(4)
    translation[3, :3] = local.vertices.mean(axis=0) - mesh.vertices.mean(axis=0)
    source_to_world = translation @ matrix
    return {"status": "pass" if tilt <= maximum_tilt_degrees else "blocked",
            "source_up_axis": "+Z", "mapped_up_vector": mapped_up.tolist(), "tilt_degrees": tilt,
            "maximum_tilt_degrees": maximum_tilt_degrees, "source_local_frame_error_mm": float(error),
            "source_to_world_row_vector": source_to_world.tolist(),
            "semantics": "preserve_reviewed_upright_source; yaw_and_translation_only"}


def transform_protected_regions(regions, matrix):
    """Carry semantic AABBs with an accepted rigid placement."""
    from itertools import product
    result=[]
    for region in regions:
        bounds=np.asarray(region.get("bounds_mm"),dtype=float)
        if bounds.shape != (2,3) or not np.isfinite(bounds).all():
            raise ValueError("unlocalized_critical_region")
        corners=np.array(list(product(*zip(bounds[0],bounds[1]))))
        world=np.c_[corners,np.ones(8)] @ np.asarray(matrix)
        result.append({**region,"bounds_mm":[world[:,:3].min(axis=0).tolist(),world[:,:3].max(axis=0).tolist()]})
    return result
