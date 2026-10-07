"""队员对话持久化(agent-teams-design §8.2):空闲/中止后可从磁盘恢复续写。

队员 conversation 全量落盘(按 agent_id 一份);恢复 = 载入记录重建 Agent 实例,
与 session 恢复(resume)同一套语义。粒度定稿为全量(压缩留作后续优化)。
"""

from __future__ import annotations

import json
from pathlib import Path


def transcript_dir(team_dir: str | Path, agent_id: str) -> Path:
    return Path(team_dir) / "transcripts" / agent_id


def save_transcript(team_dir: str | Path, agent_id: str, messages: list[dict]) -> None:
    d = transcript_dir(team_dir, agent_id)
    d.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(messages, ensure_ascii=False, indent=2)
    tmp = d / "messages.json.tmp"
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(d / "messages.json")


def load_transcript(team_dir: str | Path, agent_id: str) -> list[dict] | None:
    p = transcript_dir(team_dir, agent_id) / "messages.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, list) and data else None
