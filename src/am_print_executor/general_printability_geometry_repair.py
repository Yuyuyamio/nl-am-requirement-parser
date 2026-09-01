from __future__ import annotations

import hashlib

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import trimesh

from am_print_executor.printability_blocker_clustering import (
    BlockerCluster,
    cluster_gate_blockers,
)


@dataclass(frozen=True)
class RepairBudget:
    """Hard appearance/iteration budget for later GPR stages."""

    maximum_repair_rounds: int = 2
    maximum_candidates_per_round: int = 3
    maximum_modified_volume_ratio: float = 0.05
    maximum_bbox_dimension_change_ratio: float = 0.03
    maximum_height_change_ratio: float = 0.005
    maximum_surface_displacement_p95_mm: float = 1.5
    maximum_surface_displacement_mm: float = 3.0
    preserve_flat_base: bool = True
    preserve_critical_regions: bool = True
    additive_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PrintabilityRepairRequest:
    """Stable input contract for generic printability geometry repair."""

    schema_version: str
    module: str
    repair_mode: str

    source_geometry: str
    source_geometry_sha256: str
    source_bounds_mm: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
    ]
    source_extents_mm: tuple[
        float,
        float,
        float,
    ]
    target_height_mm: float

    slicer: str

    gate_status: str
    gate_summary: dict[str, Any]
    gate_policy: dict[str, Any]

    protected_base: dict[str, Any]
    critical_regions: tuple[
        dict[str, Any],
        ...
    ]

    blocker_clusters: tuple[
        BlockerCluster,
        ...
    ]

    repair_budget: RepairBudget

    forbidden_operations: tuple[
        str,
        ...
    ]

    coordinate_mapping: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)

        data[
            "blocker_clusters"
        ] = [
            cluster.to_dict()
            for cluster
            in self.blocker_clusters
        ]

        data[
            "repair_budget"
        ] = self.repair_budget.to_dict()

        return data


def _sha256(
    path: Path,
) -> str:
    digest = hashlib.sha256()

    with path.open(
        "rb"
    ) as handle:
        for block in iter(
            lambda:
                handle.read(
                    1024 * 1024
                ),
            b"",
        ):
            digest.update(
                block
            )

    return digest.hexdigest()


def _load_mesh_summary(
    path: Path,
) -> tuple[
    tuple[
        tuple[float, float, float],
        tuple[float, float, float],
    ],
    tuple[
        float,
        float,
        float,
    ],
]:
    loaded = trimesh.load(
        path,
        force="mesh",
        process=False,
    )

    if isinstance(
        loaded,
        trimesh.Scene,
    ):
        meshes = [
            item
            for item
            in loaded.geometry.values()
            if (
                isinstance(
                    item,
                    trimesh.Trimesh,
                )
                and len(
                    item.faces
                )
            )
        ]

        if not meshes:
            raise ValueError(
                "source geometry contains no triangle mesh"
            )

        loaded = (
            trimesh.util.concatenate(
                meshes
            )
        )

    if not isinstance(
        loaded,
        trimesh.Trimesh,
    ):
        raise ValueError(
            "source geometry is not a triangle mesh"
        )

    bounds = loaded.bounds
    extents = loaded.extents

    return (
        (
            (
                float(bounds[0][0]),
                float(bounds[0][1]),
                float(bounds[0][2]),
            ),
            (
                float(bounds[1][0]),
                float(bounds[1][1]),
                float(bounds[1][2]),
            ),
        ),
        (
            float(extents[0]),
            float(extents[1]),
            float(extents[2]),
        ),
    )


def _flat_base_contract(
    report: Mapping[
        str,
        Any,
    ]
    | None,
) -> dict[str, Any]:
    if report is None:
        return {
            "required": True,
            "verified": False,
            "status": "not_supplied",
        }

    verified = bool(
        report.get(
            "base_flatness_passed"
        )
        is True
        or report.get(
            "status"
        )
        == "pass"
    )

    result: dict[
        str,
        Any,
    ] = {
        "required": True,
        "verified": verified,
        "status": (
            "pass"
            if verified
            else "unverified_or_blocked"
        ),
    }

    for key in (
        "bed_contact_area_mm2",
        "largest_contact_patch_area_mm2",
        "contact_patch_count",
        "maximum_plane_deviation_mm",
        "base_height_range_mm",
        "plane_tilt_degrees",
    ):
        if key in report:
            result[key] = report[
                key
            ]

    return result


