from pathlib import Path
from unittest import mock
import unittest

import am_print_executor.developer_mode_final_acceptance_v1120 as a


class FinalAcceptancePreflightTests(unittest.TestCase):

    def test_missing_device_evidence_is_structured_block(self):
        fake_backend = mock.Mock()

        fake_backend.inspect_gcode_3mf.return_value = {
            "path": r"C:\fake\model.gcode.3mf",
            "sha256": "abc",
            "size_bytes": 123,
            "gcode_entries": [
                "Metadata/plate_1.gcode"
            ],
            "plate_count": 1,
        }

        with (
            mock.patch.object(
                a,
                "_load_backend",
                return_value=fake_backend,
            ),
            mock.patch.object(
                a,
                "_inspect_project_filaments",
                return_value={
                    "project_filament_count": 2,
                    "multi_material_candidate": True,
                },
            ),
            mock.patch.object(
                a,
                "_load_identity",
                side_effect=FileNotFoundError(
                    "missing gate1 evidence"
                ),
            ),
        ):
            result = a.acceptance_preflight(
                Path("dummy.gcode.3mf"),
                [0, 3],
                project_root=Path("."),
                job_request_id="M2-TEST",
                device_evidence_request_id="X1C-DEVICE-TEST",
            )

        self.assertFalse(result["passed"])
        self.assertEqual(
            result["status"],
            "live_acceptance_blocked",
        )
        self.assertIn(
            "device_identity_evidence_missing_or_invalid",
            result["blocking_reasons"],
        )
        self.assertFalse(
            result["device_identity_evidence_valid"]
        )
        self.assertFalse(result["network_used"])
        self.assertFalse(result["printer_command_sent"])
        self.assertFalse(result["access_code_requested"])


if __name__ == "__main__":
    unittest.main()
