from __future__ import annotations

import unittest

from am_print_executor.r7_live_ams_mapping_v2 import (
    normalize_color,
    extract_ams_trays,
    reason_failed,
)


class R7LiveAMSMappingV21Tests(unittest.TestCase):
    def test_normalize_rgba(self):
        self.assertEqual(
            normalize_color("F4EE2AFF"),
            "#F4EE2A",
        )

    def test_extract_first_ams_tray_3(self):
        pobj = {
            "ams": {
                "ams": [
                    {
                        "id": "0",
                        "tray": [
                            {},
                            {},
                            {},
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
        self.assertEqual(
            rows[-1]["global_tray_id"],
            3,
        )

    def test_reason_zero_is_success(self):
        self.assertFalse(
            reason_failed(0)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
