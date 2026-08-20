from __future__ import annotations

import html
import json
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import trimesh


def _load_mesh(path: str | Path) -> trimesh.Trimesh:
    loaded = trimesh.load(
        str(Path(path)),
        process=False,
    )

    if isinstance(loaded, trimesh.Scene):
        meshes = [
            g
            for g in loaded.geometry.values()
            if isinstance(g, trimesh.Trimesh)
        ]

        if not meshes:
            raise ValueError(
                "input scene contains no meshes"
            )

        loaded = trimesh.util.concatenate(
            meshes
        )

    if not isinstance(
        loaded,
        trimesh.Trimesh,
    ):
        raise TypeError(
            "input is not a triangle mesh"
        )

    if (
        len(loaded.vertices) == 0
        or len(loaded.faces) == 0
    ):
        raise ValueError(
            "input mesh is empty"
        )

    return loaded.copy()


def _escape(value: Any) -> str:
    return html.escape(
        str(value),
        quote=True,
    )


def _support_tracks(
    report: dict[str, Any],
    *,
    minimum_area_mm2: float,
) -> list[dict[str, Any]]:
    tracks = report.get(
        "tracks",
        [],
    )

    result = []

    for track in tracks:
        classes = track.get(
            "class_counts",
            {},
        )

        requires_support = (
            int(
                classes.get(
                    "SUPPORT_TOO_FAR",
                    0,
                )
            )
            > 0
            or int(
                classes.get(
                    "NO_LOCAL_SUPPORT",
                    0,
                )
            )
            > 0
        )

        if not requires_support:
            continue

        max_area = float(
            track.get(
                "max_single_layer_area_mm2",
                0.0,
            )
        )

        if max_area < minimum_area_mm2:
            continue

        bbox = track.get(
            "bbox_mm"
        )

        if (
            not isinstance(bbox, list)
            or len(bbox) != 4
        ):
            continue

        result.append(track)

    return result


def _make_enforcer_meshes(
    report: dict[str, Any],
    *,
    xy_margin_mm: float,
    minimum_area_mm2: float,
) -> list[tuple[str, trimesh.Trimesh]]:
    policy = report.get(
        "policy",
        {},
    )

    layer_h = float(
        policy.get(
            "measured_layer_height_mm",
            0.2,
        )
    )

    top_margin = max(
        0.6,
        layer_h * 3.0,
    )

    tracks = _support_tracks(
        report,
        minimum_area_mm2=(
            minimum_area_mm2
        ),
    )

    meshes = []

    for index, track in enumerate(
        tracks,
        start=1,
    ):
        x1, y1, x2, y2 = [
            float(v)
            for v in track["bbox_mm"]
        ]

        z_end = float(
            track.get(
                "z_end",
                track.get(
                    "z_start",
                    0.0,
                ),
            )
        )

        dx = max(
            1.2,
            (x2 - x1)
            + 2.0 * xy_margin_mm,
        )

        dy = max(
            1.2,
            (y2 - y1)
            + 2.0 * xy_margin_mm,
        )

        z_top = max(
            layer_h,
            z_end + top_margin,
        )

        cx = (
            x1 + x2
        ) / 2.0

        cy = (
            y1 + y2
        ) / 2.0

        transform = (
            trimesh.transformations
            .translation_matrix(
                [
                    cx,
                    cy,
                    z_top / 2.0,
                ]
            )
        )

        box = trimesh.creation.box(
            extents=[
                dx,
                dy,
                z_top,
            ],
            transform=transform,
        )

        meshes.append(
            (
                f"local_support_enforcer_{index:03d}",
                box,
            )
        )

    return meshes


def _fmt(value: float) -> str:
    return format(
        float(value),
        ".9g",
    )


def _build_geometry(
    model: trimesh.Trimesh,
    enforcers: list[
        tuple[str, trimesh.Trimesh]
    ],
):
    volumes = [
        (
            "model",
            "normal_part",
            model,
        )
    ]

    volumes.extend(
        (
            name,
            "support_enforcer",
            mesh,
        )
        for name, mesh
        in enforcers
    )

    all_vertices = []
    all_faces = []
    ranges = []

    vertex_offset = 0
    triangle_offset = 0

    for (
        name,
        volume_type,
        mesh,
    ) in volumes:
        vertices = np.asarray(
            mesh.vertices,
            dtype=float,
        )

        faces = np.asarray(
            mesh.faces,
            dtype=np.int64,
        )

        first_triangle = (
            triangle_offset
        )

        last_triangle = (
            triangle_offset
            + len(faces)
            - 1
        )

        all_vertices.append(
            vertices
        )

        all_faces.append(
            faces
            + vertex_offset
        )

        ranges.append({
            "name":
                name,

            "volume_type":
                volume_type,

            "first_triangle":
                first_triangle,

            "last_triangle":
                last_triangle,
        })

        vertex_offset += len(
            vertices
        )

        triangle_offset += len(
            faces
        )

    return (
        np.vstack(all_vertices),
        np.vstack(all_faces),
        ranges,
    )


