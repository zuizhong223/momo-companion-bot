# -*- coding: utf-8 -*-
"""MiniMax embo-01 embedding provider (custom response format)."""
import httpx

from astrbot import logger

from ..entities import ProviderType
from ..provider import EmbeddingProvider
from ..register import register_provider_adapter


@register_provider_adapter(
    "minimax_embedding",
    "MiniMax Embedding (embo-01)",
    provider_type=ProviderType.EMBEDDING,
)
class MiniMaxEmbeddingProvider(EmbeddingProvider):
    def __init__(self, provider_config: dict, provider_settings: dict) -> None:
        super().__init__(provider_config, provider_settings)
        base = provider_config.get("embedding_api_base", "https://api.minimaxi.com").rstrip("/")
        if not base.endswith("/v1"):
            base = base + "/v1"
        self.api_base = base
        self.api_key = provider_config.get("embedding_api_key", "")
        self.model = provider_config.get("embedding_model", "embo-01")
        self.timeout = int(provider_config.get("timeout", 30))
        self._dim = 1536
        logger.info(f"[MiniMax Embedding] {provider_config.get('id')} -> {self.api_base} model={self.model}")

    async def get_embedding(self, text: str) -> list[float]:
        return (await self.get_embeddings([text]))[0]

    async def get_embeddings(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        last_err = None
        for attempt in range(4):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    r = await client.post(
                        f"{self.api_base}/embeddings",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json={"model": self.model, "texts": texts, "type": "query"},
                    )
                    r.raise_for_status()
                    d = r.json()
                base = d.get("base_resp") or {}
                if base.get("status_code") not in (0, None):
                    msg = str(base.get("status_msg", ""))
                    raise RuntimeError(f"MiniMax embedding error {base.get('status_code')}: {msg}")
                vectors = d.get("vectors") or []
                out = []
                for v in vectors:
                    if isinstance(v, dict):
                        emb = v.get("embedding") or v.get("vector") or []
                    elif isinstance(v, list):
                        emb = v
                    else:
                        emb = []
                    out.append([float(x) for x in emb])
                if len(out) != len(texts):
                    raise RuntimeError(f"MiniMax returned {len(out)} vectors for {len(texts)} texts")
                return out
            except RuntimeError as e:
                last_err = e
                import asyncio
                await asyncio.sleep(6 * (attempt + 1))
            except Exception as e:
                last_err = e
                import asyncio
                await asyncio.sleep(4 * (attempt + 1))
        raise RuntimeError(f"MiniMax embedding failed after retries: {last_err}")

    def get_dim(self) -> int:
        return self._dim
