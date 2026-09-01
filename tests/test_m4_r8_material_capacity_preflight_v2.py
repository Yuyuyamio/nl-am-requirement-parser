from __future__ import annotations

import unittest

from am_print_executor.r8_material_capacity_preflight_v2 import (
    capacity_telemetry,
    required_with_margin,
)


class R8MaterialCapacityPreflightV2Tests(unittest.TestCase):
    def test_margin(self):
        self.assertEqual(
            required_with_margin(
                100.0,
                0.20,
            ),
            120.0,
        )

    def test_remain_is_not_grams(self):
        row = capacity_telemetry(
            {
                "global_tray_id": 0,
                "remain": 97,
                "tray_weight": "1000",
            }
        )

        self.assertFalse(
            row["remain_interpreted_as_grams"]
        )
        self.assertIsNone(
            row["automatic_remaining_grams"]
        )

    def test_negative_remain_stays_raw_unknown(self):
        row = capacity_telemetry(
            {
                "global_tray_id": 3,
                "remain": -1,
                "tray_weight": "1000",
            }
        )

        self.assertEqual(
            row["remain_raw"],
            -1,
        )
        self.assertFalse(
            row["automatic_capacity_proven"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
