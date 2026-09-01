from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
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
        receipt = {"status": "pass", "support_mode": "detachable", "model_self_support_required": False,
                   "before_orientation_status": "pass", "after_orientation_status": "pass",
                   "dangerous_layer_count": 0, "source_unchanged": True,
                   "flat_base": {"base_flatness_passed": True}, "removal": {"status": "pass"},
                   "auto_orient": {"status": "auto_orient_complete", "output": {"path": project, "sha256": phash,
                       "upright_pose": {"status": "pass"}, "flat_base_after_orientation": {"base_flatness_passed": True}}},
                   "project": project, "geometry": stl, "artifact": gcode,
                   "geometry_sha256": shash, "artifact_sha256": ghash}
        prepared = {"status": "slice_complete", "pipeline": "verified_support_orient_reslice_v3",
                    "support_mode": "detachable", "model_self_support_required": False, "auto_orient_applied": True,
                    "artifact": {"path": gcode, "sha256": ghash}, "geometry_path": stl, "acceptance": receipt}
        self.state = {"status": "ready_to_print", "job_id": self.job.name, "stages": {
            "bambu_slice": {"status": "completed", "result": prepared},
            "m3_printability": {"status": "completed", "result": {"status": "pass"}},
            "bambu_support_reslice": {"status": "skipped"}}}
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

    def test_downloads_use_final_receipt_and_preview_uses_same_stl(self):
        with urlopen(self.base) as response:
            delivery = json.load(response)["delivery"]
        self.assertTrue(delivery["available"])
        self.assertEqual(len(delivery["files"]), 3)
        self.assertNotIn("coupon", json.dumps(delivery))
        for file in delivery["files"]:
            with urlopen(self.base + "/files/" + file["kind"]) as response:
                self.assertEqual(response.read(), Path(self.files[file["kind"]][0]).read_bytes())
                self.assertEqual(response.headers["X-Artifact-SHA256"], file["sha256"])
                self.assertIn("filename*=UTF-8''", response.headers["Content-Disposition"])

    def test_modified_project_or_stl_or_gcode_blocks_entire_delivery(self):
        for path, _ in self.files.values():
            target = Path(path)
            original = target.read_bytes()
            target.write_bytes(b"changed file")
            with self.assertRaises(HTTPError) as error:
                urlopen(self.base + "/files/project")
            self.assertEqual(error.exception.code, 409)
            target.write_bytes(original)

    def test_rejected_regeneration_never_falls_back_to_earlier_pass(self):
        for stage in ("bambu_regeneration_slice", "bambu_regeneration_support_reslice"):
            state = copy.deepcopy(self.state)
            state["stages"][stage] = {"status": "completed", "result": {"status": "blocked"}}
            with self.assertRaises(DeliveryUnavailable):
                verified_files(state, self.job)

    def test_latest_accepted_regeneration_replaces_original_geometry(self):
        generated = copy.deepcopy(self.prepared)
        replacement = self.job / "regenerated.stl"
        replacement.write_bytes(b"new final geometry")
        generated["geometry_path"] = generated["acceptance"]["geometry"] = str(replacement)
        generated["acceptance"]["geometry_sha256"] = hashlib.sha256(replacement.read_bytes()).hexdigest()
        self.state["stages"]["bambu_regeneration_slice"] = {"status": "completed", "result": generated}
        self.state["stages"]["m3_regeneration_printability"] = {"status": "completed", "result": {"status": "pass"}}
        self.assertEqual(verified_files(self.state, self.job)["stl"][0], replacement)

    def test_foreign_job_path_is_blocked_even_with_correct_hash(self):
        other = self.root / "foreign.stl"
        other.write_bytes(Path(self.files["stl"][0]).read_bytes())
        self.prepared["geometry_path"] = str(other)
        with self.assertRaises(DeliveryUnavailable):
            verified_files(self.state, self.job)

    def test_all_final_gate_failures_block_downloads(self):
        for field, value in (("support_mode", "permanent"), ("status", "blocked"),
                             ("before_orientation_status", "blocked"), ("after_orientation_status", "blocked"),
                             ("dangerous_layer_count", 1), ("source_unchanged", False),
                             ("flat_base", {"base_flatness_passed": False}), ("removal", {"status": "blocked"})):
            state = copy.deepcopy(self.state)
            state["stages"]["bambu_slice"]["result"]["acceptance"][field] = value
            with self.subTest(field=field), self.assertRaises(DeliveryUnavailable):
                verified_files(state, self.job)

    def test_blocked_or_legacy_job_never_exposes_artifacts(self):
        for status in ("needs_geometry_regeneration", "failed", "running", "stopped"):
            self.state["status"] = status
            with self.assertRaises(DeliveryUnavailable):
                verified_files(self.state, self.job)
        self.state["status"] = "ready_to_print"
        self.prepared.pop("acceptance")
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
