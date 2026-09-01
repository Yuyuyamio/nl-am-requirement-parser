from pathlib import Path
import tempfile,unittest,zipfile
from unittest.mock import patch
from xml.etree import ElementTree as ET
from am_print_executor.bambu_project_repair import repair_bambu_model_settings_xml


class LongProjectPathTests(unittest.TestCase):
    def test_repair_does_not_lengthen_project_name_or_touch_other_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            while len(str(root))<200: root=root/('p'*min(35,200-len(str(root))))
            root.mkdir(parents=True,exist_ok=True)
            project=root/('model_'+'x'*33+'.3mf')
            self.assertLess(len(str(project)),260)
            self.assertGreater(len(str(project))+len('.model_settings_xml_repair.tmp'),260)
            with zipfile.ZipFile(project,'w') as z:
                z.writestr('Metadata/model_settings.config','<config>\n<metadata key="name" value="Mouse & Base"/>\n</config>')
                z.writestr('3D/3dmodel.model',b'unchanged original geometry')
            report=repair_bambu_model_settings_xml(project)
            self.assertTrue(report['repaired'])
            with zipfile.ZipFile(project) as z:
                ET.fromstring(z.read('Metadata/model_settings.config'))
                self.assertEqual(z.read('3D/3dmodel.model'),b'unchanged original geometry')
            self.assertEqual(list(root.iterdir()),[project])

    def test_failed_replace_keeps_original_and_cleans_only_own_temp_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);project=root/'model.3mf'
            with zipfile.ZipFile(project,'w') as z:
                z.writestr('Metadata/model_settings.config','<config>\n<metadata key="name" value="a & b"/>\n</config>')
            before=project.read_bytes()
            unrelated=root/'model.3mf.model_settings_xml_repair.tmp';unrelated.write_text('keep')
            with patch('am_print_executor.bambu_project_repair.os.replace',side_effect=PermissionError('locked')):
                with self.assertRaises(PermissionError):repair_bambu_model_settings_xml(project)
            self.assertEqual(project.read_bytes(),before)
            self.assertEqual(unrelated.read_text(),'keep')
            self.assertEqual(set(root.iterdir()),{project,unrelated})