def build_printability_repair_request(
    *,
    source_geometry: Path,
    gate_report: Mapping[
        str,
        Any,
    ],
    flat_base_report: Mapping[
        str,
        Any,
    ]
    | None = None,
    slicer: str = "bambu",
    target_height_mm: float | None = None,
    critical_regions: Sequence[
        Mapping[
            str,
            Any,
        ]
    ] = (),
    repair_budget: RepairBudget | None = None,
    sliced_artifact: Path | None = None,
) -> PrintabilityRepairRequest:
    """Build a generic GPR request without modifying the mesh."""

    source_geometry = Path(
        source_geometry
    ).resolve()

    if not source_geometry.is_file():
        raise FileNotFoundError(
            source_geometry
        )

    artifact = sliced_artifact or gate_report.get("artifact")
    if artifact:
        from am_print_executor.slice_geometry_frame import (
            gate_report_in_geometry_frame, verify_source_matches_slice,
        )
        artifact = Path(artifact).resolve()
        source_mesh = trimesh.load(source_geometry, force="mesh", process=True)
        verify_source_matches_slice(source_mesh, artifact)
        if not gate_report.get("coordinate_mapping", {}).get("applied"):
            gate_report = gate_report_in_geometry_frame(dict(gate_report), artifact)

    bounds, extents = (
        _load_mesh_summary(
            source_geometry
        )
    )

    resolved_target_height = (
        float(
            target_height_mm
        )
        if target_height_mm
        is not None
        else float(
            extents[2]
        )
    )

    if resolved_target_height <= 0.0:
        raise ValueError(
            "target_height_mm must be positive"
        )

    clusters = tuple(
        cluster_gate_blockers(
            gate_report
        )
    )

    blockers = (
        gate_report.get(
            "blockers"
        )
        or []
    )

    dangerous_layers = (
        gate_report.get(
            "dangerous_layers"
        )
        or []
    )

    summary = {
        "dangerous_layer_count":
            int(
                gate_report.get(
                    "dangerous_layer_count",
                    len(
                        dangerous_layers
                    ),
                )
                or 0
            ),

        "blocker_count":
            len(
                blockers
            ),

        "total_bad_area_mm2":
            float(
                gate_report.get(
                    "total_bad_area_mm2",
                    0.0,
                )
                or 0.0
            ),

        "worst_bad_area_mm2":
            float(
                gate_report.get(
                    "worst_bad_area_mm2",
                    0.0,
                )
                or 0.0
            ),

        "longest_bad_bridge_mm":
            float(
                gate_report.get(
                    "longest_bad_bridge_mm",
                    0.0,
                )
                or 0.0
            ),

        "cluster_count":
            len(
                clusters
            ),
    }

    budget = (
        repair_budget
        if repair_budget
        is not None
        else RepairBudget()
    )

    critical = tuple(
        dict(
            item
        )
        for item
        in critical_regions
    )

    return PrintabilityRepairRequest(
        schema_version="gpr-v1.1",
        module=(
            "GENERAL_PRINTABILITY_GEOMETRY_REPAIR"
        ),
        repair_mode=(
            "deterministic_local_additive"
        ),
        source_geometry=str(
            source_geometry
        ),
        source_geometry_sha256=(
            _sha256(
                source_geometry
            )
        ),
        source_bounds_mm=bounds,
        source_extents_mm=extents,
        target_height_mm=(
            resolved_target_height
        ),
        slicer=str(
            slicer
        ).strip().lower(),
        gate_status=str(
            gate_report.get(
                "status",
                "unknown",
            )
        ),
        gate_summary=summary,
        gate_policy=dict(
            gate_report.get(
                "policy"
            )
            or {}
        ),
        protected_base=(
            _flat_base_contract(
                flat_base_report
            )
        ),
        critical_regions=critical,
        blocker_clusters=clusters,
        repair_budget=budget,
        coordinate_mapping=dict(gate_report.get("coordinate_mapping") or {}),
        forbidden_operations=(
            "global_voxel_surface_rebuild",
            "global_marching_cubes_reconstruction",
            "subtractive_overhang_cutter",
            "protected_base_modification",
            "critical_region_fill",
            "sample_specific_coordinate_patch",
            "gate_threshold_relaxation_as_repair",
        ),
    )


def plan_geometry_repair_candidates(
    request: PrintabilityRepairRequest,
    *,
    maximum_clusters: int | None = None,
) -> list[dict[str, Any]]:
    """Plan GPR-V1.2 local additive envelopes without mesh mutation."""

    from am_print_executor.local_self_support_envelope import (
        plan_local_self_support_envelopes,
    )

    if request.gate_status != "blocked":
        return []

    return [
        plan.to_dict()
        for plan in plan_local_self_support_envelopes(
            request,
            maximum_clusters=maximum_clusters,
        )
    ]


