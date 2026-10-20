"""Score the free model pool against an offline challenge set.

Reads a set produced by ``scripts/build_challenge_set.py`` and asks every configured free
provider the same question, then reports format-level metrics that do not need human labels:

  * ``json``      - the answer contained a parseable JSON object
  * ``schema``    - that object carried the field expected for this challenge type
  * ``in_bounds`` - every coordinate fell inside the stitched image
  * latency / tokens / rate limiting

Accuracy still needs ``ground_truth`` in each item; when it is present the script also
reports ``correct``.

Usage (PowerShell):

    $env:GLM_KEY = "<zhipu key>"
    uv run python scripts/score_challenge_set.py --limit 20
    uv run python scripts/score_challenge_set.py --models glm-4.1v-thinking-flash --out scores.json
"""

from __future__ import annotations

import argparse
import io
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
from PIL import Image

# Which JSON field each hCaptcha request type is expected to answer with.
EXPECTED_FIELD = {
    "image_label_binary": "answer",
    "image_label_multiple_choice": "answer",
    "image_label_multi_select": "points",
    "image_label_area_select": "points",
    "image_drag_single": "source",
    "image_drag_multi": "source",
    "image_drag_multi_select": "source",
}

INSTRUCTION = (
    "Answer the challenge. Return ONLY a JSON object, no prose. "
    'For selection tasks use {"points": [[x, y], ...]} with pixel coordinates. '
    'For drag tasks use {"source": [x, y], "target": [x, y]}. '
    'For binary tasks use {"answer": true} or {"answer": false}.'
)


@dataclass
class Target:
    label: str
    base_url: str
    model: str
    api_key: str


@dataclass
class Score:
    model: str
    item_id: str
    request_type: str
    status: int | None = None
    latency_ms: float = 0.0
    tokens: int | None = None
    has_json: bool = False
    has_field: bool = False
    in_bounds: bool | None = None
    correct: bool | None = None
    error: str | None = None
    raw: str = ""


@dataclass
class Tally:
    model: str
    runs: int = 0
    ok: int = 0
    rate_limited: int = 0
    json_ok: int = 0
    field_ok: int = 0
    in_bounds_ok: int = 0
    labelled: int = 0
    correct: int = 0
    latencies: list[float] = field(default_factory=list)


def collect_targets(models: list[str] | None) -> list[Target]:
    targets: list[Target] = []
    glm_key = os.getenv("GLM_KEY") or os.getenv("GLM_API_KEY")
    if glm_key:
        base = os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
        for model in (
            os.getenv("GLM_MODEL_A", "glm-4v-flash"),
            os.getenv("GLM_MODEL_B", "glm-4.1v-thinking-flash"),
            os.getenv("GLM_MODEL_C", "glm-4.6v-flash"),
        ):
            targets.append(Target("zhipu", base, model, glm_key))

    or_key = os.getenv("OR_KEY") or os.getenv("OPENROUTER_API_KEY")
    if or_key:
        targets.append(
            Target("openrouter", "https://openrouter.ai/api/v1", "google/gemma-4-31b-it:free", or_key)
        )

    if models:
        targets = [t for t in targets if t.model in models]
    return targets


def stitch(images: list[Path]) -> tuple[bytes, tuple[int, int]]:
    """Stack task images vertically into one picture and return (png bytes, (w, h))."""
    if not images:
        return b"", (0, 0)
    if len(images) == 1:
        data = images[0].read_bytes()
        with Image.open(io.BytesIO(data)) as img:
            return data, img.size

    opened = [Image.open(path).convert("RGB") for path in images]
    width = max(img.width for img in opened)
    height = sum(img.height for img in opened)
    canvas = Image.new("RGB", (width, height), "white")
    offset = 0
    for img in opened:
        canvas.paste(img, (0, offset))
        offset += img.height
    buf = io.BytesIO()
    canvas.save(buf, "PNG")
    return buf.getvalue(), (width, height)


def _iter_points(obj: object, out: list[list[float]]) -> None:
    if isinstance(obj, dict):
        if "x" in obj and "y" in obj:
            try:
                out.append([float(obj["x"]), float(obj["y"])])
            except (TypeError, ValueError):
                pass
            return
        for value in obj.values():
            _iter_points(value, out)
    elif isinstance(obj, list):
        if len(obj) == 2 and all(isinstance(v, (int, float)) for v in obj):
            out.append([float(obj[0]), float(obj[1])])
            return
        for value in obj:
            _iter_points(value, out)


