from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile

from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
import posixpath

import numpy as np
import trimesh


class PrintabilityGateError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(chunk)

    return h.hexdigest()


def is_enabled(value: Any) -> bool:
    if value is True:
        return True

    if isinstance(value, int):
        return value == 1

    if isinstance(value, str):
        return value.strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    return False


def validate_support_settings(
    settings: dict[str, Any],
) -> dict[str, Any]:

    blockers: list[str] = []

    enable_support = settings.get(
        "enable_support"
    )

    support_type = settings.get(
        "support_type"
    )

    floating = settings.get(
        "detect_floating_vertical_shell"
    )

    overhang = settings.get(
        "detect_overhang_wall"
    )

    bridge_no_support = settings.get(
        "bridge_no_support"
    )

    if not is_enabled(enable_support):
        blockers.append(
            "automatic_support_is_disabled"
        )

    if support_type != "tree(auto)":
        blockers.append(
            "support_type_is_not_tree_auto"
        )

    if not is_enabled(floating):
        blockers.append(
            "floating_shell_detector_is_disabled"
        )

    if not is_enabled(overhang):
        blockers.append(
            "overhang_wall_detector_is_disabled"
        )

    if str(
        bridge_no_support
    ).strip().lower() not in {
        "0",
        "false",
    }:
        blockers.append(
            "bridge_no_support_is_enabled"
        )

    return {
        "enable_support":
            enable_support,
        "support_type":
            support_type,
        "detect_floating_vertical_shell":
            floating,
        "detect_overhang_wall":
            overhang,
        "bridge_no_support":
            bridge_no_support,
        "blockers":
            blockers,
    }


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attr_local(
    node: ET.Element,
    name: str,
) -> str | None:
    for key, value in node.attrib.items():
        if key.rsplit("}", 1)[-1] == name:
            return value
    return None


def _unit_scale_mm(
    root: ET.Element,
) -> float:
    unit = root.attrib.get(
        "unit",
        "millimeter",
    ).strip().lower()

    table = {
        "micron": 0.001,
        "millimeter": 1.0,
        "centimeter": 10.0,
        "inch": 25.4,
        "foot": 304.8,
        "meter": 1000.0,
    }

    if unit not in table:
        raise PrintabilityGateError(
            f"Unsupported 3MF unit: {unit!r}"
        )

    return table[unit]


