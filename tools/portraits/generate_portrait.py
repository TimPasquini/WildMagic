"""Standalone character-portrait generator (SDXL).

This deliberately lives outside the game's runtime: it runs in its own venv with the
heavy torch/diffusers stack (see tools/portraits/README.md), so the game process stays
light and can shell out to it. Run it directly to test, or import generate_portrait().

Usage:
    python generate_portrait.py --description "a wiry bone-singer hung with carved charms" \
        --out out/test.png --seed 7

Picks the Intel Arc XPU if torch sees it, else CUDA, else CPU (slow).
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import torch

MODEL_ID = "stabilityai/stable-diffusion-xl-base-1.0"

# A consistent house style so portraits read as one game's cast, not random art.
STYLE_SUFFIX = (
    "character portrait, head and shoulders, fantasy illustration, painterly, "
    "expressive face, dramatic rim lighting, bright tones, detailed"
)
NEGATIVE = (
    "lowres, blurry, deformed, extra limbs, bad anatomy, text, watermark, signature, "
    "frame, multiple people, photograph"
)


def pick_device() -> tuple[str, torch.dtype]:
    # bfloat16 on the Arc: same memory as fp16 but fp32's exponent range, so the SDXL
    # UNet doesn't overflow to NaN (which fp16 does on XPU -> black images). CUDA is
    # fine on fp16 (paired with the fp16-fix VAE); CPU must use fp32.
    if hasattr(torch, "xpu") and torch.xpu.is_available():
        return "xpu", torch.bfloat16
    if torch.cuda.is_available():
        return "cuda", torch.float16
    return "cpu", torch.float32


def build_prompt(description: str) -> str:
    description = description.strip().rstrip(".")
    return f"{description}. {STYLE_SUFFIX}. {description}"


FP16_VAE_ID = "madebyollin/sdxl-vae-fp16-fix"


# Cached pipeline so a long-lived worker loads the model once and reuses it across
# many portraits. CLI use loads it once per process too.
_PIPE = None


def load_pipeline():
    """Build (and cache) the SDXL pipeline on the best available device."""
    global _PIPE
    if _PIPE is not None:
        return _PIPE
    from diffusers import AutoencoderKL, StableDiffusionXLPipeline

    device, dtype = pick_device()
    print(f"[portrait] device={device} dtype={dtype}", flush=True)
    load_start = time.time()
    kwargs = {}
    if dtype == torch.float16:
        # SDXL's stock fp16 VAE overflows to NaN on many backends (incl. Arc XPU),
        # decoding to a solid black image. This drop-in VAE is rescaled to stay in fp16
        # range, so everything stays fp16 (fast, dtype-consistent) without overflow.
        kwargs["vae"] = AutoencoderKL.from_pretrained(FP16_VAE_ID, torch_dtype=dtype)
    pipe = StableDiffusionXLPipeline.from_pretrained(
        MODEL_ID,
        torch_dtype=dtype,
        use_safetensors=True,
        # Only the fp16-variant weight files are cached; load them and let torch_dtype
        # cast to the target (bf16 on XPU). CPU fp32 still loads these and upcasts.
        variant="fp16",
        **kwargs,
    )
    _apply_quant(pipe)
    pipe = pipe.to(device)
    # Keep peak VRAM under ~8GB on the Arc A750.
    pipe.enable_attention_slicing()
    pipe.enable_vae_slicing()
    print(f"[portrait] pipeline loaded in {time.time() - load_start:.1f}s", flush=True)
    _PIPE = pipe
    return _PIPE


def _apply_quant(pipe) -> None:
    """Weight-only quantize the big modules (UNet + text encoder 2) so SDXL fits the
    Arc's 8GB dedicated VRAM instead of spilling into shared system memory. int8 on the
    A750 drops peak VRAM ~8GB -> ~5.3GB at the same speed and ~same quality. Controlled
    by WILDMAGIC_PORTRAIT_QUANT (int8 | fp8 | none); falls back to unquantized bf16 if
    torchao is missing or the backend rejects it."""
    quant = os.environ.get("WILDMAGIC_PORTRAIT_QUANT", "int8").strip().lower()
    if quant in ("", "none", "off", "0"):
        return
    try:
        from torchao.quantization import (
            Float8WeightOnlyConfig,
            Int8WeightOnlyConfig,
            quantize_,
        )

        config = (
            Float8WeightOnlyConfig()
            if quant in ("fp8", "float8")
            else Int8WeightOnlyConfig()
        )
        quantize_(pipe.unet, config)
        quantize_(pipe.text_encoder_2, config)
        print(f"[portrait] quantized weights ({quant})", flush=True)
    except Exception as exc:
        print(
            f"[portrait] quantization '{quant}' unavailable, using bf16: {exc}",
            flush=True,
        )


def generate_portrait(
    description: str,
    out_path: str | Path,
    *,
    steps: int = 28,
    guidance: float = 6.0,
    size: int = 768,
    seed: int | None = None,
) -> Path:
    pipe = load_pipeline()

    generator = None
    if seed is not None:
        generator = torch.Generator(device="cpu").manual_seed(seed)

    gen_start = time.time()
    image = pipe(
        prompt=build_prompt(description),
        negative_prompt=NEGATIVE,
        num_inference_steps=steps,
        guidance_scale=guidance,
        height=size,
        width=size,
        generator=generator,
    ).images[0]
    print(f"[portrait] generated in {time.time() - gen_start:.1f}s", flush=True)

    # A near-uniform image means the decode produced garbage — on the Arc this is the
    # signature of VRAM exhaustion (a silent black square, no exception). Treat it as a
    # failure so the caller can free VRAM and retry rather than save a black PNG.
    extrema = image.getextrema()
    if isinstance(extrema[0], tuple):
        spread = max(hi - lo for lo, hi in extrema)
    else:
        spread = extrema[1] - extrema[0]
    if spread < 8:
        raise RuntimeError(
            "degenerate (near-uniform) image — likely GPU VRAM exhaustion; "
            "free the GPU and retry"
        )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    print(f"[portrait] saved {out_path}", flush=True)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a character portrait (SDXL)."
    )
    parser.add_argument("--description", required=True, help="Physical description.")
    parser.add_argument("--out", default="out/portrait.png", help="Output PNG path.")
    parser.add_argument("--steps", type=int, default=28)
    parser.add_argument("--guidance", type=float, default=6.0)
    parser.add_argument("--size", type=int, default=768)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    generate_portrait(
        args.description,
        args.out,
        steps=args.steps,
        guidance=args.guidance,
        size=args.size,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
