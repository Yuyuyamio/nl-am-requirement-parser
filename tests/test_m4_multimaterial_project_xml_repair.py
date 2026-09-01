import tempfile
import unittest
import zipfile

from pathlib import Path
from xml.etree import ElementTree as ET

from am_print_executor.multimaterial_project import (
    repair_bambu_model_settings_xml,
)


class ModelSettingsXmlRepairTests(
    unittest.TestCase
):

    def make_project(
        self,
        root: Path,
        model_settings: str,
    ) -> Path:

        path = root / "project.3mf"

        with zipfile.ZipFile(
            path,
            "w",
        ) as zf:

            zf.writestr(
                "[Content_Types].xml",
                (
                    '<?xml version="1.0"?>'
                    '<Types xmlns="http://schemas.'
                    'openxmlformats.org/package/'
                    '2006/content-types"/>'
                ),
            )

            zf.writestr(
                "Metadata/model_settings.config",
                model_settings,
            )

            zf.writestr(
                "unchanged.bin",
                b"DO_NOT_CHANGE",
            )

        return path


    def test_repairs_unescaped_quotes(
        self,
    ):
        broken = (
            '<?xml version="1.0"?>\n'
            '<config>\n'
            '  <metadata '
            'key="compatible_printers" '
            'value=""Bambu Lab X1 Carbon '
            '0.4 nozzle";"Bambu Lab X1 '
            '0.4 nozzle""/>\n'
            '</config>\n'
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            project = self.make_project(
                root,
                broken,
            )

            before_other = None

            with zipfile.ZipFile(
                project,
                "r",
            ) as zf:
                before_other = zf.read(
                    "unchanged.bin"
                )

            result = (
                repair_bambu_model_settings_xml(
                    project
                )
            )

            self.assertTrue(
                result["repaired"]
            )

            with zipfile.ZipFile(
                project,
                "r",
            ) as zf:

                raw = zf.read(
                    "Metadata/model_settings.config"
                )

                after_other = zf.read(
                    "unchanged.bin"
                )

            # Must now be genuinely parseable XML.
            root_xml = ET.fromstring(
                raw
            )

            metadata = root_xml.find(
                "metadata"
            )

            self.assertIsNotNone(
                metadata
            )

            self.assertEqual(
                metadata.attrib[
                    "compatible_printers"
                    if False
                    else "key"
                ],
                "compatible_printers",
            )

            self.assertEqual(
                metadata.attrib["value"],
                (
                    '"Bambu Lab X1 Carbon '
                    '0.4 nozzle";'
                    '"Bambu Lab X1 '
                    '0.4 nozzle"'
                ),
            )

            # No unrelated 3MF member may change.
            self.assertEqual(
                before_other,
                after_other,
            )


    def test_valid_xml_is_not_rewritten(
        self,
    ):
        valid = (
            '<?xml version="1.0"?>\n'
            '<config>\n'
            '  <metadata '
            'key="compatible_printers" '
            'value="&quot;Bambu Lab X1 '
            'Carbon 0.4 nozzle&quot;"/>\n'
            '</config>\n'
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            project = self.make_project(
                root,
                valid,
            )

            before = project.read_bytes()

            result = (
                repair_bambu_model_settings_xml(
                    project
                )
            )

            after = project.read_bytes()

            self.assertFalse(
                result["repaired"]
            )

            self.assertEqual(
                before,
                after,
            )


if __name__ == "__main__":
    unittest.main()
