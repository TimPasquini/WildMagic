"""Spike: int8 weight-only quantization of SDXL on Arc XPU via torchao. Measures peak
VRAM and checks the image isn't degenerate. Throwaway diagnostic."""

from __future__ import annotations

import time
from pathlib import Path

import torch
from diffusers import StableDiffusionXLPipeline
from torchao.quantization import Int8WeightOnlyConfig, quantize_

from generate_portrait import MODEL_ID, NEGATIVE, build_prompt, pick_device

device, dtype = pick_device()
print(f"device={device} dtype={dtype}", flush=True)

t = time.time()
pipe = StableDiffusionXLPipeline.from_pretrained(
    MODEL_ID, torch_dtype=dtype, use_safetensors=True, variant="fp16"
)
print(f"loaded {time.time() - t:.1f}s", flush=True)

t = time.time()
quantize_(pipe.unet, Int8WeightOnlyConfig())
quantize_(pipe.text_encoder_2, Int8WeightOnlyConfig())
print(f"quantized {time.time() - t:.1f}s", flush=True)

pipe = pipe.to(device)
pipe.enable_attention_slicing()
pipe.enable_vae_slicing()

if device == "xpu":
    torch.xpu.reset_peak_memory_stats()
t = time.time()
img = pipe(
    prompt=build_prompt("a hooded desert nomad with sun-dark skin and indigo wraps"),
    negative_prompt=NEGATIVE,
    num_inference_steps=20,
    guidance_scale=6.0,
    height=768,
    width=768,
    generator=torch.Generator("cpu").manual_seed(21),
).images[0]
print(f"generated {time.time() - t:.1f}s", flush=True)
if device == "xpu":
    print(f"peak_xpu_GB {torch.xpu.max_memory_allocated() / 1e9:.2f}", flush=True)

ext = img.getextrema()
spread = max(hi - lo for lo, hi in ext) if isinstance(ext[0], tuple) else ext[1] - ext[0]
print(f"spread {spread}", flush=True)
out = Path(__file__).resolve().parent / "out" / "quant_test.png"
img.save(out)
print(f"saved {out}", flush=True)