def _transform_3mf(
    value: str | None,
    *,
    translation_scale: float,
) -> np.ndarray:

    if not value:
        return np.eye(
            4,
            dtype=float,
        )

    values = [
        float(x)
        for x in value.split()
    ]

    if len(values) != 12:
        raise PrintabilityGateError(
            "Invalid 3MF transform: "
            f"expected 12 values, got {len(values)}"
        )

    # 3MF stores the affine transform as 12 values.
    # Convert it into the conventional 4x4
    # column-vector matrix used below.
    return np.asarray(
        [
            [
                values[0],
                values[3],
                values[6],
                values[9] * translation_scale,
            ],
            [
                values[1],
                values[4],
                values[7],
                values[10] * translation_scale,
            ],
            [
                values[2],
                values[5],
                values[8],
                values[11] * translation_scale,
            ],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=float,
    )


def _normalise_3mf_path(
    current_model: str,
    raw_path: str | None,
) -> str:

    if not raw_path:
        return current_model

    raw = raw_path.replace(
        "\\",
        "/",
    )

    if raw.startswith("/"):
        candidate = raw.lstrip("/")
    else:
        candidate = posixpath.join(
            posixpath.dirname(
                current_model
            ),
            raw,
        )

    return posixpath.normpath(
        candidate
    )


def _load_bambu_3mf_mesh(
    path: Path,
) -> tuple[trimesh.Trimesh, dict[str, Any]]:

    if not zipfile.is_zipfile(path):
        raise PrintabilityGateError(
            "Bambu project is not a ZIP/3MF."
        )

    with zipfile.ZipFile(
        path,
        "r",
    ) as zf:

        model_names = [
            name
            for name in zf.namelist()
            if name.lower().endswith(
                ".model"
            )
        ]

        if not model_names:
            raise PrintabilityGateError(
                "3MF contains no .model XML."
            )

        parsed: dict[
            str,
            tuple[
                ET.Element,
                float,
            ],
        ] = {}

        def get_model(
            name: str,
        ) -> tuple[
            ET.Element,
            float,
        ]:
            name = posixpath.normpath(
                name.lstrip("/")
            )

            if name in parsed:
                return parsed[name]

            if name not in zf.namelist():
                raise PrintabilityGateError(
                    "Referenced 3MF model missing: "
                    + name
                )

            try:
                root = ET.fromstring(
                    zf.read(name)
                )
            except ET.ParseError as exc:
                raise PrintabilityGateError(
                    "Cannot parse 3MF model XML: "
                    + name
                ) from exc

            result = (
                root,
                _unit_scale_mm(root),
            )

            parsed[name] = result
            return result

        canonical = (
            "3D/3dmodel.model"
        )

        if canonical in model_names:
            root_model = canonical
        else:
            root_model = model_names[0]

            for candidate in model_names:
                root, _ = get_model(
                    candidate
                )

                if any(
                    _local_name(node.tag)
                    == "build"
                    for node in root.iter()
                ):
                    root_model = candidate
                    break

        vertices_parts: list[
            np.ndarray
        ] = []

        faces_parts: list[
            np.ndarray
        ] = []

        mesh_count = 0

        def object_by_id(
            model_root: ET.Element,
            object_id: str,
        ) -> ET.Element:

            for node in model_root.iter():
                if (
                    _local_name(node.tag)
                    == "object"
                    and node.attrib.get("id")
                    == object_id
                ):
                    return node

            raise PrintabilityGateError(
                "3MF object not found: "
                f"id={object_id}"
            )

        def resolve_object(
            model_name: str,
            object_id: str,
            world_transform: np.ndarray,
            stack: tuple[
                tuple[str, str],
                ...
            ],
        ) -> None:

            nonlocal mesh_count

            identity = (
                model_name,
                object_id,
            )

            if identity in stack:
                raise PrintabilityGateError(
                    "Cyclic 3MF component reference."
                )

            model_root, scale = get_model(
                model_name
            )

            obj = object_by_id(
                model_root,
                object_id,
            )

            mesh_node = None
            components_node = None

            for child in list(obj):
                name = _local_name(
                    child.tag
                )

                if name == "mesh":
                    mesh_node = child

                elif name == "components":
                    components_node = child

            if mesh_node is not None:
                vertices_node = None
                triangles_node = None

                for child in list(
                    mesh_node
                ):
                    name = _local_name(
                        child.tag
                    )

                    if name == "vertices":
                        vertices_node = child

                    elif name == "triangles":
                        triangles_node = child

                if (
                    vertices_node is None
                    or triangles_node is None
                ):
                    raise PrintabilityGateError(
                        "Incomplete 3MF mesh."
                    )

                verts = []

                for vertex in list(
                    vertices_node
                ):
                    if (
                        _local_name(
                            vertex.tag
                        )
                        != "vertex"
                    ):
                        continue

                    verts.append(
                        [
                            float(
                                vertex.attrib["x"]
                            )
                            * scale,
                            float(
                                vertex.attrib["y"]
                            )
                            * scale,
                            float(
                                vertex.attrib["z"]
                            )
                            * scale,
                        ]
                    )

                faces = []

                for triangle in list(
                    triangles_node
                ):
                    if (
                        _local_name(
                            triangle.tag
                        )
                        != "triangle"
                    ):
                        continue

                    faces.append(
                        [
                            int(
                                triangle.attrib["v1"]
                            ),
                            int(
                                triangle.attrib["v2"]
                            ),
                            int(
                                triangle.attrib["v3"]
                            ),
                        ]
                    )

                v = np.asarray(
                    verts,
                    dtype=float,
                )

                f = np.asarray(
                    faces,
                    dtype=np.int64,
                )

                if (
                    v.size == 0
                    or f.size == 0
                ):
                    raise PrintabilityGateError(
                        "Empty 3MF mesh."
                    )

                homogeneous = np.column_stack(
                    [
                        v,
                        np.ones(
                            len(v),
                            dtype=float,
                        ),
                    ]
                )

                transformed = (
                    world_transform
                    @ homogeneous.T
                ).T[:, :3]

                offset = sum(
                    len(part)
                    for part
                    in vertices_parts
                )

                vertices_parts.append(
                    transformed
                )

                faces_parts.append(
                    f + offset
                )

                mesh_count += 1

            if components_node is not None:
                for component in list(
                    components_node
                ):
                    if (
                        _local_name(
                            component.tag
                        )
                        != "component"
                    ):
                        continue

                    child_object_id = (
                        component.attrib.get(
                            "objectid"
                        )
                    )

                    if not child_object_id:
                        raise PrintabilityGateError(
                            "3MF component has no objectid."
                        )

                    child_model = (
                        _normalise_3mf_path(
                            model_name,
                            _attr_local(
                                component,
                                "path",
                            ),
                        )
                    )

                    local_transform = (
                        _transform_3mf(
                            component.attrib.get(
                                "transform"
                            ),
                            translation_scale=scale,
                        )
                    )

                    resolve_object(
                        child_model,
                        child_object_id,
                        world_transform
                        @ local_transform,
                        stack
                        + (identity,),
                    )

        root, root_scale = get_model(
            root_model
        )

        build_node = None

        for node in root.iter():
            if (
                _local_name(node.tag)
                == "build"
            ):
                build_node = node
                break

        build_item_count = 0

        if build_node is not None:
            for item in list(
                build_node
            ):
                if (
                    _local_name(
                        item.tag
                    )
                    != "item"
                ):
                    continue

                object_id = item.attrib.get(
                    "objectid"
                )

                if not object_id:
                    continue

                item_transform = (
                    _transform_3mf(
                        item.attrib.get(
                            "transform"
                        ),
                        translation_scale=
                            root_scale,
                    )
                )

                resolve_object(
                    root_model,
                    object_id,
                    item_transform,
                    (),
                )

                build_item_count += 1

        if build_item_count == 0:
            # Generic fallback for a valid 3MF
            # without an explicit build list.
            seen_ids = set()

            for node in root.iter():
                if (
                    _local_name(node.tag)
                    != "object"
                ):
                    continue

                object_id = (
                    node.attrib.get("id")
                )

                if (
                    not object_id
                    or object_id in seen_ids
                ):
                    continue

                seen_ids.add(
                    object_id
                )

                resolve_object(
                    root_model,
                    object_id,
                    np.eye(
                        4,
                        dtype=float,
                    ),
                    (),
                )

        if (
            not vertices_parts
            or not faces_parts
        ):
            raise PrintabilityGateError(
                "No printable mesh resolved "
                "from Bambu 3MF."
            )

        mesh = trimesh.Trimesh(
            vertices=np.vstack(
                vertices_parts
            ),
            faces=np.vstack(
                faces_parts
            ),
            process=False,
        )

        return (
            mesh,
            {
                "loader":
                    "bambu_3mf_xml",
                "root_model":
                    root_model,
                "build_item_count":
                    build_item_count,
                "mesh_count":
                    mesh_count,
            },
        )


def _load_geometry_mesh(
    path: Path,
) -> tuple[
    trimesh.Trimesh,
    dict[str, Any],
]:

    if path.suffix.lower() == ".3mf":
        try:
            return _load_bambu_3mf_mesh(
                path
            )
        except Exception as exc:
            if isinstance(
                exc,
                PrintabilityGateError,
            ):
                raise

            raise PrintabilityGateError(
                "Bambu 3MF geometry parse failed: "
                + repr(exc)
            ) from exc

    try:
        loaded = trimesh.load(
            str(path),
            force="scene",
            process=False,
        )
    except Exception as exc:
        raise PrintabilityGateError(
            "Cannot load geometry: "
            + repr(exc)
        ) from exc

    if isinstance(
        loaded,
        trimesh.Scene,
    ):
        if not loaded.geometry:
            raise PrintabilityGateError(
                "Geometry scene is empty."
            )

        mesh = loaded.dump(
            concatenate=True
        )

    elif isinstance(
        loaded,
        trimesh.Trimesh,
    ):
        mesh = loaded

    else:
        raise PrintabilityGateError(
            "Unsupported geometry type."
        )

    return (
        mesh,
        {
            "loader":
                "trimesh",
            "mesh_count":
                1,
        },
    )


def inspect_geometry(
    path: Path,
) -> dict[str, Any]:

    path = path.expanduser().resolve()

    if not path.is_file():
        raise PrintabilityGateError(
            f"Geometry missing: {path}"
        )

    mesh, provenance = (
        _load_geometry_mesh(
            path
        )
    )

    if (
        len(mesh.vertices) == 0
        or len(mesh.faces) == 0
    ):
        raise PrintabilityGateError(
            "Geometry contains no triangles."
        )

    bounds = np.asarray(
        mesh.bounds,
        dtype=float,
    )

    extents = (
        bounds[1]
        - bounds[0]
    )

    z_min = float(
        bounds[0, 2]
    )

    z_max = float(
        bounds[1, 2]
    )

    centers = np.asarray(
        mesh.triangles_center,
        dtype=float,
    )

    normals = np.asarray(
        mesh.face_normals,
        dtype=float,
    )

    areas = np.asarray(
        mesh.area_faces,
        dtype=float,
    )

    bed_tolerance_mm = 0.30

    # Actual candidate build-plate contact is
    # measured relative to absolute Z=0,
    # not relative to the mesh's own z_min.
    near_bed = (
        centers[:, 2]
        <= bed_tolerance_mm
    )

    contact_area_mm2 = float(
        areas[near_bed].sum()
    )

    xy_area = float(
        max(
            extents[0]
            * extents[1],
            0.0,
        )
    )

    minimum_contact_area_mm2 = max(
        0.5,
        xy_area * 0.0005,
    )

    # Down-facing surfaces away from the build
    # plate indicate likely overhang demand.
    overhang_mask = (
        (centers[:, 2] > 0.60)
        & (normals[:, 2] < -0.50)
    )

    overhang_area_mm2 = float(
        areas[
            overhang_mask
        ].sum()
    )

    surface_area_mm2 = float(
        mesh.area
    )

    overhang_threshold_mm2 = max(
        1.0,
        surface_area_mm2 * 0.002,
    )

    support_required = (
        overhang_area_mm2
        > overhang_threshold_mm2
    )

    blockers: list[str] = []

    if z_min < -0.05:
        blockers.append(
            "geometry_penetrates_build_plate"
        )

    if z_min > 0.35:
        blockers.append(
            "geometry_is_not_on_build_plate"
        )

    if (
        contact_area_mm2
        < minimum_contact_area_mm2
    ):
        blockers.append(
            "insufficient_build_plate_contact"
        )

    return {
        "path":
            str(path),

        "sha256":
            sha256_file(path),

        "loader":
            provenance,

        "bounds_mm":
            bounds.tolist(),

        "extents_mm":
            extents.tolist(),

        "z_min_mm":
            z_min,

        "z_max_mm":
            z_max,

        "bed_tolerance_mm":
            bed_tolerance_mm,

        "contact_area_mm2":
            contact_area_mm2,

        "minimum_contact_area_mm2":
            minimum_contact_area_mm2,

        "surface_area_mm2":
            surface_area_mm2,

        "overhang_area_mm2":
            overhang_area_mm2,

        "overhang_threshold_mm2":
            overhang_threshold_mm2,

        "support_required":
            support_required,

        "blockers":
            blockers,
    }


def inspect_gcode3mf(
    artifact: Path,
) -> dict[str, Any]:

    artifact = (
        artifact
        .expanduser()
        .resolve()
    )

    if not artifact.is_file():
        raise PrintabilityGateError(
            f"GCode3MF missing: {artifact}"
        )

    if not zipfile.is_zipfile(
        artifact
    ):
        raise PrintabilityGateError(
            "Artifact is not a valid ZIP/3MF."
        )

    with zipfile.ZipFile(
        artifact,
        "r",
    ) as zf:

        names = zf.namelist()

        settings_name = (
            "Metadata/"
            "project_settings.config"
        )

        if settings_name not in names:
            raise PrintabilityGateError(
                "project_settings.config missing."
            )

        settings = json.loads(
            zf.read(
                settings_name
            ).decode(
                "utf-8-sig"
            )
        )

        gcode_entries = [
            x
            for x in names
            if x.lower().endswith(
                ".gcode"
            )
        ]

        if len(gcode_entries) != 1:
            raise PrintabilityGateError(
                "Expected exactly one "
                "internal G-code."
            )

        gcode = zf.read(
            gcode_entries[0]
        ).decode(
            "utf-8",
            errors="replace",
        )

    support_info = (
        validate_support_settings(
            settings
        )
    )

    support_features = len(
        re.findall(
            r";\s*FEATURE:\s*Support",
            gcode,
            flags=re.IGNORECASE,
        )
    )

    bridge_features = len(
        re.findall(
            r";\s*FEATURE:\s*Bridge",
            gcode,
            flags=re.IGNORECASE,
        )
    )

    return {
        "path":
            str(artifact),
        "sha256":
            sha256_file(artifact),
        "curr_bed_type":
            settings.get(
                "curr_bed_type"
            ),
        "support":
            support_info,
        "support_feature_count":
            support_features,
        "bridge_feature_count":
            bridge_features,
        "blockers":
            list(
                support_info["blockers"]
            ),
    }


def build_report(
    *,
    artifact: Path,
    geometry_project: Path,
) -> dict[str, Any]:

    geometry = inspect_geometry(
        geometry_project
    )

    sliced = inspect_gcode3mf(
        artifact
    )

    blockers = (
        list(
            geometry["blockers"]
        )
        + list(
            sliced["blockers"]
        )
    )

    if (
        geometry["support_required"]
        and sliced[
            "support_feature_count"
        ] == 0
        and sliced[
            "bridge_feature_count"
        ] == 0
    ):
        blockers.append(
            "overhang_requires_support_but_"
            "no_support_or_bridge_toolpath_exists"
        )

    blockers = list(
        dict.fromkeys(
            blockers
        )
    )

    return {
        "schema_version":
            "1.0.0",
        "module":
            "M4",
        "stage":
            "final_printability_gate",
        "status":
            (
                "printability_gate_pass"
                if not blockers
                else
                "printability_gate_blocked"
            ),
        "artifact": {
            "path":
                sliced["path"],
            "sha256":
                sliced["sha256"],
            "curr_bed_type":
                sliced[
                    "curr_bed_type"
                ],
        },
        "geometry":
            geometry,
        "slicer": {
            "support":
                sliced["support"],
            "support_feature_count":
                sliced[
                    "support_feature_count"
                ],
            "bridge_feature_count":
                sliced[
                    "bridge_feature_count"
                ],
        },
        "blockers":
            blockers,
        "policy": {
            "network_used":
                False,
            "artifact_uploaded":
                False,
            "printer_command_sent":
                False,
            "print_started":
                False,
        },
    }


def cli_main(
    argv: list[str] | None = None,
) -> int:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--artifact",
        required=True,
    )

    parser.add_argument(
        "--geometry-project",
        required=True,
    )

    parser.add_argument(
        "--report",
        required=True,
    )

    args = parser.parse_args(
        argv
    )

    try:
        result = build_report(
            artifact=Path(
                args.artifact
            ),
            geometry_project=Path(
                args.geometry_project
            ),
        )

    except Exception as exc:
        result = {
            "schema_version":
                "1.0.0",
            "module":
                "M4",
            "stage":
                "final_printability_gate",
            "status":
                "printability_gate_blocked",
            "error":
                str(exc),
            "blockers": [
                "printability_gate_runtime_error"
            ],
            "policy": {
                "network_used":
                    False,
                "artifact_uploaded":
                    False,
                "printer_command_sent":
                    False,
                "print_started":
                    False,
            },
        }

    report = (
        Path(args.report)
        .expanduser()
        .resolve()
    )

    report.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    report.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )

    return (
        0
        if result["status"]
        == "printability_gate_pass"
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(
        cli_main()
    )
