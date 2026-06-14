"""Robust SDXL fetch: sequential, resumable, retried. Separated from generation so a
flaky network doesn't waste GPU warmup. Downloads only the fp16 variant (~6.6GB)."""

from __future__ import annotations

import os
import time

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")

from huggingface_hub import snapshot_download

# (repo, allow_patterns). The base model's fp16 weights only (skip fp32 duplicates),
# plus the fp16-fix VAE that avoids SDXL's black-image overflow.
TARGETS = [
    (
        "stabilityai/stable-diffusion-xl-base-1.0",
        ["**/*.json", "**/*.txt", "**/*.fp16.safetensors"],
    ),
    ("madebyollin/sdxl-vae-fp16-fix", ["*.json", "*.safetensors"]),
]


def fetch(repo: str, allow: list[str]) -> None:
    for attempt in range(1, 13):
        try:
            path = snapshot_download(repo, allow_patterns=allow, max_workers=1)
            print(f"DOWNLOAD_OK {repo} {path}")
            return
        except Exception as exc:
            print(
                f"[fetch] {repo} attempt {attempt} failed: {type(exc).__name__}: {exc}"
            )
            time.sleep(5)
    print(f"DOWNLOAD_FAILED {repo}")
    raise SystemExit(1)


for repo, allow in TARGETS:
    fetch(repo, allow)
