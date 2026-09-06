"""
Is Dexter actually running well, and if not, why?

Dexter had no way to say "I am degraded". The symptom that motivated this
module: Ollama could not fit qwen2.5:7b because another llama.cpp server held
14.5 GB of a 16 GB card, so it silently fell back to CPU and every action took
7-23 seconds instead of well under one. Nothing in the app reported that. It
just felt broken, and the blame landed on the model and the router - both of
which measure fine (13/15 tool accuracy, 15/15 escalation).

So: detect the degradations that make Dexter feel broken, name the cause, and
say what to do about it. Detect and report - never manage. Freeing the GPU is
the user's call (PLAN.md, decision D9).

Every probe is defensive and time-boxed: this is called from /status on a timer,
and a diagnostic that hangs or raises is worse than no diagnostic at all.
"""

import os
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

import env_boot  # noqa: F401  - loads .env for every entry point

import requests

# /status is polled; the underlying facts (which model is resident, how much
# VRAM is free) change on the scale of minutes, not seconds.
_CACHE_TTL_SECONDS = 45.0
_PROBE_TIMEOUT = 2.0
_NVIDIA_SMI_TIMEOUT = 4.0

# Below this, a model that wants the GPU is not going to get it.
_VRAM_HEADROOM_MB = 1024

_cache: Optional[Dict[str, Any]] = None
_cache_at = 0.0
_lock = threading.Lock()


def _ollama_base(url: str) -> str:
    """The native API root: OLLAMA_URL points at the OpenAI-compatible /v1."""
    return url[: -len("/v1")] if url.rstrip("/").endswith("/v1") else url.rstrip("/")


def _loaded_models(ollama_url: str) -> List[Dict[str, Any]]:
    """What Ollama currently has resident, via the native /api/ps."""
    try:
        response = requests.get(
            f"{_ollama_base(ollama_url)}/api/ps", timeout=_PROBE_TIMEOUT
        )
        if response.status_code != 200:
            return []
        return response.json().get("models", []) or []
    except Exception:
        return []


def _model_size_mb(ollama_url: str, model: str) -> Optional[int]:
    """On-disk size of a model, as a stand-in for what it will want in VRAM."""
    try:
        response = requests.get(
            f"{_ollama_base(ollama_url)}/api/tags", timeout=_PROBE_TIMEOUT
        )
        if response.status_code != 200:
            return None
        for item in response.json().get("models", []) or []:
            if item.get("model") == model or item.get("name") == model:
                size = int(item.get("size") or 0)
                return int(size / (1024 * 1024)) if size else None
    except Exception:
        return None
    return None


