from __future__ import annotations

import json
import tempfile
import unittest
import zipfile

from pathlib import Path

import trimesh

from am_print_executor.gcode_printability_gate import (
    _continuous_bridge_free_span_report,
    _continuous_bridge_has_two_model_anchors,
    _segment_cells,
    inspect_final_gcode_printability,
)
from am_print_executor.gcode_support_continuity import (
    ExtrusionSegment,
)


class FdmLayerPrintabilityGateTests(unittest.TestCase):
    fixture_root = (
        Path(__file__).parent
        / "fixtures"
        / "fdm_false_positive_current_kitten"
    )

    @staticmethod
    def square(
        *,
        center_x: float,
        center_y: float,
        size: float,
        z: float,
        feature: str,
    ) -> list[str]:
        half = size / 2.0
        x0, x1 = center_x - half, center_x + half
        y0, y1 = center_y - half, center_y + half
        return [
            f"G1 X{x0} Y{y0} Z{z}",
            f"; FEATURE: {feature}",
            f"G1 X{x1} Y{y0} E0.5",
            f"G1 X{x1} Y{y1} E0.5",
            f"G1 X{x0} Y{y1} E0.5",
            f"G1 X{x0} Y{y0} E0.5",
        ]

    @staticmethod
    def bridge(
        *,
        x0: float,
        x1: float,
        y: float,
        z: float,
    ) -> list[str]:
        return [
            f"G1 X{x0} Y{y} Z{z}",
            "; FEATURE: Bridge",
            f"G1 X{x1} Y{y} E0.5",
        ]

    @staticmethod
    def write_artifact(root: Path, commands: list[str]) -> Path:
        artifact = root / "fixture.gcode.3mf"
        settings = {
            "line_width": "0.4",
            "layer_height": "0.2",
            "support_top_z_distance": "0.2",
            "support_bottom_z_distance": "0.2",
            "support_object_xy_distance": "0.0",
        }
        gcode = "\n".join(["G90", "M83", *commands, ""])
        with zipfile.ZipFile(artifact, "w") as archive:
            archive.writestr(
                "Metadata/project_settings.config",
                json.dumps(settings),
            )
            archive.writestr("Metadata/plate_1.gcode", gcode)
        return artifact

    @staticmethod
    def write_grounded_mesh(root: Path) -> Path:
        mesh = trimesh.creation.box(extents=(10.0, 10.0, 10.0))
        mesh.apply_translation((0.0, 0.0, 5.0))
        path = root / "grounded.stl"
        mesh.export(path)
        return path

    def inspect(self, root: Path, commands: list[str], **kwargs):
        return inspect_final_gcode_printability(
            self.write_artifact(root, commands),
            geometry_path=self.write_grounded_mesh(root),
            **kwargs,
        )

    def test_current_kitten_old_pass_is_now_blocked(self):
        origin = json.loads(
            (self.fixture_root / "fixture_origin.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(origin["old_gate_status"], "pass")

        result = inspect_final_gcode_printability(
            self.fixture_root / "current_kitten_false_positive.gcode.3mf",
            geometry_path=self.fixture_root / "current_kitten_source.stl",
            minimum_bad_component_mm2=5.0,
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["feature_segment_counts"]["floating vertical shell"],
            13076,
        )
        self.assertTrue(
            any(
                blocker.startswith(
                    ("unsupported_layer_island", "unsupported_extrusion_region")
                )
                for blocker in result["blockers"]
            )
        )

    def test_simple_grounded_cube_layers_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands: list[str] = []
            for z in (0.2, 0.4, 0.6):
                commands.extend(
                    self.square(
                        center_x=0,
                        center_y=0,
                        size=10,
                        z=z,
                        feature="Outer wall",
                    )
                )
            result = self.inspect(root, commands)
            self.assertEqual(result["status"], "pass", result["blockers"])

    def test_floating_feature_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = []
            for z in (0.2, 0.4):
                commands.extend(
                    self.square(
                        center_x=0,
                        center_y=0,
                        size=10,
                        z=z,
                        feature="Outer wall",
                    )
                )
            commands.extend(
                self.square(
                    center_x=20,
                    center_y=20,
                    size=2,
                    z=0.6,
                    feature="Floating vertical shell",
                )
            )
            result = self.inspect(root, commands)
            self.assertEqual(result["status"], "blocked")
            self.assertTrue(
                any(
                    blocker.startswith("unsupported_layer_island")
                    for blocker in result["blockers"]
                )
            )

    def test_real_support_path_allows_same_floating_feature(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = []
            for z in (0.2, 0.4):
                commands.extend(
                    self.square(
                        center_x=0,
                        center_y=0,
                        size=10,
                        z=z,
                        feature="Outer wall",
                    )
                )
                commands.extend(
                    self.square(
                        center_x=20,
                        center_y=20,
                        size=2,
                        z=z,
                        feature="Support interface",
                    )
                )
            commands.extend(
                self.square(
                    center_x=20,
                    center_y=20,
                    size=2,
                    z=0.6,
                    feature="Floating vertical shell",
                )
            )
            result = self.inspect(root, commands)
            self.assertEqual(result["status"], "pass", result["blockers"])

    def test_support_that_itself_begins_in_air_does_not_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = []
            for z in (0.2, 0.4):
                commands.extend(
                    self.square(
                        center_x=0,
                        center_y=0,
                        size=10,
                        z=z,
                        feature="Outer wall",
                    )
                )
            commands.extend(
                self.square(
                    center_x=20,
                    center_y=20,
                    size=2,
                    z=0.4,
                    feature="Support interface",
                )
            )
            commands.extend(
                self.square(
                    center_x=20,
                    center_y=20,
                    size=2,
                    z=0.6,
                    feature="Floating vertical shell",
                )
            )
            result = self.inspect(root, commands)
            self.assertEqual(result["status"], "blocked")
            self.assertTrue(
                any(
                    blocker.startswith("unanchored_support_toolpath")
                    for blocker in result["blockers"]
                )
            )

    def test_tiny_unsupported_island_cannot_be_exempted_by_area(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = []
            for z in (0.2, 0.4):
                commands.extend(
                    self.square(
                        center_x=0,
                        center_y=0,
                        size=10,
                        z=z,
                        feature="Outer wall",
                    )
                )
            commands.extend(
                self.square(
                    center_x=20,
                    center_y=20,
                    size=0.2,
                    z=0.6,
                    feature="Floating vertical shell",
                )
            )
            result = self.inspect(
                root,
                commands,
                minimum_bad_component_mm2=100.0,
            )
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(
                result["policy"]["unsupported_component_area_exemption_mm2"],
                0.0,
            )

    def test_short_two_sided_bridge_passes_and_long_bridge_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pillars = []
            for z in (0.2, 0.4):
                for x in (0.0, 6.0):
                    pillars.extend(
                        self.square(
                            center_x=x,
                            center_y=0,
                            size=2,
                            z=z,
                            feature="Outer wall",
                        )
                    )
            short = self.inspect(
                root,
                [*pillars, *self.bridge(x0=0, x1=6, y=0, z=0.6)],
            )
            self.assertEqual(short["status"], "pass", short["blockers"])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pillars = []
            for z in (0.2, 0.4):
                for x in (0.0, 14.0):
                    pillars.extend(
                        self.square(
                            center_x=x,
                            center_y=0,
                            size=2,
                            z=z,
                            feature="Outer wall",
                        )
                    )
            long_result = self.inspect(
                root,
                [*pillars, *self.bridge(x0=0, x1=14, y=0, z=0.6)],
            )
            self.assertEqual(long_result["status"], "blocked")
            self.assertTrue(
                any(
                    blocker.startswith("unsafe_bridge")
                    for blocker in long_result["blockers"]
                )
            )

    def test_continuous_bridge_anchor_fallback_accepts_two_ended_major_span(self):
        bridge = ExtrusionSegment(
            z=0.6,
            x1=0.0,
            y1=0.0,
            x2=4.6,
            y2=0.0,
            feature="Bridge",
        )
        previous = [
            ExtrusionSegment(
                z=0.4,
                x1=-0.2,
                y1=-1.0,
                x2=-0.2,
                y2=1.0,
                feature="Outer wall",
            ),
            ExtrusionSegment(
                z=0.4,
                x1=4.8,
                y1=-1.0,
                x2=4.8,
                y2=1.0,
                feature="Outer wall",
            ),
        ]

        component = _segment_cells(
            bridge,
            cell_mm=0.4,
        )

        self.assertTrue(
            _continuous_bridge_has_two_model_anchors(
                component,
                [bridge],
                previous,
                cell_mm=0.4,
                support_radius_mm=0.273,
                component_span_mm=4.8,
                line_width_mm=0.42,
            )
        )

    def test_continuous_bridge_anchor_fallback_rejects_single_ended_bridge(self):
        bridge = ExtrusionSegment(
            z=0.6,
            x1=0.0,
            y1=0.0,
            x2=4.6,
            y2=0.0,
            feature="Bridge",
        )
        previous = [
            ExtrusionSegment(
                z=0.4,
                x1=-0.2,
                y1=-1.0,
                x2=-0.2,
                y2=1.0,
                feature="Outer wall",
            ),
        ]

        component = _segment_cells(
            bridge,
            cell_mm=0.4,
        )

        self.assertFalse(
            _continuous_bridge_has_two_model_anchors(
                component,
                [bridge],
                previous,
                cell_mm=0.4,
                support_radius_mm=0.273,
                component_span_mm=4.8,
                line_width_mm=0.42,
            )
        )

    def test_effective_bridge_span_accepts_internal_bridge_over_reachable_infill(self):
        bridge = ExtrusionSegment(
            z=0.6,
            x1=0.0,
            y1=0.0,
            x2=18.8,
            y2=0.0,
            feature="Bridge",
        )

        previous = []

        # Repeated reachable model lines emulate sparse infill underneath
        # an internal bridge. Individual free gaps are below 8mm although
        # the full bridge region is much wider than 8mm.
        for x_value in (
            0.0,
            4.0,
            8.0,
            12.0,
            16.0,
            18.8,
        ):
            previous.append(
                ExtrusionSegment(
                    z=0.4,
                    x1=x_value,
                    y1=-1.0,
                    x2=x_value,
                    y2=1.0,
                    feature="Sparse infill",
                )
            )

        component = _segment_cells(
            bridge,
            cell_mm=0.4,
        )

        report = (
            _continuous_bridge_free_span_report(
                component,
                [bridge],
                previous,
                cell_mm=0.4,
                support_radius_mm=0.273,
                component_span_mm=18.8,
                line_width_mm=0.42,
            )
        )

        self.assertTrue(
            report["available"]
        )

        self.assertTrue(
            report[
                "all_major_segments_two_ended"
            ]
        )

        self.assertLessEqual(
            report[
                "max_continuous_free_span_mm"
            ],
            8.0,
        )

    def test_effective_bridge_span_rejects_true_long_free_air_bridge(self):
        bridge = ExtrusionSegment(
            z=0.6,
            x1=0.0,
            y1=0.0,
            x2=14.0,
            y2=0.0,
            feature="Bridge",
        )

        previous = [
            ExtrusionSegment(
                z=0.4,
                x1=0.0,
                y1=-1.0,
                x2=0.0,
                y2=1.0,
                feature="Outer wall",
            ),
            ExtrusionSegment(
                z=0.4,
                x1=14.0,
                y1=-1.0,
                x2=14.0,
                y2=1.0,
                feature="Outer wall",
            ),
        ]

        component = _segment_cells(
            bridge,
            cell_mm=0.4,
        )

        report = (
            _continuous_bridge_free_span_report(
                component,
                [bridge],
                previous,
                cell_mm=0.4,
                support_radius_mm=0.273,
                component_span_mm=14.0,
                line_width_mm=0.42,
            )
        )

        self.assertTrue(
            report["available"]
        )

        self.assertTrue(
            report[
                "all_major_segments_two_ended"
            ]
        )

        self.assertGreater(
            report[
                "max_continuous_free_span_mm"
            ],
            8.0,
        )

    def test_disconnected_mesh_component_above_plate_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            grounded = trimesh.creation.box(extents=(10, 10, 2))
            grounded.apply_translation((0, 0, 1))
            floating = trimesh.creation.box(extents=(2, 2, 2))
            floating.apply_translation((20, 20, 6))
            geometry = root / "floating-component.stl"
            trimesh.util.concatenate([grounded, floating]).export(geometry)

            commands = []
            for z in (0.2, 0.4):
                commands.extend(
                    self.square(
                        center_x=0,
                        center_y=0,
                        size=10,
                        z=z,
                        feature="Outer wall",
                    )
                )
            result = inspect_final_gcode_printability(
                self.write_artifact(root, commands),
                geometry_path=geometry,
            )
            self.assertEqual(result["status"], "blocked")
            self.assertTrue(
                any(
                    blocker.startswith("floating_mesh_component")
                    for blocker in result["blockers"]
                )
            )


if __name__ == "__main__":
    unittest.main()
