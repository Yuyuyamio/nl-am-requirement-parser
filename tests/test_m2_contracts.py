from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from am_model_generator.contracts import (
    LoadedM1Input,
    M2InputError,
    M2Route,
    build_m2_request,
    build_planned_m2_manifest,
    validate_m2_manifest,
    validate_m2_request,
    validate_m2_route,
)
from am_model_generator.routing import (
    build_m2_route,
)


def _loaded_input(
    *,
    task_type: str,
    payload: dict[str, Any],
) -> LoadedM1Input:
    status = (
        "ready"
        if task_type == "creative_asset"
        else "complete"
    )

    output_file = (
        "creative_asset_spec.json"
        if task_type == "creative_asset"
        else "requirement_spec.json"
    )

    return LoadedM1Input(
        manifest_path=Path(
            "outputs/m1/m1_manifest.json"
        ).resolve(),
        manifest={
            "schema_version": "0.1.0",
            "module": "M1",
            "original_input": "test input",
            "task_type": task_type,
            "status": status,
            "route_file": "task_route.json",
            "output_file": output_file,
            "next_module": "M2",
        },
        payload_path=Path(
            f"outputs/m1/{output_file}"
        ).resolve(),
        payload=payload,
        task_type=task_type,
    )


class TestM2Contracts(unittest.TestCase):
    def test_creative_contracts_are_valid(
        self,
    ) -> None:
        loaded = _loaded_input(
            task_type="creative_asset",
            payload={
                "schema_version": "0.1.0",
                "task_type": "creative_asset",
                "object_name": "cartoon puppy",
                "target_height_mm": 100.0,
            },
        )

        route = build_m2_route(
            loaded
        )

        request = build_m2_request(
            loaded,
            route,
        )

        manifest = (
            build_planned_m2_manifest(
                loaded,
                route,
            )
        )

        self.assertEqual(
            validate_m2_route(
                route.to_dict()
            ),
            [],
        )

        self.assertEqual(
            validate_m2_request(
                request.to_dict()
            ),
            [],
        )

        self.assertEqual(
            validate_m2_manifest(
                manifest.to_dict()
            ),
            [],
        )

    def test_engineering_contracts_are_valid(
        self,
    ) -> None:
        loaded = _loaded_input(
            task_type="engineering_part",
            payload={
                "schema_version": "0.2.0",
                "request_id": (
                    "REQ-ENGINEERING-001"
                ),
                "status": "complete",
            },
        )

        route = build_m2_route(
            loaded
        )

        request = build_m2_request(
            loaded,
            route,
        )

        manifest = (
            build_planned_m2_manifest(
                loaded,
                route,
            )
        )

        self.assertEqual(
            request.request_id,
            "REQ-ENGINEERING-001",
        )

        self.assertEqual(
            request.preferred_provider,
            "cadquery",
        )

        self.assertEqual(
            validate_m2_route(
                route.to_dict()
            ),
            [],
        )

        self.assertEqual(
            validate_m2_request(
                request.to_dict()
            ),
            [],
        )

        self.assertEqual(
            validate_m2_manifest(
                manifest.to_dict()
            ),
            [],
        )

    def test_payload_hash_is_stable(
        self,
    ) -> None:
        first_loaded = _loaded_input(
            task_type="creative_asset",
            payload={
                "object_name": "puppy",
                "target_height_mm": 100.0,
            },
        )

        second_loaded = _loaded_input(
            task_type="creative_asset",
            payload={
                "target_height_mm": 100.0,
                "object_name": "puppy",
            },
        )

        first_request = build_m2_request(
            first_loaded,
            build_m2_route(first_loaded),
        )

        second_request = build_m2_request(
            second_loaded,
            build_m2_route(second_loaded),
        )

        self.assertEqual(
            first_request.source_payload_sha256,
            second_request.source_payload_sha256,
        )

    def test_route_schema_rejects_mismatch(
        self,
    ) -> None:
        loaded = _loaded_input(
            task_type="creative_asset",
            payload={
                "object_name": "puppy",
            },
        )

        route_data = build_m2_route(
            loaded
        ).to_dict()

        route_data["route"] = (
            "parametric_cad"
        )

        self.assertTrue(
            validate_m2_route(
                route_data
            )
        )

    def test_planned_manifest_rejects_model(
        self,
    ) -> None:
        loaded = _loaded_input(
            task_type="creative_asset",
            payload={
                "object_name": "puppy",
            },
        )

        route = build_m2_route(
            loaded
        )

        manifest_data = (
            build_planned_m2_manifest(
                loaded,
                route,
            ).to_dict()
        )

        manifest_data[
            "primary_model"
        ] = "model.stl"

        self.assertTrue(
            validate_m2_manifest(
                manifest_data
            )
        )

    def test_complete_manifest_can_enter_m3(
        self,
    ) -> None:
        loaded = _loaded_input(
            task_type="creative_asset",
            payload={
                "object_name": "puppy",
            },
        )

        route = build_m2_route(
            loaded
        )

        manifest_data = (
            build_planned_m2_manifest(
                loaded,
                route,
            ).to_dict()
        )

        manifest_data.update(
            {
                "status": "complete",
                "provider": "meshy",
                "primary_model": (
                    "manufacturing_model.3mf"
                ),
                "validation_file": (
                    "geometry_validation.json"
                ),
                "hard_constraints_passed": True,
                "next_module": "M3",
            }
        )

        self.assertEqual(
            validate_m2_manifest(
                manifest_data
            ),
            [],
        )

    def test_builder_rejects_route_mismatch(
        self,
    ) -> None:
        loaded = _loaded_input(
            task_type="creative_asset",
            payload={
                "object_name": "puppy",
            },
        )

        invalid_route = M2Route(
            schema_version="0.1.0",
            request_id="REQ-WRONG",
            task_type="engineering_part",
            route="parametric_cad",
            generator_family="cadquery",
            preferred_provider="cadquery",
            reason_code=(
                "ENGINEERING_PART_ROUTE"
            ),
            ready_for_generation=True,
        )

        with self.assertRaises(
            M2InputError
        ) as context:
            build_m2_request(
                loaded,
                invalid_route,
            )

        self.assertEqual(
            context.exception.code,
            "M2_ROUTE_INPUT_MISMATCH",
        )


if __name__ == "__main__":
    unittest.main()