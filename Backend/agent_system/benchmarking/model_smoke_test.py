"""
Quick model smoke test for Ollama/Ollama-Cloud endpoints.

Purpose:
- Validate which models are actually reachable before running llm_judge.py.
- Show simple pass/fail + latency per model.

Usage examples:
  python model_smoke_test.py
  python model_smoke_test.py --models gemma4,glm-5.1,qwen3.5
  python model_smoke_test.py --models-file model_candidates.txt
  python model_smoke_test.py --base-url https://your-endpoint --api-key your_key
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List


def _parse_env_file(env_path: Path) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return parsed

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        parsed[key] = value

    return parsed


def _discover_env_files() -> List[Path]:
    candidates: List[Path] = []
    cwd = Path.cwd().resolve()
    for directory in [cwd, *cwd.parents]:
        env_file = directory / ".env"
        if env_file.exists():
            candidates.append(env_file)

    env_local = cwd / ".env.local"
    if env_local.exists():
        candidates.insert(0, env_local)

    deduped: List[Path] = []
    seen = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _load_env_into_process() -> None:
    for env_file in _discover_env_files():
        for key, value in _parse_env_file(env_file).items():
            os.environ.setdefault(key, value)


def _normalize_base_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def _default_models_from_env() -> List[str]:
    models: List[str] = []
    primary = os.getenv("JUDGE_MODEL", "").strip()
    if primary:
        models.append(primary)

    fallback = os.getenv("JUDGE_FALLBACK_MODELS", "")
    print(f"Found fallback models in env: {fallback}")
    models.extend([m.strip() for m in fallback.split(",") if m.strip()])

    if not models:
        models = ["qwen2.5-coder:7b"]
    return models


def _read_models_from_file(path: Path) -> List[str]:
    if not path.exists():
        return []

    models: List[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        models.append(line)
    return models


def _post_v1_chat(base_url: str, api_key: str, model: str, timeout: int) -> Dict:
    endpoint = f"{_normalize_base_url(base_url)}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "temperature": 0,
        "max_tokens": 8,
    }

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def _get_ollama_tags(base_url: str, timeout: int) -> List[str]:
    endpoint = f"{base_url.rstrip('/')}/api/tags"
    req = urllib.request.Request(endpoint, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    parsed = json.loads(body)
    models = parsed.get("models", []) if isinstance(parsed, dict) else []
    names: List[str] = []
    for item in models:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])
    return names


def main() -> None:
    _load_env_into_process()

    parser = argparse.ArgumentParser(description="Smoke-test model availability for judge.")
    parser.add_argument("--base-url", default=os.getenv("JUDGE_BASE_URL", os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")))
    parser.add_argument("--api-key", default=os.getenv("JUDGE_API_KEY", os.getenv("OLLAMA_API_KEY", "ollama")))
    parser.add_argument("--models", default=",".join(_default_models_from_env()), help="Comma-separated model names")
    parser.add_argument("--models-file", default="", help="Optional file with one model per line")
    parser.add_argument("--timeout", type=int, default=int(os.getenv("JUDGE_TIMEOUT", "30")))
    parser.add_argument("--show-tags", action="store_true", help="Also print /api/tags model list")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if args.models_file:
        models_from_file = _read_models_from_file(Path(args.models_file))
        if models_from_file:
            models = models_from_file

    # De-duplicate preserving order
    seen = set()
    deduped: List[str] = []
    for model in models:
        if model in seen:
            continue
        seen.add(model)
        deduped.append(model)
    models = deduped

    print(f"Base URL: {args.base_url}")
    print(f"Models to test: {models}")

    if args.show_tags:
        try:
            available = _get_ollama_tags(args.base_url, args.timeout)
            print(f"/api/tags models ({len(available)}): {available}")
        except Exception as exc:
            print(f"Could not fetch /api/tags: {type(exc).__name__}: {exc}")

    print("\nSmoke test results:")
    ok_count = 0
    for model in models:
        started = time.perf_counter()
        try:
            out = _post_v1_chat(args.base_url, args.api_key, model, args.timeout)
            elapsed_ms = (time.perf_counter() - started) * 1000

            content = ""
            choices = out.get("choices", []) if isinstance(out, dict) else []
            if choices and isinstance(choices[0], dict):
                message = choices[0].get("message", {})
                if isinstance(message, dict):
                    content = str(message.get("content", "")).strip()

            print(f"  [PASS] {model:<35} {elapsed_ms:8.1f} ms  reply={content!r}")
            ok_count += 1
        except urllib.error.HTTPError as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            body = ""
            try:
                body = exc.read().decode("utf-8")
            except Exception:
                pass
            print(f"  [FAIL] {model:<35} {elapsed_ms:8.1f} ms  HTTP {exc.code} {exc.reason} {body[:180]}")
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            print(f"  [FAIL] {model:<35} {elapsed_ms:8.1f} ms  {type(exc).__name__}: {exc}")

    print(f"\nSummary: {ok_count}/{len(models)} models passed")


if __name__ == "__main__":
    main()
