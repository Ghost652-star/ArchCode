"""团队共享任务列表(agent-teams-design §3.2/§6.2)。

tasks.json 存团队全部任务;依赖(blocks/blocked_by)建结构化 DAG;
"可认领"即时判定 = pending ∧ 认领人为空 ∧ 所有 blocked_by 已 completed——
依赖完成即自动解锁,不需要额外的解锁动作。
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path

from archcode.teams.models import TeamTask

logger = logging.getLogger(__name__)


class SharedTaskStore:
    """一个团队的共享任务存储(tasks.json;写走文件锁同款协议——认领防竞争)。"""

    def __init__(self, team_dir: str | Path) -> None:
        self._path = Path(team_dir) / "tasks.json"

    # ------------------------------------------------------------------

    def _load(self) -> list[TeamTask]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("tasks.json 读取失败,按空表处理: %s", e)
            return []
        return [
            TeamTask(
                id=str(t.get("id", "")),
                subject=str(t.get("subject", "")),
                description=str(t.get("description", "")),
                status=t.get("status", "pending"),
                owner=str(t.get("owner", "")),
                blocks=list(t.get("blocks", [])),
                blocked_by=list(t.get("blocked_by", [])),
                created_at=str(t.get("created_at", "")),
            )
            for t in (raw if isinstance(raw, list) else [])
        ]

    def _save(self, tasks: list[TeamTask]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            [
                {
                    "id": t.id,
                    "subject": t.subject,
                    "description": t.description,
                    "status": t.status,
                    "owner": t.owner,
                    "blocks": t.blocks,
                    "blocked_by": t.blocked_by,
                    "created_at": t.created_at,
                }
                for t in tasks
            ],
            ensure_ascii=False,
            indent=2,
        )
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self._path)

    # ------------------------------------------------------------------
    # CRUD 与依赖
    # ------------------------------------------------------------------

    def create(self, subject: str, description: str = "") -> TeamTask:
        tasks = self._load()
        task = TeamTask(id=uuid.uuid4().hex[:6], subject=subject, description=description)
        tasks.append(task)
        self._save(tasks)
        return task

    def get(self, task_id: str) -> TeamTask | None:
        return next((t for t in self._load() if t.id == task_id), None)

    def list(self) -> list[TeamTask]:
        return self._load()

    def update(
        self,
        task_id: str,
        *,
        status: str | None = None,
        owner: str | None = None,
        add_blocks: list[str] | None = None,
        add_blocked_by: list[str] | None = None,
    ) -> TeamTask | None:
        """更新任务;add_blocks/add_blocked_by 建立双向依赖(改本任务时同步对端)。"""
        tasks = self._load()
        task = next((t for t in tasks if t.id == task_id), None)
        if task is None:
            return None
        if status is not None:
            task.status = status
        if owner is not None:
            task.owner = owner
        by_ids = {t.id for t in tasks}
        for dep in add_blocked_by or []:
            if dep in by_ids and dep not in task.blocked_by and dep != task.id:
                task.blocked_by.append(dep)
                other = next(t for t in tasks if t.id == dep)
                if task.id not in other.blocks:
                    other.blocks.append(task.id)
        for dep in add_blocks or []:
            if dep in by_ids and dep not in task.blocks and dep != task.id:
                task.blocks.append(dep)
                other = next(t for t in tasks if t.id == dep)
                if task.id not in other.blocked_by:
                    other.blocked_by.append(task.id)
        self._save(tasks)
        return task

    def claimable(self, task: TeamTask) -> bool:
        """可认领 = pending ∧ 未认领 ∧ 全部依赖已完成(依赖完成即自动解锁)。"""
        if task.status != "pending" or task.owner:
            return False
        tasks = {t.id: t for t in self._load()}
        return all(
            tasks.get(dep) is not None and tasks[dep].status == "completed"
            for dep in task.blocked_by
        )
