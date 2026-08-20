from __future__ import annotations

import unittest

from am_print_executor.r11_real_start import (
    normalize_sequence_ids,
    replace_sequence_ids,
    target_is_zero,
)


class TestR11RealStart(
    unittest.TestCase
):

    def test_only_sequence_changes(
        self,
    ):

        original = {
            "print": {
                "command":
                    "project_file",

                "sequence_id":
                    "0",

                "param":
                    "Metadata/plate_1.gcode",
            }
        }

        runtime, count = (
            replace_sequence_ids(
                original,
                "123456789",
            )
        )

        self.assertEqual(
            count,
            1,
        )

        self.assertEqual(
            runtime["print"][
                "sequence_id"
            ],
            "123456789",
        )

        self.assertEqual(
            normalize_sequence_ids(
                runtime
            ),
            normalize_sequence_ids(
                original
            ),
        )

    def test_temperature_target_zero(
        self,
    ):

        self.assertTrue(
            target_is_zero(0)
        )

        self.assertTrue(
            target_is_zero("0")
        )

        self.assertTrue(
            target_is_zero(None)
        )

        self.assertFalse(
            target_is_zero(60)
        )


if __name__ == "__main__":

    unittest.main(
        verbosity=2
    )
