import unittest
from unittest import mock

import am_print_executor.developer_mode_backend as backend


class BackendFacadeTests(
    unittest.TestCase
):

    def test_fixture_command_routes(
        self,
    ):
        with mock.patch.object(
            backend.multimaterial_fixture,
            "cli_main",
            return_value=11,
        ) as call:
            rc = backend.main(
                [
                    "prepare-ams-fixture",
                    "--x",
                ]
            )

        self.assertEqual(
            rc,
            11,
        )

        call.assert_called_once()

    def test_project_command_routes(
        self,
    ):
        with mock.patch.object(
            backend.multimaterial_project,
            "cli_main",
            return_value=12,
        ) as call:
            rc = backend.main(
                [
                    "assemble-ams-project",
                    "--x",
                ]
            )

        self.assertEqual(
            rc,
            12,
        )

        call.assert_called_once()

    def test_existing_command_routes_to_legacy(
        self,
    ):
        with mock.patch.object(
            backend.legacy,
            "main",
            return_value=0,
        ) as call:
            rc = backend.main(
                [
                    "inspect",
                    "x.gcode.3mf",
                ]
            )

        self.assertEqual(
            rc,
            0,
        )

        call.assert_called_once()


if __name__ == "__main__":
    unittest.main()
