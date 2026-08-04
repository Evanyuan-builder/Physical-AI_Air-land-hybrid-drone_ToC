"""指令契约 —— 地面大脑理解完人话之后，回传给板子的那一个结构。

徐梓阳那条「回传通道」吃的就是 to_wire() 的最小载荷：
    {"action": ..., "target_id": ..., "form": ...}
其余字段（reason / needs_confirm / candidates）是给地面大屏和防翻车逻辑用的，不回传板子。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

# 板子能听懂的高层动作（跟徐梓阳最终对齐时再定死这几个词）
# stop 是单独一个动作，不能并进 idle：idle=没听懂/待命（不下发），
# stop=用户明确让停（必须下发 follow_enabled=false）。混在一起会出现"说了停但没停"。
ACTIONS = {"lock", "follow", "orbit", "shoot", "return", "search", "stop", "idle"}
# 两栖形态：飞 / 走 / 自己看着办
FORMS = {"fly", "walk", "auto"}
# 云台/取景模式 —— 对上队里 control_server.py 的 mode 字段（level / pitch）
CAMERA_MODES = {"level", "pitch", "auto"}

# 板子接受的高度范围（control_server.py 的 FollowState.HEIGHT_MIN_M / MAX_M）
HEIGHT_MIN_M = 0.5
HEIGHT_MAX_M = 5.0


@dataclass
class Intent:
    action: str = "idle"
    target_id: Optional[int] = None
    form: str = "auto"
    reason: str = ""            # 一句给现场大屏的话，证明「身体听懂了人」
    confidence: float = 1.0
    needs_confirm: bool = False  # 防翻车：True → 大屏让用户点选/改，不直接下发
    candidates: list = field(default_factory=list)  # 歧义时的候选 target_id
    # —— 下面两个是对上队里 /api/state 的：一句话不光要能选人，还要能定高度和取景 ——
    height_m: Optional[float] = None   # 说了「飞两米四」才有值；没说=None=不动板子当前高度
    camera_mode: str = "auto"          # level=云台锁平 / pitch=定高俯拍 / auto=不指定

    def to_wire(self) -> dict:
        """回传给板子的最小载荷。"""
        return {"action": self.action, "target_id": self.target_id, "form": self.form}

    def to_dict(self) -> dict:
        return asdict(self)

    def pretty(self) -> str:
        tid = "—" if self.target_id is None else f"#{self.target_id}"
        extra = ""
        if self.height_m is not None:
            extra += f" 高度={self.height_m:.1f}m"
        if self.camera_mode != "auto":
            extra += f" 取景={self.camera_mode}"
        return f"[{self.action}] 目标{tid} 形态={self.form}{extra} · {self.reason}"


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

    cam = str(raw.get("camera_mode", "auto")).strip().lower()
    if cam not in CAMERA_MODES:
        cam = "auto"

    # 高度：模型可能吐 "2.4m" / None / 胡说的 50。夹到板子的合法区间，别让它 reject。
    h = raw.get("height_m", None)
    if h is None or str(h).strip() == "":
        height = None
    else:
        try:
            height = float(str(h).strip().rstrip("mM米"))
            height = min(HEIGHT_MAX_M, max(HEIGHT_MIN_M, height))
        except (ValueError, TypeError):
            height = None

    return Intent(
        action=action,
        target_id=tid,
        form=form,
        reason=str(raw.get("reason", "")).strip(),
        confidence=float(raw.get("confidence", 1.0) or 1.0),
        needs_confirm=bool(raw.get("needs_confirm", False)) or len(cands) > 1,
        candidates=cands,
        height_m=height,
        camera_mode=cam,
    )
