"""感知层 —— 地面大脑「看见了什么」。

数据来源（徐梓阳的板子，7/26 路线文档已定死格式）：
  · 视频：RTSP  rtsp://<board>:8554/live  (720p H.264)
  · YOLO 结构化输出：存在/类别/置信/中心坐标/框宽高/target_id/时间戳 + 归一化误差 error_x/y
    —— 这份输出走哪条线回地面（HTTP 拉 / WS 推 / 共享文件）还没定，等跟徐梓阳对齐。

⚠️ YOLO 本身不给「颜色/外观」。「红衣服的那个」靠的是在 RTSP 帧里把 bbox 抠出来取主色，
   复杂描述再挂个小 VLM 生成 caption。这一步在 enrich() 里，真实实现拿到样例数据后填；
   mock 数据直接把 attributes 填好，先把上层 grounding 跑通。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class Detection:
    target_id: int
    cls: str                 # YOLO 类别：person / dog / cat ...
    conf: float
    cx: float = 0.5          # 归一化中心 x (0~1)
    cy: float = 0.5
    w: float = 0.0
    h: float = 0.0
    error_x: float = 0.0     # 归一化跟随误差（板上算，跟随闭环用）
    error_y: float = 0.0
    # —— 富化字段：YOLO 不给，由 RTSP 裁剪取色 / 小 VLM 补 ——
    attributes: dict = field(default_factory=dict)  # {"color": "红", "caption": "穿红卫衣的男生"}

    @property
    def label(self) -> str:
        c = self.attributes.get("color")
        cap = self.attributes.get("caption")
        if cap:
            return cap
        if c:
            return f"{c}色的{_cn(self.cls)}"
        return _cn(self.cls)


@dataclass
class DetectionFrame:
    ts: float
    detections: list  # list[Detection]

    @classmethod
    def from_dict(cls, d: dict) -> "DetectionFrame":
        return cls(
            ts=float(d.get("ts", 0.0)),
            detections=[Detection(**x) for x in d.get("detections", [])],
        )

    def ids(self) -> set:
        return {d.target_id for d in self.detections}

    def by_id(self, tid) -> Optional[Detection]:
        for d in self.detections:
            if d.target_id == tid:
                return d
        return None

    def describe(self) -> str:
        if not self.detections:
            return "（画面里暂时没有目标）"
        return "\n".join(
            f"  #{d.target_id}  {d.label}  ({_cn(d.cls)}, 置信 {d.conf:.0%})"
            for d in self.detections
        )


_CN = {"person": "人", "dog": "狗", "cat": "猫", "car": "车", "bicycle": "自行车"}


def _cn(cls: str) -> str:
    return _CN.get(cls, cls)


# ----------------- 数据源接口 -----------------
class YoloSource:
    """感知源抽象。真实源和 mock 源都实现 latest()。"""

    def latest(self) -> DetectionFrame:
        raise NotImplementedError

    def advance(self) -> None:
        """mock 用：推进到下一帧。真实源忽略。"""


class MockYoloSource(YoloSource):
    """从 json（单帧）或 jsonl（多帧序列）喂假数据，先把上层跑通。"""

    def __init__(self, path: str | Path):
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        if p.suffix == ".jsonl":
            self._frames = [DetectionFrame.from_dict(json.loads(line))
                            for line in text.splitlines() if line.strip()]
        else:
            self._frames = [DetectionFrame.from_dict(json.loads(text))]
        self._i = 0

    def latest(self) -> DetectionFrame:
        return self._frames[min(self._i, len(self._frames) - 1)]

    def advance(self) -> None:
        if self._i < len(self._frames) - 1:
            self._i += 1


class RtspYoloSource(YoloSource):
    """真实实现的占位。拿到徐梓阳的样例数据后填这里，上层一行都不用改。

    需要定的那一条：YOLO 结构化输出怎么从板子到地面。
      候选 A：地面 HTTP 轮询板上 /api/detections
      候选 B：板子 WS 推 / 或写共享文件，地面订阅
    视频流 rtsp://<board>:8554/live 已定，取色/VLM 富化在 enrich() 里做。
    """

    def __init__(self, rtsp_url: str, detections_endpoint: str):
        self.rtsp_url = rtsp_url
        self.detections_endpoint = detections_endpoint

    def latest(self) -> DetectionFrame:
        raise NotImplementedError("接徐梓阳的 YOLO 输出通道 —— 等样例数据")


def enrich(frame: DetectionFrame, rtsp_frame=None) -> DetectionFrame:
    """给检测框补外观（颜色/caption）。真实实现：抠 bbox → 取主色 / 小 VLM。
    mock 数据已带 attributes，这里原样返回。"""
    return frame
