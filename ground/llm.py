"""极简 LLM 客户端（OpenAI 兼容），只用标准库，不引任何依赖。

默认走七牛 MaaS 的 deepseek-v4-flash（Clash 已放行、便宜快）。
配置从 ground/.env 读（LLM_API_KEY / LLM_BASE_URL / LLM_MODEL）。
连不上就抛异常，让 grounding 层降级到规则匹配 —— 演示永远不因为没网翻车。
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from pathlib import Path


def load_env(env_path: str | Path | None = None) -> None:
    """把 .env 里的键读进 os.environ（已存在的不覆盖）。"""
    p = Path(env_path) if env_path else Path(__file__).parent / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


class LLMError(RuntimeError):
    pass


def chat_json(system: str, user: str, timeout: float = 20.0) -> dict:
    """要一段 JSON 回来。thinking 关掉跑快档；解析失败/网络失败都抛 LLMError。"""
    load_env()
    key = os.environ.get("LLM_API_KEY")
    base = os.environ.get("LLM_BASE_URL", "https://api.qnaigc.com/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL", "deepseek/deepseek-v4-flash")
    if not key:
        raise LLMError("没有 LLM_API_KEY，走规则降级")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.1,
        "thinking": {"type": "disabled"},  # DeepSeek V4 生效，其他 provider 安全忽略
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMError(f"LLM 请求失败：{e}") from e

    try:
        content = body["choices"][0]["message"]["content"]
        return json.loads(content)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise LLMError(f"LLM 返回解析失败：{e}") from e


def available() -> bool:
    load_env()
    return bool(os.environ.get("LLM_API_KEY"))
