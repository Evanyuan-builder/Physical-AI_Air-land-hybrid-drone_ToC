"""偏好记忆（Memory Core lite）—— grounding 之外的第二层 AI 价值。

跟拍机跟久了应该「记得主人、记得你爱用的机位」。
这里是最小版：一个 JSON 存主人的目标特征 + 习惯形态。真实版接 Memory Core substrate。
demo 里用来演「跟我」直接锁对人、以及「还是老样子」自动套习惯机位。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class Preferences:
    owner_caption: str = ""      # 主人长什么样（用来在新画面里重新认出）
    owner_color: str = ""
    habitual_form: str = "auto"  # 习惯形态：多数时候飞还是走
    habitual_action: str = "follow"


class PreferenceMemory:
    def __init__(self, path: str | Path = "mock_data/prefs.json"):
        self.path = Path(path)
        if self.path.exists():
            self.prefs = Preferences(**json.loads(self.path.read_text(encoding="utf-8")))
        else:
            self.prefs = Preferences()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(self.prefs), ensure_ascii=False, indent=2),
                             encoding="utf-8")

    def remember_owner(self, caption: str, color: str = "") -> None:
        self.prefs.owner_caption = caption
        self.prefs.owner_color = color
        self.save()

    def match_owner(self, frame) -> Optional[int]:
        """在当前画面里把主人重新认出来（demo 版：按颜色/caption 粗匹配）。"""
        if not self.prefs.owner_color and not self.prefs.owner_caption:
            return None
        for d in frame.detections:
            if self.prefs.owner_color and d.attributes.get("color") == self.prefs.owner_color:
                return d.target_id
            if self.prefs.owner_caption and self.prefs.owner_caption in d.label:
                return d.target_id
        return None
