from __future__ import annotations

import unittest

from am_print_executor.r10_final_preflight import (
    extract_trays,
    normalize_colour,
)


class TestR10FinalPreflight(
    unittest.TestCase
):
    def test_colour_rgba_normalization(
        self,
    ):
        self.assertEqual(
            normalize_colour(
                "A6A9AAFF"
            ),
            "#A6A9AA",
        )

    def test_extract_two_ams_trays(
        self,
    ):
        sample = {
            "ams": {
                "ams": [
                    {
                        "id": "0",
                        "tray": [
                            {
                                "id": "0",
                                "tray_type": "PLA",
                                "tray_color":
                                    "A6A9AAFF",
                                "remain": 97,
                            },
                            {
                                "id": "3",
                                "tray_type": "PLA",
                                "tray_color":
                                    "F4EE2AFF",
                                "remain": -1,
                            },
                        ],
                    }
                ]
            }
        }

        rows = extract_trays(
            sample
        )

        actual = {
            (
                row["ams_id"],
                row["tray_id"],
                row["tray_type"],
                row["tray_color"],
            )
            for row in rows
        }

        self.assertIn(
            (
                "0",
                "0",
                "PLA",
                "#A6A9AA",
            ),
            actual,
        )

        self.assertIn(
            (
                "0",
                "3",
                "PLA",
                "#F4EE2A",
            ),
            actual,
        )

    def test_external_spool_does_not_override_ams(self):
        sample = {
            "ams": {
                "ams": [
                    {
                        "id": "0",
                        "tray": [
                            {
                                "id": "0",
                                "tray_type": "PLA",
                                "tray_color":
                                    "A6A9AAFF",
                            }
                        ],
                    }
                ],
                "tray_now": "255",
            }
        }

        rows = extract_trays(
            sample
        )

        self.assertTrue(
            any(
                row["ams_id"] == "0"
                and row["tray_id"]
                == "0"
                for row in rows
            )
        )


if __name__ == "__main__":
    unittest.main(
        verbosity=2
    )
