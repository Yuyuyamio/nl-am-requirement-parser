from __future__ import annotations

import unittest

from pathlib import Path

from am_print_executor.r2s_slice_paint_proof import (
    build_slice_command,
    parse_gcode_usage,
    parse_slice_info_xml,
    validate_dual_material_evidence,
)


class R2SSlicePaintProofTests(unittest.TestCase):
    def test_slice_command_has_no_post_paint_geometry_flags(self):
        command = build_slice_command(
            studio_exe=Path("bambu-studio.exe"),
            input_project=Path("painted.3mf"),
            output_gcode=Path("painted.gcode.3mf"),
            machine_json=Path("machine.json"),
            process_json=Path("process.json"),
            filament_jsons=[
                Path("gray.json"),
                Path("yellow.json"),
            ],
        )

        self.assertNotIn("--orient", command)
        self.assertNotIn("--arrange", command)
        self.assertNotIn("--ensure-on-bed", command)
        self.assertIn("--slice", command)

    def test_slice_info_extracts_two_used_filaments(self):
        raw = b"""<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <filament id="1" type="PLA" color="#A6A9AA"
      used_m="1.2" used_g="3.4"
      used_for_object="true" used_for_support="false"/>
    <filament id="2" type="PLA" color="#F4EE2A"
      used_m="0.8" used_g="2.1"
      used_for_object="true" used_for_support="false"/>
    <layer_filament_list filament_list="0" layer_ranges="0 2"/>
    <layer_filament_list filament_list="0 1" layer_ranges="3 10"/>
  </plate>
</config>"""

        parsed = parse_slice_info_xml(raw)
        self.assertEqual(len(parsed["filaments"]), 2)
        self.assertEqual(parsed["filaments"][0]["id_int"], 1)
        self.assertTrue(parsed["filaments"][1]["used_for_object_bool"])

    def test_gcode_requires_real_bicolor_tool_changes(self):
        text = """
M620 S0A
M621 S0A
G1 X1 Y1 E0.25
M620 S1A
M621 S1A
G1 X2 Y2 E0.35
M620 S0A
M621 S0A
"""
        parsed = parse_gcode_usage(text)

        self.assertEqual(parsed["m620_logical_filaments"], [0, 1])
        self.assertEqual(parsed["m621_logical_filaments"], [0, 1])
        self.assertEqual(parsed["logical_toolchange_transitions"], 2)
        self.assertEqual(parsed["extrusion_command_count"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
