from __future__ import annotations

import unittest

from am_print_executor.r9_upload_only_v2 import (
    make_remote_name,
)


class R9UploadOnlyV2Tests(unittest.TestCase):
    def test_remote_name_is_sha_bound(self):
        sha = "9894f2f80a2d82227a7fb344004cf0beffa9d718c2025745a9f9cb02218aa324"
        self.assertEqual(
            make_remote_name(sha),
            "dog_r9_9894f2f80a2d.gcode.3mf",
        )

    def test_remote_name_is_ascii_safe(self):
        name = make_remote_name("a" * 64)
        self.assertNotIn(" ", name)
        self.assertTrue(name.endswith(".gcode.3mf"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
