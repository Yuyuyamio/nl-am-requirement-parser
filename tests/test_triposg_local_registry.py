from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from am_model_generator.providers import (
    TripoSGLocalProvider,
)
from am_model_generator.providers.defaults import (
    build_default_provider_registry,
)


class TripoSGLocalRegistryTests(
    unittest.TestCase
):
    def test_default_registry_contains_local_provider(
        self,
    ) -> None:
        registry = (
            build_default_provider_registry()
        )

        self.assertEqual(
            registry.names(),
            [
                "meshy",
                "mock",
                "triposg_local",
            ],
        )

        provider = registry.get(
            "triposg_local"
        )

        self.assertIsInstance(
            provider,
            TripoSGLocalProvider,
        )

        self.assertEqual(
            provider.name,
            "triposg_local",
        )

    def test_default_cache_is_project_stable(
        self,
    ) -> None:
        project_root = (
            Path(__file__)
            .resolve()
            .parents[1]
        )

        expected = (
            project_root
            / ".m2_local_cache"
            / "triposg_local"
        ).resolve()

        original_cwd = Path.cwd()

        with tempfile.TemporaryDirectory() as temp:
            try:
                os.chdir(temp)

                registry = (
                    build_default_provider_registry()
                )

                provider = registry.get(
                    "triposg_local"
                )

                self.assertEqual(
                    provider._cache_root,
                    expected,
                )
            finally:
                os.chdir(
                    original_cwd
                )


if __name__ == "__main__":
    unittest.main()
