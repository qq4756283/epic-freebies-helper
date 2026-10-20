"""Build an offline challenge evaluation set from hcaptcha-challenger cache dumps.

The runtime caches every solved challenge under
``app/volumes/hcaptcha/.challenge/<request_type>/<prompt>/<date>/<time>_captcha.json``
and uploads that directory as the ``epic-runtime-*`` artifact. This script turns such a
cache tree into a stable, replayable evaluation set:

    challenge-set/
      index.json                 # list of items with metadata
      items/<id>/meta.json       # per-challenge metadata (prompt, type, task keys)
      items/<id>/image_00.png    # decoded task images

Usage (PowerShell):

    # 从当前运行的 volume 目录构建
    uv run python scripts/build_challenge_set.py

    # 从解压出来的 artifact 目录构建
    uv run python scripts/build_challenge_set.py --source <unzipped>/app/volumes/hcaptcha/.challenge --out challenge-set

    # 只保留带图片的条目
    uv run python scripts/build_challenge_set.py --require-images

Nothing in the repository is modified; the output directory is yours to keep or attach to a run.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path

DEFAULT_SOURCE = Path("app/volumes/hcaptcha/.challenge")
DEFAULT_OUT = Path("challenge-set")

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def _slug(text: str, limit: int = 48) -> str:
    cleaned = _SAFE_NAME.sub("_", text).strip("_")
    return (cleaned or "item")[:limit]


def _decode_data_uri(uri: str) -> bytes | None:
    """Decode a ``data:image/...;base64,`` URI; return None for http(s) references."""
    if not isinstance(uri, str) or not uri.startswith("data:"):
        return None
    try:
        _, b64 = uri.split(",", 1)
        return base64.b64decode(b64)
    except (ValueError, TypeError):
        return None


def _prompt_of(payload: dict) -> str:
    question = payload.get("requester_question") or {}
    if isinstance(question, dict):
        return str(question.get("en") or next(iter(question.values()), "") or "")
    return str(question)


def build_item(payload_path: Path, item_dir: Path, require_images: bool) -> dict | None:
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    tasklist = payload.get("tasklist") or []
    images: list[str] = []
    task_keys: list[str] = []
    for index, task in enumerate(tasklist):
        if not isinstance(task, dict):
            continue
        task_keys.append(str(task.get("task_key") or ""))
        raw = _decode_data_uri(task.get("datapoint_uri") or "")
        if raw:
            image_path = item_dir.joinpath(f"image_{index:02d}.png")
            image_path.write_bytes(raw)
            images.append(image_path.name)

    if require_images and not images:
        for name in images:
            item_dir.joinpath(name).unlink(missing_ok=True)
        return None

    meta = {
        "source": str(payload_path),
        "request_type": (payload.get("request_type") or ""),
        "prompt": _prompt_of(payload),
        "task_count": len(tasklist),
        "task_keys": [key for key in task_keys if key],
        "images": images,
        "ground_truth": None,  # fill in by hand if you want accuracy, not just format scoring
    }
    item_dir.joinpath("meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="challenge cache root")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    parser.add_argument(
        "--require-images", action="store_true", help="skip payloads without inline images"
    )
    args = parser.parse_args()

    if not args.source.exists():
        print(f"source not found: {args.source}")
        print("hint: pass --source with an unzipped epic-runtime-* artifact's .challenge directory")
        return

    payload_paths = sorted(args.source.rglob("*_captcha.json"))
    print(f"found {len(payload_paths)} payload file(s) under {args.source}")
    if not payload_paths:
        return

    args.out.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    for payload_path in payload_paths:
        digest = hashlib.sha256(str(payload_path).encode("utf-8")).hexdigest()[:12]
        item_id = f"{_slug(payload_path.parent.name)}_{digest}"
        item_dir = args.out.joinpath("items", item_id)
        item_dir.mkdir(parents=True, exist_ok=True)
        meta = build_item(payload_path, item_dir, args.require_images)
        if meta is None:
            continue
        meta["id"] = item_id
        items.append(meta)

    index = {
        "source": str(args.source),
        "count": len(items),
        "by_type": {},
        "items": items,
    }
    for meta in items:
        key = meta["request_type"] or "unknown"
        index["by_type"][key] = index["by_type"].get(key, 0) + 1

    args.out.joinpath("index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {len(items)} item(s) to {args.out / 'index.json'}")
    for key, count in sorted(index["by_type"].items()):
        print(f"  {key}: {count}")


if __name__ == "__main__":
    main()
