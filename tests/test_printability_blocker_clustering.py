from __future__ import annotations

import unittest

from am_print_executor.printability_blocker_clustering import (
    cluster_gate_blockers,
    extract_blocker_observations,
)


def report(
    layers,
):
    return {
        "status": "blocked",
        "policy": {
            "cell_mm": 0.4,
            "line_width_mm": 0.42,
            "configured_layer_height_mm": 0.2,
            "measured_layer_height_mm": 0.2,
        },
        "dangerous_layers": layers,
    }


class BlockerClusteringTests(
    unittest.TestCase
):

    def test_adjacent_layers_form_one_3d_cluster(self):
        result = cluster_gate_blockers(
            report(
                [
                    {
                        "z_mm": 10.0,
                        "issues": [
                            {
                                "kind":
                                    "unsupported_extrusion_region",
                                "area_mm2": 0.16,
                                "xy_bounds_mm":
                                    [[5.0, 5.0], [5.4, 5.4]],
                                "features":
                                    ["floating vertical shell"],
                            }
                        ],
                    },
                    {
                        "z_mm": 10.2,
                        "issues": [
                            {
                                "kind":
                                    "unsupported_extrusion_region",
                                "area_mm2": 0.32,
                                "xy_bounds_mm":
                                    [[5.2, 5.0], [5.8, 5.4]],
                                "features":
                                    ["floating vertical shell"],
                            }
                        ],
                    },
                    {
                        "z_mm": 10.4,
                        "issues": [
                            {
                                "kind":
                                    "unsupported_extrusion_region",
                                "area_mm2": 0.16,
                                "xy_bounds_mm":
                                    [[5.4, 5.0], [5.8, 5.4]],
                                "features":
                                    ["outer wall"],
                            }
                        ],
                    },
                ]
            )
        )

        self.assertEqual(
            len(result),
            1,
        )

        cluster = result[0]

        self.assertEqual(
            cluster.layer_count,
            3,
        )

        self.assertEqual(
            cluster.observation_count,
            3,
        )

        self.assertEqual(
            cluster.classification,
            "local_overhang",
        )

        self.assertTrue(
            cluster.persistent_across_layers
        )

        self.assertTrue(
            cluster.local_additive_candidate
        )

    def test_spatially_separate_defects_do_not_merge(self):
        result = cluster_gate_blockers(
            report(
                [
                    {
                        "z_mm": 5.0,
                        "issues": [
                            {
                                "kind":
                                    "unsupported_extrusion_region",
                                "area_mm2": 0.16,
                                "xy_bounds_mm":
                                    [[0.0, 0.0], [0.4, 0.4]],
                                "features":
                                    ["outer wall"],
                            },
                            {
                                "kind":
                                    "unsupported_extrusion_region",
                                "area_mm2": 0.16,
                                "xy_bounds_mm":
                                    [[20.0, 20.0], [20.4, 20.4]],
                                "features":
                                    ["outer wall"],
                            },
                        ],
                    }
                ]
            )
        )

        self.assertEqual(
            len(result),
            2,
        )

    def test_island_and_local_issue_share_physical_cluster(self):
        result = cluster_gate_blockers(
            report(
                [
                    {
                        "z_mm": 7.0,
                        "issues": [
                            {
                                "kind":
                                    "unsupported_layer_island",
                                "area_mm2": 0.64,
                                "xy_bounds_mm":
                                    [[3.0, 3.0], [3.8, 3.8]],
                                "features":
                                    ["outer wall"],
                            },
                            {
                                "kind":
                                    "unsupported_extrusion_region",
                                "area_mm2": 0.32,
                                "xy_bounds_mm":
                                    [[3.2, 3.2], [3.6, 3.6]],
                                "features":
                                    ["outer wall"],
                            },
                        ],
                    }
                ]
            )
        )

        self.assertEqual(
            len(result),
            1,
        )

        self.assertEqual(
            result[0].classification,
            "detached_growth",
        )

        self.assertFalse(
            result[0].local_additive_candidate
        )

    def test_support_path_issue_does_not_merge_with_geometry(self):
        result = cluster_gate_blockers(
            report(
                [
                    {
                        "z_mm": 9.0,
                        "issues": [
                            {
                                "kind":
                                    "unsupported_extrusion_region",
                                "area_mm2": 0.16,
                                "xy_bounds_mm":
                                    [[1.0, 1.0], [1.4, 1.4]],
                                "features":
                                    ["outer wall"],
                            },
                            {
                                "kind":
                                    "unanchored_support_toolpath",
                                "area_mm2": 0.16,
                                "xy_bounds_mm":
                                    [[1.0, 1.0], [1.4, 1.4]],
                                "features":
                                    ["support"],
                            },
                        ],
                    }
                ]
            )
        )

        self.assertEqual(
            len(result),
            2,
        )

        classes = {
            item.classification
            for item in result
        }

        self.assertEqual(
            classes,
            {
                "local_overhang",
                "support_path_defect",
            },
        )

    def test_observation_count_matches_gate_issue_count(self):
        source = report(
            [
                {
                    "z_mm": 1.0,
                    "issues": [
                        {
                            "kind":
                                "unsupported_extrusion_region",
                            "area_mm2": 0.16,
                            "xy_bounds_mm":
                                [[0, 0], [0.4, 0.4]],
                            "features":
                                ["outer wall"],
                        },
                        {
                            "kind":
                                "unsafe_bridge",
                            "area_mm2": 0.32,
                            "span_mm": 4.0,
                            "anchored_contact_count": 1,
                            "xy_bounds_mm":
                                [[1, 1], [2, 2]],
                            "features":
                                ["bridge"],
                        },
                    ],
                }
            ]
        )

        observations = (
            extract_blocker_observations(
                source
            )
        )

        self.assertEqual(
            len(observations),
            2,
        )


if __name__ == "__main__":
    unittest.main()
