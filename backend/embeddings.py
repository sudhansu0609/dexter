"""
Shared sentence-embedding model.

One MiniLM instance serves both consumers - Tier-1 intent routing (router.py)
and semantic memory (memory.py). Loading it twice would double ~90MB of RAM
and the multi-second warm-up, for identical vectors.

Everything degrades: when sentence-transformers is not installed the model is
simply unavailable, the router falls back to lexical matching, and memory
falls back to its keyword recall.
"""

import threading
from typing import Any, List, Optional

MODEL_NAME = "all-MiniLM-L6-v2"

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
            from sentence_transformers import SentenceTransformer  # type: ignore

            _model = SentenceTransformer(MODEL_NAME)
            _state = "ready"
            print(f"[Embeddings] {MODEL_NAME} loaded (shared by router + memory).")
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
