from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SourceEvidence:
    """记录每个参数来自哪里，防止系统伪造工程数据。"""

    source_type: str
    source_text: str | None = None
    confidence: float | None = None
    confirmation_status: str = "unconfirmed"


@dataclass
class RequirementSpec:
    """自然语言解析后的正式增材制造工程需求。"""

    schema_version: str = "0.2.0"
    request_id: str | None = None
    status: str = "incomplete"

    original_input: dict[str, Any] = field(
        default_factory=lambda: {
            "input_type": "text",
            "raw_text": "",
        }
    )

    product: dict[str, Any] = field(
        default_factory=lambda: {
            "name": None,
            "functions": [],
            "application_environment": [],
        }
    )

    geometry: dict[str, Any] = field(
        default_factory=lambda: {
            "design_domain": None,
            "preserved_regions": [],
            "forbidden_regions": [],
            "interfaces": [],
            "symmetry": None,
            "reference_frame": "global_cartesian",
        }
    )

    materials: list[dict[str, Any]] = field(default_factory=list)

    boundary_conditions: dict[str, Any] = field(
        default_factory=lambda: {
            "fixed_regions": [],
            "loads": [],
            "thermal_conditions": [],
            "contacts": [],
        }
    )

    performance_requirements: dict[str, Any] = field(
        default_factory=lambda: {
            "maximum_stress": None,
            "maximum_displacement": None,
            "minimum_safety_factor": None,
            "target_mass": None,
        }
    )

    optimization: dict[str, Any] = field(
        default_factory=lambda: {
            "objectives": [],
            "hard_constraints": [],
            "soft_preferences": [],
        }
    )

    additive_manufacturing: dict[str, Any] = field(
        default_factory=lambda: {
            "process": None,
            "machine": None,
            "build_direction": None,
            "minimum_wall_thickness": None,
            "minimum_feature_size": None,
            "maximum_overhang": None,
            "support_policy": None,
            "material_removal": None,
        }
    )

    assumptions: list[dict[str, Any]] = field(default_factory=list)
    derived_constraints: list[dict[str, Any]] = field(default_factory=list)
    missing_information: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    clarification_questions: list[dict[str, Any]] = field(default_factory=list)
    source_evidence: list[dict[str, Any]] = field(default_factory=list)

    validation: dict[str, Any] = field(
        default_factory=lambda: {
            "schema_valid": False,
            "unit_valid": False,
            "engineering_complete": False,
            "manufacturing_feasible": None,
            "errors": [],
        }
    )

    approval: dict[str, Any] = field(
        default_factory=lambda: {
            "engineer_confirmed": False,
            "confirmed_by": None,
            "confirmed_at": None,
        }
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)