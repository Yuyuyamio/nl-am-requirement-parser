from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from am_print_frontend.delivery import DeliveryUnavailable, verified_files
from am_print_frontend.server import JobManager, create_server


class TestFinalDelivery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.job = self.root / "AUTO-DELIVERY"
        self.job.mkdir()
        self.files = {}
        for kind, name in {"project": "final.3mf", "stl": "final.stl", "gcode": "final.gcode.3mf"}.items():
            path = self.job / name
            path.write_bytes(("final " + kind).encode())
            self.files[kind] = (str(path), hashlib.sha256(path.read_bytes()).hexdigest())
        project, phash = self.files["project"]
        stl, shash = self.files["stl"]
        gcode, ghash = self.files["gcode"]
        prepared = {"status": "slice_complete", "pipeline": "bambu_native_direct_print_v2",
                    "acceptance_basis": "bambu_cli_slice_success",
                    "post_slice_validation_performed": False,
                    "support_mode": "detachable", "support_generator": "bambu_studio_native",
                    "support_type": "tree(auto)", "support_style": "tree_hybrid", "headless": True,
                    "model_self_support_required": False, "auto_orient_applied": True,
                    "artifact": {"path": gcode, "sha256": ghash},
                    "project": {"path": project, "sha256": phash},
                    "geometry_path": stl, "geometry_sha256": shash}
        self.state = {"status": "ready_to_print", "job_id": self.job.name, "stages": {
            "bambu_slice": {"status": "completed", "result": prepared},
        }}
        self.prepared = prepared
        self.save()
        self.server = create_server(port=0, manager=JobManager(output_root=self.root))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}/api/jobs/{self.job.name}"
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def save(self):
        (self.job / "workflow_state.json").write_text(json.dumps(self.state), encoding="utf-8")

    def test_downloads_use_bambu_slice_files_and_preview_uses_same_stl(self):
        with urlopen(self.base) as response:
            delivery = json.load(response)["delivery"]
        self.assertTrue(delivery["available"])
        self.assertEqual(len(delivery["files"]), 3)
        self.assertEqual([file["kind"] for file in delivery["files"]], ["gcode", "project", "stl"])
        self.assertTrue(delivery["files"][0]["name"].startswith("01_含真实支撑切片"))
        self.assertEqual(delivery["preview_url"], self.base.replace("http://127.0.0.1:" + str(self.server.server_port), "") + "/support-preview")
        self.assertNotIn("coupon", json.dumps(delivery))
        for file in delivery["files"]:
            with urlopen(self.base + "/files/" + file["kind"]) as response:
                self.assertEqual(response.read(), Path(self.files[file["kind"]][0]).read_bytes())
                self.assertEqual(response.headers["X-Artifact-SHA256"], file["sha256"])
                self.assertIn("filename*=UTF-8''", response.headers["Content-Disposition"])

    def test_support_preview_uses_verified_gcode_and_maps_nozzle_offset(self):
        gcode_path = Path(self.files["gcode"][0])
        with zipfile.ZipFile(gcode_path, "w") as archive:
            archive.writestr("Metadata/project_settings.config", json.dumps({"extruder_offset": ["1x2"]}))
            archive.writestr("Metadata/plate_1.gcode", """
M83
G90
; FEATURE:Support
; LINE_WIDTH:0.42
G1 Z0.2
G1 X10 Y20
G1 X12 Y20 E1
; FEATURE:Support interface
G1 Z0.4
G1 X10 Y20
G1 X12 Y20 E1
""")
        digest = hashlib.sha256(gcode_path.read_bytes()).hexdigest()
        self.files["gcode"] = (str(gcode_path), digest)
        self.prepared["artifact"]["sha256"] = digest
        self.save()
        with urlopen(self.base + "/support-preview") as response:
            preview = json.load(response)
        self.assertEqual(preview["schema"], "actual_support_toolpaths_v1")
        self.assertEqual(preview["source"]["gcode_sha256"], digest)
        self.assertEqual(preview["source"]["stl_sha256"], self.files["stl"][1])
        self.assertEqual(preview["segment_count"], 2)
        self.assertEqual(preview["interface_segment_count"], 1)
        self.assertEqual(preview["paths"][0][:6], [11.0, 22.0, 0.2, 13.0, 22.0, 0.2])
        gcode_path.write_bytes(b"changed after receipt")
        with self.assertRaises(HTTPError) as error:
            urlopen(self.base + "/support-preview")
        self.assertEqual(error.exception.code, 409)

    def test_modified_project_or_stl_or_gcode_blocks_entire_delivery(self):
        for path, _ in self.files.values():
            target = Path(path)
            original = target.read_bytes()
            target.write_bytes(b"changed file")
            with self.assertRaises(HTTPError) as error:
                urlopen(self.base + "/files/project")
            self.assertEqual(error.exception.code, 409)
            target.write_bytes(original)

    def test_foreign_job_path_is_blocked_even_with_correct_hash(self):
        other = self.root / "foreign.stl"
        other.write_bytes(Path(self.files["stl"][0]).read_bytes())
        self.prepared["geometry_path"] = str(other)
        with self.assertRaises(DeliveryUnavailable):
            verified_files(self.state, self.job)

    def test_legacy_printability_fields_do_not_block_bambu_slice_delivery(self):
        self.prepared.update(
            dangerous_layer_count=999,
            flat_base={"base_flatness_passed": False},
            removal={"status": "blocked", "blockers": ["ignored"]},
        )
        self.assertEqual(set(verified_files(self.state, self.job)), {"gcode", "project", "stl"})

    def test_blocked_or_legacy_job_never_exposes_artifacts(self):
        for status in ("printability_blocked", "needs_geometry_regeneration", "failed", "running", "stopped"):
            self.state["status"] = status
            with self.assertRaises(DeliveryUnavailable):
                verified_files(self.state, self.job)
        self.state["status"] = "ready_to_print"
        self.prepared["pipeline"] = "bambu_native_tree_support_v1"
        with self.assertRaises(DeliveryUnavailable):
            verified_files(self.state, self.job)

    def test_download_endpoint_does_not_accept_paths(self):
        with self.assertRaises(HTTPError) as error:
            urlopen(self.base + "/files/..%2Fworkflow_state.json")
        self.assertEqual(error.exception.code, 404)

    def test_changed_file_cannot_be_promoted_to_physical_print(self):
        Path(self.files["gcode"][0]).write_bytes(b"changed")
        request = Request(self.base + "/retry", data=b'{"start_print":true}', headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 409)

    def test_already_started_job_cannot_be_retried(self):
        self.state["status"] = "print_started"
        self.save()
        request = Request(self.base + "/retry", data=b'{"start_print":true}', headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 409)
