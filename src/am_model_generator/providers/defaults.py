from __future__ import annotations

from .meshy import MeshyProvider
from .mock import MockProvider
from .triposg_local import TripoSGLocalProvider
from .registry import ProviderRegistry


def build_default_provider_registry(
) -> ProviderRegistry:
    """
    构建M2默认Provider Registry。

    这里只创建Provider对象，不访问网络。
    """

    return ProviderRegistry(
        [
            MockProvider(),
            TripoSGLocalProvider(),
            MeshyProvider(),
        ]
    )