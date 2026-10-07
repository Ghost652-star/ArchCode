"""队员名称注册表(agent-teams-design §5.3):name → agent_id 的进程内映射。

spawn 带 name 即注册;resolve 同时接受名称或 agent_id;进程重启/恢复后从
config.json 的 members 重建。寻址靠 name 不靠 id——恢复后实例 id 可能变,
花名与邮箱不变。
"""

from __future__ import annotations

import threading


class AgentNameRegistry:
    """线程安全的 name → agent_id 映射(单 manager 一张表)。"""

    def __init__(self) -> None:
        self._names: dict[str, str] = {}
        self._lock = threading.Lock()

    def register(self, name: str, agent_id: str) -> None:
        with self._lock:
            self._names[name] = agent_id

    def unregister(self, name: str) -> None:
        with self._lock:
            self._names.pop(name, None)

    def resolve(self, to: str) -> str | None:
        """名称或 agent_id → agent_id;解析不到返回 None(调用方报错,不静默丢弃)。"""
        with self._lock:
            return self._names.get(to, to if to in self._names.values() else None)

    def rebuild(self, mappings: dict[str, str]) -> None:
        with self._lock:
            self._names = dict(mappings)
