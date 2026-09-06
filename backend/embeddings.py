"""
Shared sentence-embedding model.

One MiniLM instance serves both consumers - Tier-1 intent routing (router.py)
and semantic memory (memory.py). Loading it twice would double ~90MB of RAM
and the multi-second warm-up, for identical vectors.

Everything degrades: when sentence-transformers is not installed the model is
simply unavailable, the router falls back to lexical matching, and memory
falls back to its keyword recall.
"""

import os
import threading
from typing import Any, List, Optional

MODEL_NAME = "all-MiniLM-L6-v2"
_HF_CACHE_DIR = os.path.join(
    os.path.expanduser("~"), ".cache", "huggingface", "hub",
    f"models--sentence-transformers--{MODEL_NAME}",
)


def cached_locally() -> bool:
    """True when the MiniLM snapshot is already on disk."""
    return os.path.isdir(os.path.join(_HF_CACHE_DIR, "snapshots"))


def _prefer_offline() -> None:
    """
    Load from the local cache without touching the network.

    Every startup used to issue ~40 HEAD requests to huggingface.co before the
    model loaded (a multi-second stall, and much worse with no connectivity).
    When the snapshot exists there is nothing to fetch, so tell the hub so.
    Explicit environment values win; a missing cache leaves the one-time
    download path untouched.
    """
    if cached_locally():
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    else:
        print(f"[Embeddings] {MODEL_NAME} not cached yet; first load will download it once.")

_model: Optional[Any] = None
_state = "pending"  # pending | ready | unavailable
_lock = threading.Lock()


def get_model() -> Optional[Any]:
    global _model, _state
    if _state != "pending":
        return _model
    with _lock:
        if _state != "pending":
            return _model
        try:
            _prefer_offline()
            import time as _time

            from sentence_transformers import SentenceTransformer  # type: ignore

            started = _time.perf_counter()
            _model = SentenceTransformer(MODEL_NAME)
            _state = "ready"
            print(
                f"[Embeddings] {MODEL_NAME} loaded in {_time.perf_counter() - started:.1f}s "
                f"(shared by router + memory, offline={os.environ.get('HF_HUB_OFFLINE') == '1'})."
            )
        except Exception as exc:
            _state = "unavailable"
            print(f"[Embeddings] sentence-transformers unavailable ({exc}).")
        return _model


def available() -> bool:
    return get_model() is not None


def embed(texts: List[str]):
    """Normalized float32 vectors (one per text), or None when unavailable."""
    model = get_model()
    if model is None or not texts:
        return None
    return model.encode(texts, normalize_embeddings=True)


def to_blob(vec) -> bytes:
    """float32 bytes for a BLOB column (the layout sqlite-vec expects too)."""
    import numpy as np

    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes):
    import numpy as np

    return np.frombuffer(blob, dtype=np.float32)
