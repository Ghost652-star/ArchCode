"""AgentLoader:三层来源扫描 + seen 遮蔽诊断 + 热重载 + catalog(sub-agent-design §4.2/§2.5)。

三层优先级从高到低(§4.2):
  1. 项目级  <work_dir>/.archcode/agents/*.md   随 Git 共享
  2. 用户级  <源码根>/.archcode/agents/*.md     个人跨项目(测试可注入临时目录)
  3. 内置级  archcode/agents/builtins/*.md      包内资源,随包发布

同名先到先得,被遮蔽产生诊断(不静默);解析失败跳过 + 诊断,不阻断启动。
get() 热重载:文件仍在则重读,解析失败沿用上次成功版本;文件被删仍返回内存
中的定义(不让已知类型消失,与 skills get() 同款)。
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from archcode.agents.models import AgentDef
from archcode.agents.parser import AgentParseError, parse_agent_file, parse_agent_text
from archcode.paths import application_agents_dir, project_agents_dir


class AgentLoader:
    """agent 定义的加载与查询;`user_dir` 仅供测试注入,生产用默认用户级目录。"""

    def __init__(self, work_dir: str | Path, user_dir: Path | None = None) -> None:
        self._work_dir = work_dir
        self._user_dir = user_dir
        self._agents: dict[str, AgentDef] = {}
        self.diagnostics: list[str] = []

    def _layer_dirs(self) -> list[tuple[str, Path]]:
        layers: list[tuple[str, Path]] = [("project", project_agents_dir(self._work_dir))]
        layers.append(("user", self._user_dir or application_agents_dir()))
        return layers

    def _scan_directory(self, directory: Path, source: str) -> list[AgentDef]:
        results: list[AgentDef] = []
        if not directory.is_dir():
            return results
        for path in sorted(directory.glob("*.md")):
            try:
                defn = parse_agent_file(path)
                defn.source = source
                results.append(defn)
            except AgentParseError as exc:
                self.diagnostics.append(f"[agents] 解析失败,已跳过 {path.name}: {exc}")
        return results

    def _load_builtins(self) -> list[AgentDef]:
        results: list[AgentDef] = []
        try:
            pkg = importlib.resources.files("archcode.agents.builtins")
        except (ModuleNotFoundError, TypeError):
            self.diagnostics.append("[agents] 内置定义包不可用,已跳过")
            return results
        for item in sorted(pkg.iterdir(), key=lambda r: r.name):
            if not item.name.endswith(".md"):
                continue
            try:
                defn = parse_agent_text(item.read_text(encoding="utf-8"))
                defn.source = "builtin"
                results.append(defn)
            except (AgentParseError, OSError) as exc:
                self.diagnostics.append(
                    f"[agents] 内置定义解析失败,已跳过 {item.name}: {exc}"
                )
        return results

    def load_all(self) -> dict[str, AgentDef]:
        """扫描三层并建目录;同名先到先得(高优先级遮蔽低),遮蔽产生诊断。"""
        self.diagnostics = []
        seen: dict[str, AgentDef] = {}
        for source, directory in self._layer_dirs():
            for defn in self._scan_directory(directory, source):
                if defn.agent_type in seen:
                    self.diagnostics.append(
                        f"[agents] '{defn.agent_type}' 的 {source} 版本被更高优先级版本遮蔽,未加载"
                    )
                    continue
                seen[defn.agent_type] = defn
        for defn in self._load_builtins():
            if defn.agent_type in seen:
                self.diagnostics.append(
                    f"[agents] '{defn.agent_type}' 的内置版本被更高优先级版本遮蔽,未加载"
                )
                continue
            seen[defn.agent_type] = defn
        self._agents = seen
        return seen

    def get(self, agent_type: str) -> AgentDef | None:
        """取单个定义;文件仍在则热重载,失败沿用上次成功版本(不消失)。"""
        cached = self._agents.get(agent_type)
        if cached is None:
            return None
        if cached.file_path is not None and cached.file_path.exists():
            try:
                reloaded = parse_agent_file(cached.file_path)
                reloaded.source = cached.source
                self._agents[agent_type] = reloaded
                return reloaded
            except AgentParseError as exc:
                self.diagnostics.append(
                    f"[agents] 热重载失败,沿用上次成功版本 {agent_type}: {exc}"
                )
        return cached

    def manifests(self) -> dict[str, AgentDef]:
        """返回当前全部定义(浅拷贝视图)。"""
        return dict(self._agents)

    def list_agents(self) -> list[tuple[str, str]]:
        """(agent_type, when_to_use) 列表,供 catalog 与错误提示使用。"""
        return [(d.agent_type, d.when_to_use) for d in self._agents.values()]

    def get_catalog_text(self) -> str:
        """<agent-catalog> 的内容文本(§2.5):每行一个类型的 name + when_to_use。"""
        return "\n".join(
            f"- **{d.agent_type}**: {d.when_to_use}" for d in self._agents.values()
        )
