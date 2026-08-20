from __future__ import annotations

import unittest

from am_print_executor.r6_toolchange_material_consistency_v2 import (
    filtered_logical_sequence,
    transition_count,
)


class R6ToolchangeMaterialConsistencyV2Tests(unittest.TestCase):
    def test_filtered_sequence_ignores_255(self):
        self.assertEqual(
            filtered_logical_sequence(
                [255, 0, 0, 1, 1, 0, 255]
            ),
            [0, 0, 1, 1, 0],
        )

    def test_transition_count(self):
        self.assertEqual(
            transition_count(
                [0, 0, 1, 1, 0, 1]
            ),
            3,
        )

    def test_identical_sequences_have_same_transitions(self):
        left = [0, 1, 0, 1]
        right = [0, 1, 0, 1]

        self.assertEqual(left, right)
        self.assertEqual(
            transition_count(left),
            transition_count(right),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
