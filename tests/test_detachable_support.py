from __future__ import annotations

import unittest

from am_print_executor.detachable_support import (
    detachable_process,
    validate_support_mode,
)


class BambuTreeSupportProfileTests(unittest.TestCase):
    def test_profile_requests_bambu_native_tree_hybrid(self):
        settings, action = detachable_process(
            {"layer_height": "0.2", "outer_wall_line_width": "0.42"},
            0.4,
        )
        self.assertEqual(settings["enable_support"], "1")
        self.assertEqual(settings["support_type"], "tree(auto)")
        self.assertEqual(settings["support_style"], "tree_hybrid")
        self.assertEqual(action["support_generator"], "bambu_studio_native")
        self.assertTrue(action["headless"])

    def test_permanent_custom_support_mode_is_removed(self):
        with self.assertRaisesRegex(ValueError, "unknown_support_mode"):
            validate_support_mode("permanent")

    def test_unrelated_bambu_profile_settings_are_preserved(self):
        original = {
            "layer_height": "0.16",
            "support_top_z_distance": "0.31",
            "support_threshold_angle": "47",
        }
        settings, action = detachable_process(original, 0.0)
        for key, value in original.items():
            self.assertEqual(settings[key], value)
        self.assertTrue(action["bambu_profile_settings_preserved"])


if __name__ == "__main__":
    unittest.main()
