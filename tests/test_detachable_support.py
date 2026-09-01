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

    def test_invalid_nozzle_is_rejected_before_invoking_bambu(self):
        with self.assertRaisesRegex(ValueError, "invalid_nozzle_diameter"):
            detachable_process({"layer_height": "0.2"}, 0.0)


if __name__ == "__main__":
    unittest.main()
