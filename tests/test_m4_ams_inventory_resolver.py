import unittest

from am_print_executor.ams_inventory_resolver import (
    extract_inventory,
    resolve_mapping,
    standard_ams_wire_slot,
)


class AmsInventoryResolverTests(
    unittest.TestCase
):

    def test_standard_ams_slot_index(
        self,
    ):
        self.assertEqual(
            standard_ams_wire_slot(0, 0),
            0,
        )

        self.assertEqual(
            standard_ams_wire_slot(0, 3),
            3,
        )

        self.assertEqual(
            standard_ams_wire_slot(1, 0),
            4,
        )


    def test_extract_realistic_ams_payload(
        self,
    ):
        payload = {
            "print": {
                "ams": {
                    "tray_exist_bits": "9",

                    "ams": [
                        {
                            "id": "0",

                            "tray": [
                                {
                                    "id": "0",
                                    "tray_type": "PLA",
                                    "tray_color": "A6A9AAFF",
                                    "remain": 80,
                                },
                                {
                                    "id": "1",
                                },
                                {
                                    "id": "2",
                                },
                                {
                                    "id": "3",
                                    "tray_type": "PLA",
                                    "tray_color": "F4EE2AFF",
                                    "remain": 70,
                                },
                            ],
                        }
                    ],
                }
            }
        }

        inventory = extract_inventory(
            payload
        )

        self.assertIsNotNone(
            inventory
        )

        occupied = [
            row
            for row in inventory["slots"]
            if row["exists"]
        ]

        self.assertEqual(
            [row["wire_slot"] for row in occupied],
            [0, 3],
        )

        self.assertEqual(
            occupied[0]["colour"],
            "#A6A9AA",
        )

        self.assertEqual(
            occupied[1]["colour"],
            "#F4EE2A",
        )


    def test_exact_gray_yellow_resolves(
        self,
    ):
        requirements = [
            {
                "logical_filament_id": 1,
                "material": "PLA",
                "colour": "#A6A9AA",
            },
            {
                "logical_filament_id": 2,
                "material": "PLA",
                "colour": "#F4EE2A",
            },
        ]

        inventory = {
            "slots": [
                {
                    "wire_slot": 0,
                    "exists": True,
                    "identity_ready": True,
                    "material": "PLA",
                    "colour": "#A6A9AA",
                },
                {
                    "wire_slot": 3,
                    "exists": True,
                    "identity_ready": True,
                    "material": "PLA",
                    "colour": "#F4EE2A",
                },
            ]
        }

        result = resolve_mapping(
            requirements=requirements,
            inventory=inventory,
        )

        self.assertEqual(
            result["status"],
            "ams_mapping_resolved",
        )

        self.assertEqual(
            result["ams_mapping_logical"],
            [0, 3],
        )


    def test_ambiguous_duplicate_blocks(
        self,
    ):
        requirements = [
            {
                "logical_filament_id": 1,
                "material": "PLA",
                "colour": "#FFFFFF",
            }
        ]

        inventory = {
            "slots": [
                {
                    "wire_slot": 0,
                    "exists": True,
                    "identity_ready": True,
                    "material": "PLA",
                    "colour": "#FFFFFF",
                },
                {
                    "wire_slot": 1,
                    "exists": True,
                    "identity_ready": True,
                    "material": "PLA",
                    "colour": "#FFFFFF",
                },
            ]
        }

        result = resolve_mapping(
            requirements=requirements,
            inventory=inventory,
        )

        self.assertEqual(
            result["status"],
            "ams_mapping_blocked",
        )

        self.assertIn(
            "ambiguous_exact_assignment",
            result["blockers"],
        )


    def test_wrong_colour_never_guessed(
        self,
    ):
        requirements = [
            {
                "logical_filament_id": 1,
                "material": "PLA",
                "colour": "#A6A9AA",
            }
        ]

        inventory = {
            "slots": [
                {
                    "wire_slot": 0,
                    "exists": True,
                    "identity_ready": True,
                    "material": "PLA",
                    "colour": "#A7A9AA",
                }
            ]
        }

        result = resolve_mapping(
            requirements=requirements,
            inventory=inventory,
        )

        self.assertEqual(
            result["status"],
            "ams_mapping_blocked",
        )

        self.assertIsNone(
            result["ams_mapping_logical"]
        )


if __name__ == "__main__":
    unittest.main()
