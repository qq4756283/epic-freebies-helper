"""Probe the free LLM pool before wiring it into the captcha router.

Runs the same image + prompt against every configured free provider slot and
reports: reachability, latency, JSON compliance and pixel error against a known
ground truth. Use it to fill the routing table in
`docs/refactor-plan-2026-10.md` (section 4.1) with measurements instead of
guesses.

Usage (PowerShell):
    $env:GLM_KEY  = "<zhipu standard api key>"
    $env:OR_KEY   = "<openrouter key>"          # optional
    uv run python scripts/probe_llm_pool.py
    uv run python scripts/probe_llm_pool.py --models glm-4.6v-flash --out probe.json

Nothing is written to the repository unless --out is given.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import time
from dataclasses import asdict, dataclass, field

import httpx
from PIL import Image, ImageDraw

# Ground truth for the synthetic challenge image: two red dots.
GROUND_TRUTH = [(200, 150), (900, 500)]
IMAGE_SIZE = (1280, 720)

# Grid task (closer to hCaptcha `image_label_multi_select`): 3x3 cells, dots in cells 2 and 6.
GRID_CELL = 128
GRID_TRUTH = [2, 6]

PROMPT = (
    'Return ONLY a JSON object, no prose: {"count": <number of red circles>, '
    '"points": [[x, y], ...]} where x,y are pixel coordinates of each red circle center '
    f"in the {IMAGE_SIZE[0]}x{IMAGE_SIZE[1]} image."
)

GRID_PROMPT = (
    "This image is a 3x3 grid of cells numbered 1-9 (left to right, top to bottom). "
    'Return ONLY a JSON object, no prose: {"cells": [<cell numbers that contain a red circle>]}.'
)


@dataclass
class Target:
    label: str
    base_url: str
    model: str
    api_key: str
    image_style: str = "dataurl"  # "dataurl" | "raw" (Zhipu accepts raw base64)


@dataclass
class Result:
    label: str
    model: str
    status: int | None = None
    latency_ms: float = 0.0
    total_tokens: int | None = None
    raw: str = ""
    parsed_count: int | None = None
    points: list[list[float]] = field(default_factory=list)
    pixel_error: list[float] = field(default_factory=list)
    cells: list[int] = field(default_factory=list)
    cells_correct: bool | None = None
    error: str | None = None


def build_image() -> bytes:
    """Synthetic challenge-like image with known dot coordinates."""
    img = Image.new("RGB", IMAGE_SIZE, "white")
    draw = ImageDraw.Draw(img)
    for x in range(60, IMAGE_SIZE[0] - 60, 180):
        draw.rectangle([x, 560, x + 120, 680], outline=(120, 120, 120), width=3)
        draw.text((x + 10, 620), f"Label {x}", fill=(60, 60, 60))
    draw.line([50, 50, IMAGE_SIZE[0] - 50, 50], fill=(0, 0, 0), width=2)
    for cx, cy in GROUND_TRUTH:
        draw.ellipse([cx - 20, cy - 20, cx + 20, cy + 20], fill=(220, 30, 30))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def collect_targets(model_filter: list[str] | None) -> list[Target]:
    targets: list[Target] = []

    glm_key = os.getenv("GLM_KEY") or os.getenv("GLM_API_KEY")
    if glm_key:
        glm_models = [
            os.getenv("GLM_MODEL_A", "glm-4.6v-flash"),
            os.getenv("GLM_MODEL_B", "glm-4.1v-thinking-flash"),
            os.getenv("GLM_MODEL_C", "glm-4v-flash"),
        ]
        for model in glm_models:
            targets.append(
                Target(
                    label="zhipu",
                    base_url=os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
                    model=model,
                    api_key=glm_key,
                )
            )

    or_key = os.getenv("OR_KEY") or os.getenv("OPENROUTER_API_KEY")
    if or_key:
        for model in ("google/gemma-4-31b-it:free", "nvidia/nemotron-3.5-super-vl:free"):
            targets.append(
                Target(
                    label="openrouter",
                    base_url="https://openrouter.ai/api/v1",
                    model=model,
                    api_key=or_key,
                )
            )

    local_base = os.getenv("LOCAL_VLM_BASE")
    if local_base:
        targets.append(
            Target(
                label="local",
                base_url=local_base,
                model=os.getenv("LOCAL_VLM", "qwen3-vl:2b"),
                api_key="",
            )
        )

    if model_filter:
        targets = [t for t in targets if t.model in model_filter]
    return targets


def build_grid_image() -> bytes:
    """3x3 numbered grid with dots in GRID_TRUTH cells (hCaptcha multi-select shape)."""
    size = GRID_CELL * 3
    img = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(img)
    for index in range(9):
        col, row = index % 3, index // 3
        x0, y0 = col * GRID_CELL, row * GRID_CELL
        draw.rectangle([x0, y0, x0 + GRID_CELL - 1, y0 + GRID_CELL - 1], outline=(150, 150, 150), width=2)
        draw.text((x0 + 6, y0 + 4), str(index + 1), fill=(40, 40, 40))
        if index + 1 in GRID_TRUTH:
            cx, cy = x0 + GRID_CELL // 2, y0 + GRID_CELL // 2
            draw.ellipse([cx - 18, cy - 18, cx + 18, cy + 18], fill=(220, 30, 30))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def probe(client: httpx.Client, target: Target, image_b64: str, task: str = "points") -> Result:
    url = image_b64 if target.image_style == "raw" else f"data:image/png;base64,{image_b64}"
    prompt = PROMPT if task == "points" else GRID_PROMPT
    payload = {
        "model": target.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "temperature": 0,
    }
    headers = {"Content-Type": "application/json"}
    if target.api_key:
        headers["Authorization"] = f"Bearer {target.api_key}"

    result = Result(label=target.label, model=target.model)
    started = time.monotonic()
    try:
        response = client.post(f"{target.base_url.rstrip('/')}/chat/completions", headers=headers, json=payload)
        result.latency_ms = round((time.monotonic() - started) * 1000, 1)
        result.status = response.status_code
        if response.status_code != 200:
            result.error = response.text[:300]
            return result

        body = response.json()
        content = body["choices"][0]["message"].get("content") or ""
        result.raw = content[:600]
        result.total_tokens = (body.get("usage") or {}).get("total_tokens")
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end == -1:
            result.error = "no json object in response"
            return result
        parsed = json.loads(content[start : end + 1])
        if task == "grid":
            cells = parsed.get("cells") or []
            result.cells = sorted(int(c) for c in cells if str(c).isdigit())
            result.cells_correct = result.cells == sorted(GRID_TRUTH)
            return result

        result.parsed_count = parsed.get("count")
        points = parsed.get("points") or []
        result.points = points
        for gx, gy in GROUND_TRUTH:
            if not points:
                continue
            near = min(points, key=lambda p: (float(p[0]) - gx) ** 2 + (float(p[1]) - gy) ** 2)
            distance = ((float(near[0]) - gx) ** 2 + (float(near[1]) - gy) ** 2) ** 0.5
            result.pixel_error.append(round(distance, 1))
    except Exception as err:  # noqa: BLE001 - a probe must never abort the run
        result.latency_ms = round((time.monotonic() - started) * 1000, 1)
        result.error = f"{type(err).__name__}: {err}"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="*", help="only probe these model ids")
    parser.add_argument("--out", help="write JSON results to this path")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--task", choices=["points", "grid"], default="points", help="challenge shape to probe")
    parser.add_argument("--repeat", type=int, default=1, help="repeat each target N times (measures rate limiting)")
    args = parser.parse_args()

    targets = collect_targets(args.models)
    if not targets:
        print("no targets configured: set GLM_KEY / OR_KEY / LOCAL_VLM_BASE")
        return

    image_bytes = build_grid_image() if args.task == "grid" else build_image()
    image_b64 = base64.b64encode(image_bytes).decode()
    print(f"probe targets={len(targets)} task={args.task} repeat={args.repeat} b64_len={len(image_b64)}")
    if args.task == "grid":
        print(f"ground truth: cells={sorted(GRID_TRUTH)} (3x3 grid, {GRID_CELL}px cells)")
    else:
        print(f"ground truth: points={GROUND_TRUTH} in {IMAGE_SIZE[0]}x{IMAGE_SIZE[1]}")
    print(f"{'provider':<11}{'model':<32}{'status':>7}{'ms':>9}{'tokens':>8}  result")

    results: list[Result] = []
    with httpx.Client(timeout=args.timeout, trust_env=False) as client:
        for target in targets:
            for _ in range(max(1, args.repeat)):
                result = probe(client, target, image_b64, args.task)
                results.append(result)
                verdict = "OK"
                if result.error:
                    verdict = result.error.replace("\n", " ")[:80]
                elif args.task == "grid":
                    verdict = f"cells={result.cells} correct={result.cells_correct}"
                elif result.pixel_error:
                    verdict = f"count={result.parsed_count} err_px={result.pixel_error}"
                print(
                    f"{result.label:<11}{result.model:<32}{str(result.status or '-'):>7}"
                    f"{result.latency_ms:>9.0f}{str(result.total_tokens or '-'):>8}  {verdict}"
                )

    if results:
        print("\n--- summary ---")
        for model in dict.fromkeys(r.model for r in results):
            rows = [r for r in results if r.model == model]
            ok = [r for r in rows if r.status == 200]
            limited = [r for r in rows if r.status in (429, 503)]
            lat = sorted(r.latency_ms for r in ok)
            line = f"{model:<32} ok={len(ok)}/{len(rows)} rate_limited={len(limited)}"
            if lat:
                line += f" latency_p50={lat[len(lat) // 2]:.0f}ms"
            if args.task == "grid":
                correct = sum(1 for r in ok if r.cells_correct)
                line += f" correct={correct}/{len(ok)}"
            elif ok and all(r.pixel_error for r in ok):
                errs = sorted(min(r.pixel_error) for r in ok)
                line += f" err_px_min_p50={errs[len(errs) // 2]:.0f}"
            print(line)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump([asdict(r) for r in results], fh, ensure_ascii=False, indent=2)
        print(f"results written to {args.out}")


if __name__ == "__main__":
    main()
