from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from am_model_generator.contracts import (
    LoadedM1Input,
)
from am_model_generator.routing import (
    build_m2_route,
)


def _loaded_input(
    *,
    task_type: str,
    payload: dict[str, Any],
) -> LoadedM1Input:
    return LoadedM1Input(
        manifest_path=Path(
            "m1_manifest.json"
        ),
        manifest={
            "schema_version": "0.1.0",
            "module": "M1",
            "original_input": "test input",
            "task_type": task_type,
            "status": (
                "ready"
                if task_type
                == "creative_asset"
                else "complete"
            ),
            "route_file": (
                "task_route.json"
            ),
            "output_file": (
                "creative_asset_spec.json"
                if task_type
                == "creative_asset"
                else "requirement_spec.json"
            ),
            "next_module": "M2",
        },
        payload_path=Path(
            "payload.json"
        ),
        payload=payload,
        task_type=task_type,
    )


class TestM2Router(unittest.TestCase):
    def test_routes_creative_asset(
        self,
    ) -> None:
        loaded = _loaded_input(
            task_type="creative_asset",
            payload={
                "schema_version": "0.1.0",
                "task_type": (
                    "creative_asset"
                ),
                "object_name": (
                    "cartoon puppy"
                ),
                "target_height_mm": 100.0,
            },
        )

        route = build_m2_route(
            loaded
        )

        self.assertEqual(
            route.task_type,
            "creative_asset",
        )

        self.assertEqual(
            route.route,
            "creative_mesh",
        )

        self.assertEqual(
            route.generator_family,
            "generation_provider",
        )

        self.assertIsNone(
            route.preferred_provider
        )

        self.assertEqual(
            route.reason_code,
            "CREATIVE_ASSET_ROUTE",
        )

        self.assertTrue(
            route.ready_for_generation
        )

        self.assertTrue(
            route.request_id.startswith(
                "M2-"
            )
        )

    def test_routes_engineering_part(
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

        self.assertEqual(
            route.request_id,
            "REQ-ENGINEERING-001",
        )

        self.assertEqual(
            route.task_type,
            "engineering_part",
        )

        self.assertEqual(
            route.route,
            "parametric_cad",
        )

        self.assertEqual(
            route.generator_family,
            "cadquery",
        )

        self.assertEqual(
            route.preferred_provider,
            "cadquery",
        )

        self.assertEqual(
            route.reason_code,
            "ENGINEERING_PART_ROUTE",
        )

        self.assertTrue(
            route.ready_for_generation
        )

    def test_creative_request_id_is_stable(
        self,
    ) -> None:
        payload = {
            "schema_version": "0.1.0",
            "task_type": "creative_asset",
            "object_name": "cartoon puppy",
            "target_height_mm": 100.0,
        }

        first_loaded = _loaded_input(
            task_type="creative_asset",
            payload=payload,
        )

        second_loaded = _loaded_input(
            task_type="creative_asset",
            payload=dict(payload),
        )

        first_route = build_m2_route(
            first_loaded
        )

        second_route = build_m2_route(
            second_loaded
        )

        self.assertEqual(
            first_route.request_id,
            second_route.request_id,
        )

    def test_different_payloads_get_different_ids(
        self,
    ) -> None:
        first_loaded = _loaded_input(
            task_type="creative_asset",
            payload={
                "task_type": (
                    "creative_asset"
                ),
                "object_name": "puppy",
                "target_height_mm": 100.0,
            },
        )

        second_loaded = _loaded_input(
            task_type="creative_asset",
            payload={
                "task_type": (
                    "creative_asset"
                ),
                "object_name": "puppy",
                "target_height_mm": 120.0,
            },
        )

        first_route = build_m2_route(
            first_loaded
        )

        second_route = build_m2_route(
            second_loaded
        )

        self.assertNotEqual(
            first_route.request_id,
            second_route.request_id,
        )

    def test_route_can_be_serialized(
        self,
    ) -> None:
        loaded = _loaded_input(
            task_type="engineering_part",
            payload={
                "request_id": "REQ-001",
                "status": "complete",
            },
        )

        route_data = build_m2_route(
            loaded
        ).to_dict()

        self.assertEqual(
            route_data[
                "schema_version"
            ],
            "0.1.0",
        )

        self.assertEqual(
            route_data["route"],
            "parametric_cad",
        )

        self.assertIn(
            "ready_for_generation",
            route_data,
        )


if __name__ == "__main__":
    unittest.main()