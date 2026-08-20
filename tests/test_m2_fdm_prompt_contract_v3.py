from __future__ import annotations

import unittest

from am_model_generator.providers.request_builder import (
    build_creative_generation_request,
)


class FdmPromptContractV3Tests(
    unittest.TestCase
):
    def _m2(self):
        return {
            "task_type": "creative_asset",
            "route": "creative_mesh",
            "request_id": "M2-TEST",
            "source_payload_sha256": "a" * 64,
            "original_input": "test",
        }

    def _source(self):
        return {
            "generation_prompt_en":
                "Create a small standing animal figurine with recognisable body, head, legs and tail.",

            "negative_prompt_en":
                None,

            "target_height_mm":
                60.0,

            "object_name":
                "animal",

            "system_printability_guidance": [
                "Prefer an easy-to-manufacture pose."
            ],
        }

    def test_triposg_keeps_full_fdm_contract(self):
        feedback = {
            "status":
                "needs_geometry_regeneration",

            "requested_geometry_changes": [
                "Avoid unsupported protrusions.",
                "Make the base stable.",
            ],
        }

        request = (
            build_creative_generation_request(
                m2_request=self._m2(),
                source_spec=self._source(),
                provider_name="triposg_local",
                manufacturability_feedback=feedback,
            )
        )

        lowered = request.prompt.lower()

        for term in (
            "fdm",
            "printable",
            "watertight",
            "continuous",
            "cantilever",
            "appendages",
            "overhang",
        ):
            self.assertIn(
                term,
                lowered,
            )

        self.assertLessEqual(
            len(request.prompt),
            1800,
        )

    def test_meshy_stays_within_600(self):
        request = (
            build_creative_generation_request(
                m2_request=self._m2(),
                source_spec=self._source(),
                provider_name="meshy",
                manufacturability_feedback={
                    "status":
                        "needs_geometry_regeneration",
                    "requested_geometry_changes":
                        ["x" * 1000],
                },
            )
        )

        self.assertLessEqual(
            len(request.prompt),
            600,
        )

        lowered = request.prompt.lower()

        self.assertIn(
            "fdm",
            lowered,
        )
        self.assertIn(
            "printable",
            lowered,
        )
        self.assertIn(
            "cantilever",
            lowered,
        )

    def test_feedback_changes_idempotency(self):
        first = (
            build_creative_generation_request(
                m2_request=self._m2(),
                source_spec=self._source(),
                provider_name="triposg_local",
                manufacturability_feedback={
                    "status":
                        "needs_geometry_regeneration",
                    "requested_geometry_changes":
                        ["repair A"],
                },
            )
        )

        second = (
            build_creative_generation_request(
                m2_request=self._m2(),
                source_spec=self._source(),
                provider_name="triposg_local",
                manufacturability_feedback={
                    "status":
                        "needs_geometry_regeneration",
                    "requested_geometry_changes":
                        ["repair B"],
                },
            )
        )

        self.assertNotEqual(
            first.idempotency_key,
            second.idempotency_key,
        )


if __name__ == "__main__":
    unittest.main()
