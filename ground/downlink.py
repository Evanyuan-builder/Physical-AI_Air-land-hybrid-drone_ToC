"""回传通道 —— 地面大脑把决定好的 Intent 送回板子。

⚠️ 这是我跟徐梓阳「唯一要一起定」的那条线，别的都不碰他。
两个候选实现都留好了占位，他定哪个我就用哪个，上层 agent 一行不用改：
  A. HttpIntentDownlink —— 板上 MaixCAM2 的 HTTP 服务加一个语义端点 POST /api/intent
     （解耦干净，不碰他的板上飞控闭环，我倾向这个）
  B. MavlinkDownlink   —— 地面走 MAVLink offboard 发高层 setpoint

现在默认 MockDownlink：只打印/记录，不连硬件，demo 当天就能跑。
"""
from __future__ import annotations

import json
from intents import Intent


class Downlink:
    def send(self, intent: Intent) -> dict:
        raise NotImplementedError


class MockDownlink(Downlink):
    """不连硬件，把回传载荷打印出来（demo 大屏上能看见「下发给板子」这一下）。"""

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.log: list[dict] = []

    def send(self, intent: Intent) -> dict:
        wire = intent.to_wire()
        self.log.append(wire)
        if self.verbose:
            print(f"  ↳ 下发板子：{json.dumps(wire, ensure_ascii=False)}")
        return {"ok": True, "wire": wire}


class HttpIntentDownlink(Downlink):
    """候选 A：POST 到板上语义端点。等徐梓阳确认端点后启用。"""

    def __init__(self, endpoint: str = "http://192.168.4.1/api/intent"):
        self.endpoint = endpoint

    def send(self, intent: Intent) -> dict:
        import urllib.request
        data = json.dumps(intent.to_wire()).encode("utf-8")
        req = urllib.request.Request(self.endpoint, data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))


class MavlinkDownlink(Downlink):
    """候选 B：MAVLink offboard 发高层 setpoint。占位。"""

    def __init__(self, conn: str):
        self.conn = conn

    def send(self, intent: Intent) -> dict:
        raise NotImplementedError("接 MAVLink offboard —— 跟徐梓阳定通道后填")
