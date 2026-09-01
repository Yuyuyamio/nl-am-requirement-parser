import unittest

from am_model_generator.providers.request_builder import (
    build_creative_generation_request,
)


class M2FdmPromptContractTests(
    unittest.TestCase
):

    def request(self):
        return {
            "task_type":
                "creative_asset",

            "route":
                "creative_mesh",

            "request_id":
                "M2-TEST",

            "source_payload_sha256":
                "a" * 64,

            "original_input":
                "print a 60 mm animal",
        }


    def source(self):
        return {
            "generation_prompt_en":
                "A stylized 60 mm animal figurine",

            "negative_prompt_en":
                None,

            "target_height_mm":
                60.0,

            "object_name":
                "animal",

            "visual_description":
                "stylized figurine",

            "style":
                None,

            "pose":
                None,

            "output_target":
                "3d_model",

            "system_printability_guidance": [
                (
                    "avoid unsupported "
                    "cantilevers"
                ),
            ],
        }


    def test_fdm_guidance_reaches_prompt(
        self,
    ):
        result = (
            build_creative_generation_request(
                m2_request=self.request(),
                source_spec=self.source(),
                provider_name="meshy",
            )
        )

        self.assertIn(
            "stylized 60 mm animal",
            result.prompt.lower(),
        )

        self.assertIn(
            "fdm constraints",
            result.prompt.lower(),
        )

        self.assertIn(
            "unsupported",
            result.prompt.lower(),
        )

        self.assertLessEqual(
            len(result.prompt),
            600,
        )


    def test_feedback_reaches_prompt(
        self,
    ):
        result = (
            build_creative_generation_request(
                m2_request=self.request(),
                source_spec=self.source(),
                provider_name="meshy",
                manufacturability_feedback={
                    "required_m2_changes": [
                        (
                            "reduce large "
                            "downward-facing surfaces"
                        ),
                    ]
                },
            )
        )

        self.assertIn(
            "downward-facing",
            result.prompt.lower(),
        )

        self.assertLessEqual(
            len(result.prompt),
            600,
        )


    def test_feedback_changes_idempotency(
        self,
    ):
        first = (
            build_creative_generation_request(
                m2_request=self.request(),
                source_spec=self.source(),
                provider_name="meshy",
            )
        )

        repaired = (
            build_creative_generation_request(
                m2_request=self.request(),
                source_spec=self.source(),
                provider_name="meshy",
                manufacturability_feedback={
                    "required_m2_changes": [
                        "reduce unsupported protrusions"
                    ]
                },
            )
        )

        self.assertNotEqual(
            first.idempotency_key,
            repaired.idempotency_key,
        )


if __name__ == "__main__":
    unittest.main()