def build_boolean_union_candidate(
    request: PrintabilityRepairRequest,
    plan: Any,
    *,
    output_directory: Path,
) -> dict[str, Any]:
    """Build and validate a V1.3 candidate; never slice or modify the source.

    Only fidelity-PASS STLs may be written, exclusively under the validation
    output root. A geometry PASS is not a printability PASS.
    """
    import io

    from am_print_executor.geometry_fidelity_gate import (
        inspect_geometry_fidelity, mesh_validation, normalized_boolean_copy,
    )
    from am_print_executor.local_self_support_envelope import (
        EnvelopeCandidatePlan, build_envelope_mesh,
    )

    source_path = Path(request.source_geometry).resolve()
    output_directory = Path(output_directory).resolve()
    allowed_root = Path(__file__).resolve().parents[2] / "outputs" / "gpr_v1_3_validation"
    if not output_directory.is_relative_to(allowed_root.resolve()):
        raise ValueError("V1.3 output must be under outputs/gpr_v1_3_validation")
    result: dict[str, Any] = {
        "schema_version": "gpr-v1.3", "status": "blocked", "blockers": [],
        "source_geometry": str(source_path), "candidate_geometry": None,
        "source_sha_before": _sha256(source_path), "source_mutation": 0,
        "operation": "manifold_union", "slicing_runs": 0, "auto_orient_runs": 0,
        "printability_status": "not_evaluated",
    }
    if result["source_sha_before"] != request.source_geometry_sha256:
        result["blockers"] = ["source_sha_changed_since_request"]
        return result
    if request.gate_status != "blocked":
        result["status"] = "not_needed"
        return result
    if isinstance(plan, Mapping):
        plan = EnvelopeCandidatePlan(**plan)
    if not isinstance(plan, EnvelopeCandidatePlan) or plan.status != "candidate":
        result["blockers"] = ["plan_not_candidate"]
        return result
    if plan.cluster_id not in {cluster.cluster_id for cluster in request.blocker_clusters}:
        result["blockers"] = ["plan_cluster_not_in_request"]
        return result
    # Do not permit a persisted/stale/tampered plan to smuggle in arbitrary
    # geometry or to bypass V1.2's anchor and locality checks.
    from am_print_executor.local_self_support_envelope import plan_local_self_support_envelopes
    valid_plans = plan_local_self_support_envelopes(request)
    import json
    plan_key = json.dumps(plan.to_dict(), sort_keys=True)
    if plan_key not in {json.dumps(item.to_dict(), sort_keys=True) for item in valid_plans}:
        result["blockers"] = ["plan_does_not_match_source_and_blockers"]
        return result
    result["plan"] = plan.to_dict()
    destination = output_directory / f"{plan.cluster_id}_{plan.strength}.stl"
    if destination.resolve() == source_path:
        raise ValueError("candidate must not overwrite source geometry")
    try:
        loaded = trimesh.load(source_path, force="mesh", process=False)
        source = normalized_boolean_copy(loaded)
        result["topology_normalization"] = {
            "method": "exact_coincident_vertex_weld_on_working_copy",
            "vertices_before": len(loaded.vertices), "vertices_after": len(source.vertices),
            "faces_before": len(loaded.faces), "faces_after": len(source.faces),
        }
        source_check = mesh_validation(source)
        result["source_topology"] = source_check
        if not source_check["valid"] or source_check["component_count"] != 1:
            result["blockers"] = ["source_topology_requires_regeneration"]
            return result
        envelope = build_envelope_mesh(plan)
        candidate = trimesh.boolean.union([source.copy(), envelope.copy()], engine="manifold", check_volume=True)
        candidate_check = mesh_validation(candidate)
        result["union_topology"] = candidate_check
        if not candidate_check["valid"]:
            result["blockers"] = ["invalid_boolean_union_result"]
            return result
        # Validate exactly the serialized geometry that the next stage would
        # read. No failed candidate STL is written, even temporarily.
        payload = candidate.export(file_type="stl")
        serialized = trimesh.load(io.BytesIO(payload), file_type="stl", force="mesh", process=False)
        fidelity = inspect_geometry_fidelity(
            source=source, candidate=serialized, repair_volume=envelope,
            budget=request.repair_budget,
            protected_base_clearance_z_mm=plan.protected_base_clearance_z_mm,
            critical_regions=request.critical_regions,
        )
        result["fidelity"] = fidelity
        if fidelity["status"] != "pass":
            result["blockers"] = fidelity["blockers"]
            return result
        if _sha256(source_path) != request.source_geometry_sha256:
            result["blockers"] = ["source_changed_during_validation"]
            return result
        output_directory.mkdir(parents=True, exist_ok=True)
        # Exclusive creation also prevents accidental overwrite on retries.
        with destination.open("xb") as handle:
            handle.write(payload)
        result["status"] = "pass"
        result["candidate_geometry"] = str(destination)
        result["candidate_sha256"] = hashlib.sha256(payload).hexdigest()
    except Exception as exc:
        result["blockers"] = ["candidate_build_failed"]
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["source_sha_after"] = _sha256(source_path)
        if result["source_sha_after"] != result["source_sha_before"]:
            result["status"] = "blocked"
            result["source_mutation"] = 1
            result["blockers"].append("source_sha_changed")
    return result


def repair_geometry(
    request: PrintabilityRepairRequest,
) -> None:
    """Reserved for the separately approved V1.4 reslice/feedback loop."""

    raise NotImplementedError(
        "Use build_boolean_union_candidate for V1.3 validation; V1.4 is not enabled."
    )
