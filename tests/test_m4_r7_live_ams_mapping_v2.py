from __future__ import annotations

import unittest

from am_print_executor.r7_live_ams_mapping_v2 import (
    extract_ams_trays,
    match_material,
    normalize_color,
)


class R7LiveAMSMappingV2Tests(unittest.TestCase):
    def test_normalize_rgba_colour(self):
        self.assertEqual(
            normalize_color("A6A9AAFF"),
            "#A6A9AA",
        )

    def test_extract_trays(self):
        pobj = {
            "ams": {
                "ams": [
                    {
                        "id": "0",
                        "tray": [
                            {
                                "id": "0",
                                "tray_color": "A6A9AAFF",
                                "tray_type": "PLA",
                            },
                            {
                                "id": "3",
                                "tray_color": "F4EE2AFF",
                                "tray_type": "PLA",
                            },
                        ],
                    }
                ]
            }
        }

        rows = extract_ams_trays(pobj)
        self.assertEqual(rows[0]["global_tray_id"], 0)
        self.assertEqual(rows[1]["global_tray_id"], 3)

    def test_unique_gray_match(self):
        trays = [
            {
                "global_tray_id": 0,
                "tray_color": "#A6A9AA",
                "tray_type": "PLA",
            },
            {
                "global_tray_id": 3,
                "tray_color": "#F4EE2A",
                "tray_type": "PLA",
            },
        ]

        result = match_material(
            trays,
            logical_id=0,
            semantic_label="Gray",
            expected_color="#A6A9AA",
        )

        self.assertTrue(result["unique_match"])
        self.assertEqual(
            result["selected"]["global_tray_id"],
            0,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
