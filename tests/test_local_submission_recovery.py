"""Recovery completes cached local postprocessing; it never resubmits work."""
from pathlib import Path
import hashlib
import json
import tempfile
import unittest
from unittest.mock import Mock, patch
import trimesh

from am_model_generator.contracts import M2ProviderError
from am_model_generator.planning import plan_m2
from am_model_generator.providers import ProviderRegistry, TripoSGLocalProvider
from am_model_generator.submission import submit_m2_plan
from am_model_generator.wsl_bridge import WslWorkerResult
from test_m2_submission import _create_m1_manifest


class LocalSubmissionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task = Path(plan_m2(_create_m1_manifest(self.root), output_root=self.root / 'tasks')['output_directory'])
        self.cache = self.root / 'cache'
        self.bridge = Mock()
        self.provider = TripoSGLocalProvider(bridge=self.bridge, cache_root=self.cache, wsl_cache_root='/cache')
        self.registry = ProviderRegistry([self.provider])

    def interrupted_size_step(self):
        def worker(*, environment_name, **kwargs):
            directory = next(self.cache.glob('triposg-local-*'))
            if environment_name == 't2i':
                (directory / 'reference_image.png').write_bytes(b'cached reference')
            else:
                (directory / 'generated_model.glb').write_bytes(trimesh.exchange.gltf.export_glb(
                    trimesh.Scene(trimesh.creation.box(extents=(2, 6, 3)))))
            return WslWorkerResult(command=(), payload={'status': 'completed'}, stdout='', stderr='', return_code=0)
        self.bridge.run_json_worker.side_effect = worker
        with patch('am_model_generator.normalization.export_glb_at_target_height',
                   side_effect=M2ProviderError('M2_FLAT_BASE_GATE_BLOCK', 'simulated old stage-order failure')):
            with self.assertRaises(M2ProviderError):
                self.submit()
        self.raw = next(self.cache.glob('triposg-local-*/generated_model.glb'))
        self.assertTrue((self.task / 'provider_request.json').is_file())
        self.assertFalse((self.task / 'provider_submission.json').exists())
        self.bridge.reset_mock()
        self.bridge.run_json_worker.side_effect = AssertionError('Generation must not be called during recovery')

    def submit(self):
        return submit_m2_plan(self.task, provider_name='triposg_local', registry=self.registry)

    def test_restart_recovers_size_failure_without_worker_execution(self):
        self.interrupted_size_step()
        raw_hash = hashlib.sha256(self.raw.read_bytes()).hexdigest()
        self.provider = TripoSGLocalProvider(bridge=self.bridge, cache_root=self.cache, wsl_cache_root='/cache')
        self.registry = ProviderRegistry([self.provider])
        result = self.submit()
        self.assertTrue(result['reused_existing_submission'])
        self.bridge.run_json_worker.assert_not_called()
        saved = json.loads((self.task / 'provider_submission.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['status'], 'completed')
        self.assertTrue(saved['provider_metadata']['recovered_local_postprocess'])
        self.assertEqual(hashlib.sha256(self.raw.read_bytes()).hexdigest(), raw_hash)
        self.assertTrue(self.submit()['reused_existing_submission'])
        self.bridge.run_json_worker.assert_not_called()

    def test_missing_cached_model_stays_blocked_without_regeneration(self):
        self.interrupted_size_step()
        self.raw.unlink()
        with self.assertRaises(M2ProviderError) as error:
            self.submit()
        self.assertEqual(error.exception.code, 'M2_SUBMISSION_STATE_INCOMPLETE')
        self.bridge.run_json_worker.assert_not_called()
        self.assertFalse((self.task / 'provider_submission.json').exists())

    def test_changed_request_cannot_claim_an_old_generated_model(self):
        self.interrupted_size_step()
        path = self.task / 'provider_request.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data['prompt'] += ' a different requested shape'
        path.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(M2ProviderError) as error:
            self.submit()
        self.assertEqual(error.exception.code, 'M2_PROVIDER_REQUEST_CONFLICT')
        self.bridge.run_json_worker.assert_not_called()
        self.assertFalse((self.task / 'provider_submission.json').exists())

    def test_invalid_cached_model_preserves_previous_artifact_and_reports_cause(self):
        self.interrupted_size_step()
        self.raw.write_bytes(b'broken glb')
        prior = self.raw.with_name('generated_model_mm.glb')
        prior.write_bytes(b'previous verified artifact')
        with self.assertRaises(M2ProviderError) as error:
            self.submit()
        self.assertEqual(error.exception.code, 'M2_TRIPOSG_LOCAL_TARGET_SIZE_FAILED')
        self.assertEqual(error.exception.details['normalization_error_code'], 'M2_NORMALIZATION_LOAD_FAILED')
        self.assertEqual(prior.read_bytes(), b'previous verified artifact')
        self.bridge.run_json_worker.assert_not_called()
        self.assertFalse((self.task / 'provider_submission.json').exists())