def _model_xml(
    vertices: np.ndarray,
    faces: np.ndarray,
) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            '<model unit="millimeter" '
            'xml:lang="en-US" '
            'xmlns="http://schemas.microsoft.com/'
            '3dmanufacturing/core/2015/02">'
        ),
        (
            ' <metadata name="Application">'
            'PrusaSlicer-2.6.0'
            '</metadata>'
        ),
        ' <resources>',
        '  <object id="1" type="model">',
        '   <mesh>',
        '    <vertices>',
    ]

    for x, y, z in vertices:
        lines.append(
            '     <vertex '
            f'x="{_fmt(x)}" '
            f'y="{_fmt(y)}" '
            f'z="{_fmt(z)}"/>'
        )

    lines.extend([
        '    </vertices>',
        '    <triangles>',
    ])

    for a, b, c in faces:
        lines.append(
            '     <triangle '
            f'v1="{int(a)}" '
            f'v2="{int(b)}" '
            f'v3="{int(c)}"/>'
        )

    lines.extend([
        '    </triangles>',
        '   </mesh>',
        '  </object>',
        ' </resources>',
        ' <build>',
        (
            '  <item objectid="1" '
            'printable="1"/>'
        ),
        ' </build>',
        '</model>',
        '',
    ])

    return "\n".join(lines)


def _config_xml(
    ranges: list[dict[str, Any]],
) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<config>',
        (
            ' <object id="1" '
            'instances_count="1">'
        ),
        (
            '  <metadata type="object" '
            'key="name" '
            'value="NL-AM local support project"/>'
        ),
    ]

    for item in ranges:
        lines.append(
            '  <volume '
            f'firstid="{item["first_triangle"]}" '
            f'lastid="{item["last_triangle"]}">'
        )

        lines.append(
            '   <metadata type="volume" '
            'key="name" '
            f'value="{_escape(item["name"])}"/>'
        )

        lines.append(
            '   <metadata type="volume" '
            'key="volume_type" '
            f'value="{_escape(item["volume_type"])}"/>'
        )

        lines.append(
            '  </volume>'
        )

    lines.extend([
        ' </object>',
        '</config>',
        '',
    ])

    return "\n".join(lines)


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="config" ContentType="application/octet-stream"/>
</Types>
"""

_RELATIONSHIPS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""


def write_support_enforcer_project(
    *,
    model_path: str | Path,
    defect_tracks_path: str | Path,
    output_path: str | Path,
    xy_margin_mm: float = 0.8,
    minimum_area_mm2: float = 1.0,
) -> dict[str, Any]:
    model_path = Path(
        model_path
    ).resolve()

    defect_tracks_path = Path(
        defect_tracks_path
    ).resolve()

    output_path = Path(
        output_path
    ).resolve()

    model = _load_mesh(
        model_path
    )

    report = json.loads(
        defect_tracks_path.read_text(
            encoding="utf-8"
        )
    )

    enforcers = (
        _make_enforcer_meshes(
            report,
            xy_margin_mm=xy_margin_mm,
            minimum_area_mm2=(
                minimum_area_mm2
            ),
        )
    )

    if not enforcers:
        raise ValueError(
            "no support-enforcer tracks selected"
        )

    (
        vertices,
        faces,
        ranges,
    ) = _build_geometry(
        model,
        enforcers,
    )

    model_xml = _model_xml(
        vertices,
        faces,
    )

    config_xml = _config_xml(
        ranges
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with zipfile.ZipFile(
        output_path,
        "w",
        compression=(
            zipfile.ZIP_DEFLATED
        ),
    ) as archive:
        archive.writestr(
            "[Content_Types].xml",
            _CONTENT_TYPES,
        )

        archive.writestr(
            "_rels/.rels",
            _RELATIONSHIPS,
        )

        archive.writestr(
            "3D/3dmodel.model",
            model_xml,
        )

        archive.writestr(
            "Metadata/Slic3r_PE_model.config",
            config_xml,
        )

    return {
        "project":
            str(output_path),

        "model":
            str(model_path),

        "defect_tracks":
            str(defect_tracks_path),

        "enforcer_count":
            len(enforcers),

        "volume_count":
            len(ranges),

        "vertex_count":
            int(len(vertices)),

        "triangle_count":
            int(len(faces)),

        "xy_margin_mm":
            float(xy_margin_mm),

        "minimum_area_mm2":
            float(minimum_area_mm2),
    }
