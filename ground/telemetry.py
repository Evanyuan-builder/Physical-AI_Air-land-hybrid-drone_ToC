"""遥测层 —— 地面大脑「知道机器现在什么状态」。

来源（徐梓阳路线文档）：板上 UART+MAVLink(115200 接 TELEM2)，地空 Wi-Fi。
字段：飞行模式 / 姿态 / 高度 / 本地位置 / 电池 / 是否解锁。
地面大脑用它做「决策」那层：低电返航、起飞前检查、丢失后搜索等。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Telemetry:
    mode: str = "MANUAL"        # 飞行模式
    armed: bool = False
    battery_pct: int = 100
    altitude_m: float = 0.0
    airborne: bool = False       # 是否在空中（True=飞, False=地面）
    x: float = 0.0               # 本地位置
    y: float = 0.0

    @property
    def form(self) -> str:
        return "fly" if self.airborne else "walk"


class TelemetrySource:
    def latest(self) -> Telemetry:
        raise NotImplementedError


class MockTelemetry(TelemetrySource):
    """从 json 读一个固定状态，或用 set() 现场改（demo 里演低电返航）。"""

    def __init__(self, path: str | Path | None = None):
        if path and Path(path).exists():
            self._t = Telemetry(**json.loads(Path(path).read_text(encoding="utf-8")))
        else:
            self._t = Telemetry(mode="LOITER", armed=True, battery_pct=82,
                                altitude_m=0.0, airborne=False)

    def latest(self) -> Telemetry:
        return self._t

    def set(self, **kw) -> None:
        for k, v in kw.items():
            setattr(self._t, k, v)


class MavlinkTelemetry(TelemetrySource):
    """真实实现的占位：pymavlink 连板子 TELEM2，读 heartbeat/SYS_STATUS/GLOBAL_POSITION。"""

    def __init__(self, conn: str):
        self.conn = conn

    def latest(self) -> Telemetry:
        raise NotImplementedError("接板上 MAVLink 遥测")
