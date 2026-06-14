# Character Portraits (experimental)

Generates a character portrait from a physical description using **SDXL**, chosen to fit
the Intel Arc A750 (8GB). This is intentionally **isolated from the game's runtime**: the
heavy `torch` + `diffusers` stack lives in its own venv, and the game shells out to this
script rather than importing torch. That keeps the game process light and the GPU work
out of process.

Status: **test harness** (not yet wired into the creation screen).

## Setup (one-time)

A dedicated venv lives outside the repo so it isn't committed and doesn't touch the
game's environment:

```
python -m venv C:\Games\wm_image_venv
C:\Games\wm_image_venv\Scripts\python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/xpu
C:\Games\wm_image_venv\Scripts\python -m pip install "diffusers>=0.31" transformers accelerate safetensors pillow torchao
```

`torchao` provides the int8 weight quantization (default) that keeps SDXL inside the
A750's 8GB; without it the generator falls back to bf16, which overflows into shared
memory.

The `xpu` wheels pull Intel's SYCL/oneMKL runtime so torch can use the Arc GPU. First run
downloads SDXL (~6.6GB fp16) into the Hugging Face cache.

## Run a test

```
C:\Games\wm_image_venv\Scripts\python tools\portraits\generate_portrait.py \
    --description "a wiry bone-singer hung with carved bone charms" \
    --out tools\portraits\out\test.png --seed 7
```

Device selection is automatic: Arc XPU if torch sees it, else CUDA, else CPU (slow).

## Why not Qwen-Image?

Qwen-Image is ~20B + a 7B text encoder; even GGUF-quantized it overflows 8GB and needs
CPU offload (minutes per image) on the A750. SDXL fits in VRAM and stays fast. Revisit if
the hardware changes.
