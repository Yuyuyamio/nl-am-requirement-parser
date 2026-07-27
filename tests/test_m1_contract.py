import unittest
from typing import Any

from am_requirement_parser.m1_contract import (
    validate_m1_manifest,
)


def valid_manifest() -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "module": "M1",
        "original_input": (
            "给我打印一只坐着的卡通小狗，"
            "高度10厘米"
        ),
        "task_type": "creative_asset",
        "status": "ready",
        "route_file": (
            "C:/project/outputs/task_route.json"
        ),
        "output_file": (
            "C:/project/outputs/"
            "creative_asset_spec.json"
        ),
        "next_module": "M2",
    }


class M1ContractTests(
    unittest.TestCase
):
    def test_ready_manifest_is_valid(
        self,
    ) -> None:
        errors = validate_m1_manifest(
            valid_manifest()
        )

        self.assertEqual(
            errors,
            [],
        )

    def test_ready_manifest_requires_m2(
        self,
    ) -> None:
        manifest = valid_manifest()
        manifest["next_module"] = None

        errors = validate_m1_manifest(
            manifest
        )

        self.assertTrue(errors)

    def test_incomplete_manifest_cannot_enter_m2(
        self,
    ) -> None:
        manifest = valid_manifest()
        manifest["task_type"] = (
            "engineering_part"
        )
        manifest["status"] = "incomplete"
        manifest["next_module"] = "M2"

        errors = validate_m1_manifest(
            manifest
        )

        self.assertTrue(errors)

    def test_unknown_task_requires_clarification(
        self,
    ) -> None:
        manifest = valid_manifest()
        manifest["task_type"] = "unknown"
        manifest["status"] = "ready"
        manifest["next_module"] = "M2"

        errors = validate_m1_manifest(
            manifest
        )

        self.assertTrue(errors)

    def test_unknown_task_can_stop_in_m1(
        self,
    ) -> None:
        manifest = valid_manifest()
        manifest["task_type"] = "unknown"
        manifest["status"] = (
            "needs_clarification"
        )
        manifest["next_module"] = None

        errors = validate_m1_manifest(
            manifest
        )

        self.assertEqual(
            errors,
            [],
        )


if __name__ == "__main__":
    unittest.main()