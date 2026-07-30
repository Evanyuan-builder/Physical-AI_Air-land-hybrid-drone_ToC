"""指令契约 —— 地面大脑理解完人话之后，回传给板子的那一个结构。

徐梓阳那条「回传通道」吃的就是 to_wire() 的最小载荷：
    {"action": ..., "target_id": ..., "form": ...}
其余字段（reason / needs_confirm / candidates）是给地面大屏和防翻车逻辑用的，不回传板子。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

# 板子能听懂的高层动作（跟徐梓阳最终对齐时再定死这几个词）
ACTIONS = {"lock", "follow", "orbit", "shoot", "return", "search", "idle"}
# 两栖形态：飞 / 走 / 自己看着办
FORMS = {"fly", "walk", "auto"}


@dataclass
class Intent:
    action: str = "idle"
    target_id: Optional[int] = None
    form: str = "auto"
    reason: str = ""            # 一句给现场大屏的话，证明「身体听懂了人」
    confidence: float = 1.0
    needs_confirm: bool = False  # 防翻车：True → 大屏让用户点选/改，不直接下发
    candidates: list = field(default_factory=list)  # 歧义时的候选 target_id

    def to_wire(self) -> dict:
        """回传给板子的最小载荷。"""
        return {"action": self.action, "target_id": self.target_id, "form": self.form}

    def to_dict(self) -> dict:
        return asdict(self)

    def pretty(self) -> str:
        tid = "—" if self.target_id is None else f"#{self.target_id}"
        return f"[{self.action}] 目标{tid} 形态={self.form} · {self.reason}"


def coerce(raw: dict) -> Intent:
    """把 LLM 吐出来的 dict 收敛成一个合法 Intent（脏输入也不炸）。"""
    action = str(raw.get("action", "idle")).strip().lower()
    if action not in ACTIONS:
        action = "idle"
    form = str(raw.get("form", "auto")).strip().lower()
    if form not in FORMS:
        form = "auto"
    tid = raw.get("target_id", None)
    try:
        tid = int(tid) if tid is not None and str(tid) != "" else None
    except (ValueError, TypeError):
        tid = None
    cands = raw.get("candidates") or []
    cands = [int(c) for c in cands if str(c).lstrip("-").isdigit()]
    return Intent(
        action=action,
        target_id=tid,
        form=form,
        reason=str(raw.get("reason", "")).strip(),
        confidence=float(raw.get("confidence", 1.0) or 1.0),
        needs_confirm=bool(raw.get("needs_confirm", False)) or len(cands) > 1,
        candidates=cands,
    )
