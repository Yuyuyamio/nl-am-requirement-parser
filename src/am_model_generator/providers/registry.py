from __future__ import annotations

from collections.abc import Iterable

from am_model_generator.contracts import (
    M2ProviderError,
)

from .base import GenerationProvider


class ProviderRegistry:
    """
    M2生成Provider注册表。

    上层通过名称获取Provider，
    不直接依赖MeshyProvider或MockProvider类。
    """

    def __init__(
        self,
        providers: Iterable[
            GenerationProvider
        ]
        | None = None,
    ) -> None:
        self._providers: dict[
            str,
            GenerationProvider,
        ] = {}

        if providers is not None:
            for provider in providers:
                self.register(provider)

    @staticmethod
    def _normalize_name(
        name: str,
    ) -> str:
        cleaned = name.strip().lower()

        if not cleaned:
            raise M2ProviderError(
                "M2_PROVIDER_NAME_EMPTY",
                "Provider名称不能为空",
            )

        return cleaned

    def register(
        self,
        provider: GenerationProvider,
        *,
        replace: bool = False,
    ) -> None:
        name = self._normalize_name(
            provider.name
        )

        if (
            name in self._providers
            and not replace
        ):
            raise M2ProviderError(
                "M2_PROVIDER_ALREADY_REGISTERED",
                "Provider已经注册",
                details={
                    "provider": name,
                },
            )

        self._providers[name] = provider

    def get(
        self,
        name: str,
    ) -> GenerationProvider:
        normalized_name = (
            self._normalize_name(name)
        )

        provider = self._providers.get(
            normalized_name
        )

        if provider is None:
            raise M2ProviderError(
                "M2_PROVIDER_NOT_FOUND",
                "没有找到指定Provider",
                details={
                    "provider": (
                        normalized_name
                    ),
                    "available_providers": (
                        self.names()
                    ),
                },
            )

        return provider

    def names(self) -> list[str]:
        return sorted(
            self._providers
        )