def score_one(client: httpx.Client, target: Target, item: dict, set_root: Path) -> Score:
    result = Score(model=target.model, item_id=item["id"], request_type=item["request_type"])
    item_dir = set_root.joinpath("items", item["id"])
    images = [item_dir.joinpath(name) for name in item.get("images") or []]
    try:
        data, size = stitch(images)
    except OSError as err:
        result.error = f"image error: {err}"
        return result
    if not data:
        result.error = "no images in item"
        return result

    b64 = __import__("base64").b64encode(data).decode()
    prompt = f"{item.get('prompt') or 'Solve the challenge.'}\n\n{INSTRUCTION}"
    payload = {
        "model": target.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "temperature": 0,
    }
    headers = {"Content-Type": "application/json"}
    if target.api_key:
        headers["Authorization"] = f"Bearer {target.api_key}"

    started = time.monotonic()
    try:
        response = client.post(
            f"{target.base_url.rstrip('/')}/chat/completions", headers=headers, json=payload
        )
        result.latency_ms = round((time.monotonic() - started) * 1000, 1)
        result.status = response.status_code
        if response.status_code != 200:
            result.error = response.text[:200]
            return result

        body = response.json()
        content = body["choices"][0]["message"].get("content") or ""
        result.raw = content[:400]
        result.tokens = (body.get("usage") or {}).get("total_tokens")

        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end == -1:
            return result
        parsed = json.loads(content[start : end + 1])
        result.has_json = True

        expected = EXPECTED_FIELD.get(item["request_type"], "points")
        result.has_field = any(key in parsed for key in (expected, "points", "source", "answer"))

        points: list[list[float]] = []
        _iter_points(parsed, points)
        if points and size[0] and size[1]:
            result.in_bounds = all(0 <= p[0] <= size[0] and 0 <= p[1] <= size[1] for p in points)

        truth = item.get("ground_truth")
        if truth is not None and points:
            try:
                result.correct = sorted(truth) == sorted(
                    points if isinstance(points[0], list) else [points]
                )
            except TypeError:
                result.correct = None
    except Exception as err:  # noqa: BLE001 - scoring must continue
        result.latency_ms = round((time.monotonic() - started) * 1000, 1)
        result.error = f"{type(err).__name__}: {err}"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", type=Path, default=Path("challenge-set"), help="evaluation set root")
    parser.add_argument("--models", nargs="*", help="only score these model ids")
    parser.add_argument("--limit", type=int, default=0, help="score at most N items (0 = all)")
    parser.add_argument("--out", type=Path, help="write raw results as JSON")
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    index_path = args.set.joinpath("index.json")
    if not index_path.exists():
        print(f"no index at {index_path}")
        print("hint: run scripts/build_challenge_set.py first")
        return
    index = json.loads(index_path.read_text(encoding="utf-8"))
    items = index.get("items") or []
    if args.limit:
        items = items[: args.limit]
    if not items:
        print("evaluation set is empty")
        return

    targets = collect_targets(args.models)
    if not targets:
        print("no targets configured: set GLM_KEY / OR_KEY")
        return

    print(f"set={args.set} items={len(items)} targets={len(targets)}")
    scores: list[Score] = []
    tally: dict[str, Tally] = {t.model: Tally(model=t.model) for t in targets}

    with httpx.Client(timeout=args.timeout, trust_env=False) as client:
        for target in targets:
            for item in items:
                score = score_one(client, target, item, args.set)
                scores.append(score)
                row = tally[target.model]
                row.runs += 1
                if score.status == 200:
                    row.ok += 1
                    row.latencies.append(score.latency_ms)
                    row.json_ok += int(score.has_json)
                    row.field_ok += int(score.has_field)
                    row.in_bounds_ok += int(bool(score.in_bounds))
                    if score.correct is not None:
                        row.labelled += 1
                        row.correct += int(score.correct)
                elif score.status in (429, 503):
                    row.rate_limited += 1
                print(
                    f"{target.model:<30}{item['id'][:28]:<30}{item['request_type'][:26]:<28}"
                    f"{str(score.status or '-'):>5}{score.latency_ms:>8.0f}ms"
                    f"  json={int(score.has_json)} field={int(score.has_field)}"
                    f" bounds={score.in_bounds if score.in_bounds is not None else '-'}"
                )

    print("\n--- summary ---")
    for model, row in tally.items():
        lat = sorted(row.latencies)
        p50 = f"{lat[len(lat) // 2]:.0f}ms" if lat else "-"
        line = (
            f"{model:<30} ok={row.ok}/{row.runs} limited={row.rate_limited} "
            f"json={row.json_ok} field={row.field_ok} in_bounds={row.in_bounds_ok} p50={p50}"
        )
        if row.labelled:
            line += f" correct={row.correct}/{row.labelled}"
        print(line)

    if args.out:
        args.out.write_text(
            json.dumps([asdict(s) for s in scores], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"raw scores written to {args.out}")


if __name__ == "__main__":
    main()
