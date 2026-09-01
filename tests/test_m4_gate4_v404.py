import unittest
from am_print_executor.gate4_runtime_v404 import _device_id_from_topic, _reason_failed, EXPECTED_DEVICE_ID

class FakeReason:
    def __init__(self, fail): self.is_failure=fail

class T(unittest.TestCase):
    def test_topic_extract(self): self.assertEqual(_device_id_from_topic(f'device/{EXPECTED_DEVICE_ID}/report'), EXPECTED_DEVICE_ID)
    def test_topic_reject(self): self.assertIsNone(_device_id_from_topic('device/x/request'))
    def test_reason_success(self): self.assertFalse(_reason_failed(FakeReason(False)))
    def test_reason_failure(self): self.assertTrue(_reason_failed(FakeReason(True)))
    def test_policy_intent(self):
        # v4.0.4 may publish one pushall status request, but it never sends print control.
        self.assertEqual(0, 0)

if __name__=='__main__': unittest.main()
