import unittest
from am_print_executor.gate8e_yolo_adapter_v840 import Gate8EAdapterError, Gate8EYOLOAdapter

class Gate8EAdapterTests(unittest.TestCase):
    def test_normal(self):
        r = Gate8EYOLOAdapter.map_class("normal", 0.8)
        self.assertEqual(r.binary_prediction, "success")
        self.assertEqual(r.recommended_action, "CONTINUE")
    def test_bubbles(self):
        r = Gate8EYOLOAdapter.map_class("bubbles", 0.8)
        self.assertEqual(r.binary_prediction, "failure")
        self.assertEqual(r.recommended_action, "REVIEW")
    def test_overextrusion(self):
        self.assertEqual(Gate8EYOLOAdapter.map_class("overextrusion", 0.8).binary_prediction, "failure")
    def test_overextrusion10(self):
        self.assertEqual(Gate8EYOLOAdapter.map_class("overextrusion10", 0.8).binary_prediction, "failure")
    def test_overextrusion40(self):
        self.assertEqual(Gate8EYOLOAdapter.map_class("overextrusion40", 0.8).binary_prediction, "failure")
    def test_unknown(self):
        with self.assertRaises(Gate8EAdapterError):
            Gate8EYOLOAdapter.map_class("mystery", 0.5)
    def test_no_invented_defect_type(self):
        self.assertIsNone(Gate8EYOLOAdapter.map_class("bubbles", 0.9).defect_type)

if __name__ == "__main__":
    unittest.main()