def _gpu_state() -> Optional[Dict[str, Any]]:
    """Total/used/free VRAM via nvidia-smi. None when there is no NVIDIA GPU."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        out = subprocess.run(
            [
                smi,
                "--query-gpu=name,memory.total,memory.used,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=_NVIDIA_SMI_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        name, total, used, free = [p.strip() for p in out.stdout.strip().splitlines()[0].split(",")]
        return {
            "name": name,
            "totalMb": int(float(total)),
            "usedMb": int(float(used)),
            "freeMb": int(float(free)),
        }
    except Exception:
        return None


def _gpu_hogs() -> List[str]:
    """Processes holding GPU memory, so the cause can be named rather than guessed."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return []
    try:
        out = subprocess.run(
            [smi, "--query-compute-apps=process_name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=_NVIDIA_SMI_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if out.returncode != 0:
            return []
        names = []
        for line in out.stdout.strip().splitlines():
            name = os.path.basename(line.strip())
            # Desktop compositing and browsers always appear here and are noise;
            # what matters is another inference server competing for the card.
            if name and name.lower() not in names:
                names.append(name.lower())
        interesting = [
            n for n in names
            if any(k in n for k in ("llama", "ollama", "python", "lmstudio", "lm-studio", "koboldcpp"))
        ]
        return interesting
    except Exception:
        return []


def _describe_placement(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Where a resident Ollama model is actually running."""
    size = int(entry.get("size") or 0)
    vram = int(entry.get("size_vram") or 0)
    if size <= 0:
        return {"device": "unknown", "gpuFraction": None}
    fraction = max(0.0, min(1.0, vram / size))
    if vram == 0:
        device = "cpu"
    elif fraction >= 0.95:
        device = "gpu"
    else:
        device = "partial"
    return {"device": device, "gpuFraction": round(fraction, 3)}


def diagnose(
    provider: str,
    model: str,
    ollama_url: str,
    force: bool = False,
) -> Dict[str, Any]:
    """
    A verdict on how well Dexter is running, with warnings the UI can show.

    Shape: {ok, severity, device, model, provider, gpu, warnings:[{code, message, action}]}
    `ok` is False whenever there is a warning worth interrupting the user for.
    """
    global _cache, _cache_at

    now = time.time()
    with _lock:
        if _cache is not None and not force and (now - _cache_at) < _CACHE_TTL_SECONDS:
            return _cache

    warnings: List[Dict[str, str]] = []
    device = "unknown"
    gpu_fraction = None
    gpu = _gpu_state()
    free_the_gpu = (
        "Close the other model server to give Dexter the GPU, "
        "or point DEXTER_SMART_MODEL at a smaller model."
    )

    if provider == "ollama":
        loaded = _loaded_models(ollama_url)
        entry = None
        for item in loaded:
            if item.get("model") == model or item.get("name") == model:
                entry = item
                break

        # How much VRAM this model wants. Whether it *fits* is the question that
        # matters - a flat "is some VRAM free" threshold passes happily with
        # 1.5 GB free and a 4.7 GB model, which is the exact case that made
        # Dexter feel broken.
        needed_mb = _model_size_mb(ollama_url, model)
        fits = None
        if gpu is not None and needed_mb:
            fits = gpu["freeMb"] >= (needed_mb + _VRAM_HEADROOM_MB)

        if entry is not None:
            # Resident: report where it actually landed.
            placement = _describe_placement(entry)
            device = placement["device"]
            gpu_fraction = placement["gpuFraction"]

            if device in ("cpu", "partial"):
                hogs = _gpu_hogs()
                if gpu is None:
                    cause = "no NVIDIA GPU was detected"
                    action = "Expect slow replies, or use a smaller model."
                elif fits is False:
                    holder = f" ({', '.join(hogs)})" if hogs else ""
                    cause = (
                        f"it needs about {needed_mb} MB but only {gpu['freeMb']} MB "
                        f"of {gpu['totalMb']} MB VRAM is free{holder}"
                    )
                    action = free_the_gpu
                else:
                    cause = "Ollama chose not to use the GPU"
                    action = "Check Ollama's GPU support and driver version."

                where = "entirely on the CPU" if device == "cpu" else "mostly on the CPU"
                warnings.append({
                    "code": "model_on_cpu",
                    "message": (
                        f"{model} is running {where} because {cause}. "
                        f"Replies will take several seconds instead of under one."
                    ),
                    "action": action,
                })

        elif fits is False and gpu is not None:
            # Not resident yet, and it will not fit when it loads. Warn now
            # rather than after the first ten-second reply.
            hogs = _gpu_hogs()
            holder = f" ({', '.join(hogs)})" if hogs else ""
            warnings.append({
                "code": "gpu_contended",
                "message": (
                    f"{model} needs about {needed_mb} MB but only {gpu['freeMb']} MB "
                    f"of {gpu['totalMb']} MB VRAM is free{holder}. Dexter will fall "
                    f"back to the CPU on the next request, and replies will take "
                    f"several seconds instead of under one."
                ),
                "action": free_the_gpu,
            })

    ok = not warnings
    result: Dict[str, Any] = {
        "ok": ok,
        "severity": "none" if ok else "warning",
        "provider": provider,
        "model": model,
        "device": device,
        "gpuFraction": gpu_fraction,
        "gpu": gpu,
        "warnings": warnings,
        "checkedAt": int(now),
    }

    with _lock:
        _cache = result
        _cache_at = now
    return result


def summary_lines(diagnosis: Dict[str, Any]) -> List[str]:
    """One line per warning, for the startup banner and the log."""
    return [f"{w['message']} {w['action']}".strip() for w in diagnosis.get("warnings", [])]
