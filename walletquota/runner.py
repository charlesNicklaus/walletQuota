from concurrent.futures import ThreadPoolExecutor
from typing import Sequence, Tuple

from .models import ProviderResult
from .providers.base import Provider


def run_providers(
    providers: Sequence[Provider], max_workers: int = 8
) -> Tuple[ProviderResult, ...]:
    if not 1 <= max_workers <= 8:
        raise ValueError("max_workers must be between 1 and 8")
    if not providers:
        return ()

    worker_count = min(len(providers), max_workers)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(provider.run) for provider in providers]
        results = []
        for provider, future in zip(providers, futures):
            try:
                result = future.result()
                if result.provider != provider.name:
                    raise ValueError("Provider returned a mismatched name")
            except Exception:
                result = ProviderResult.failure(
                    provider.name, "internal", "Unexpected provider failure"
                )
            results.append(result)
    return tuple(results)
