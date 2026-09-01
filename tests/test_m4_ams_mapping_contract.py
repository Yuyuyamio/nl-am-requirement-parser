import unittest

from am_print_executor.ams_mapping import (
    AmsMappingError,
    parse_logical_mapping,
    to_x1c_wire_mapping,
    validate_logical_mapping,
)

from am_print_executor.developer_mode_backend_v1120 import (
    DeveloperBackendError,
    build_project_file_payload,
)

from am_print_executor.developer_mode_final_acceptance_v1120 import (
    FinalAcceptanceError,
    _parse_mapping,
)


class AmsMappingContractTests(
    unittest.TestCase
):

    def test_two_logical_slots_encode_to_wire(self):
        self.assertEqual(
            to_x1c_wire_mapping([0, 3]),
            [0, 3, -1, -1, -1],
        )


    def test_single_logical_slot_encodes_to_wire(self):
        self.assertEqual(
            to_x1c_wire_mapping([2]),
            [2, -1, -1, -1, -1],
        )


    def test_logical_mapping_rejects_padding(self):
        with self.assertRaises(
            AmsMappingError
        ):
            validate_logical_mapping(
                [0, -1]
            )


    def test_mapping_over_capacity_blocks(self):
        with self.assertRaises(
            AmsMappingError
        ):
            validate_logical_mapping(
                [0, 1, 2, 3, 4, 5]
            )


    def test_expected_filament_count_is_strict(self):
        with self.assertRaises(
            AmsMappingError
        ):
            validate_logical_mapping(
                [0, 3],
                expected_count=3,
            )


    def test_final_acceptance_rejects_duplicate_slots(self):
        with self.assertRaises(
            FinalAcceptanceError
        ):
            _parse_mapping("0,0")


    def test_final_acceptance_rejects_wire_padding(self):
        with self.assertRaises(
            FinalAcceptanceError
        ):
            _parse_mapping("0,3,-1,-1,-1")


    def test_final_acceptance_accepts_logical_mapping(self):
        self.assertEqual(
            _parse_mapping("0,3"),
            [0, 3],
        )


    def test_backend_is_only_wire_padding_layer(self):
        payload = build_project_file_payload(
            remote_path="/cache/test.gcode.3mf",
            gcode_entry="Metadata/plate_1.gcode",
            sequence_id="123",
            use_ams=True,
            ams_mapping=[0, 3],
        )

        self.assertEqual(
            payload["print"]["ams_mapping"],
            [0, 3, -1, -1, -1],
        )


    def test_backend_rejects_prepadded_mapping(self):
        with self.assertRaises(
            DeveloperBackendError
        ):
            build_project_file_payload(
                remote_path="/cache/test.gcode.3mf",
                gcode_entry="Metadata/plate_1.gcode",
                sequence_id="123",
                use_ams=True,
                ams_mapping=[
                    0,
                    3,
                    -1,
                    -1,
                    -1,
                ],
            )


    def test_external_spool_wire_semantics_preserved(self):
        payload = build_project_file_payload(
            remote_path="/cache/test.gcode.3mf",
            gcode_entry="Metadata/plate_1.gcode",
            sequence_id="123",
            use_ams=False,
            ams_mapping=None,
        )

        self.assertEqual(
            payload["print"]["ams_mapping"],
            [-1],
        )


if __name__ == "__main__":
    unittest.main()
