"""目录型 Skill 的专属工具注册(skills-design.md 0.5,实现于 2026-09-19)。

- tool.json(数组或单对象,标准 function calling schema)声明工具接口;
- references/{tool_name}.py 提供模块级 execute() 实现;
- params_model 由 schema 经 MCP 的 _build_params_model 生成——模型传参先过真校验;
- **延迟导入**:注册时不执行任何 Skill 代码,首次调用且权限放行后才 import
  (任意代码执行被权限系统罩住,与 Bash 同等待遇——见 0.5 修正记录);
- 所有权:owner="skill:<name>",卸载/清空/reload 时 unregister_owner 整体注销。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from archcode.tools.base import Tool, ToolResult
from archcode.tools.registry import ToolRegistry


def parse_tool_json(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    """解析 tool.json(数组或单对象)。返回 (schemas, error)。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], f"tool.json 解析失败 {path}: {exc}"
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return [], f"tool.json 必须是数组或对象: {path}"
    return raw, None


def _load_tool_implementation(references_dir: Path, tool_name: str):
    """延迟导入 references/{tool_name}.py 的模块级 execute()。

    只在首次调用且权限放行后被触发。找不到实现或导入失败 → None(调用方报错)。
    """
    script = references_dir / f"{tool_name}.py"
    if not script.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            f"archcode_skill_tool_{tool_name}", script
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # 规范做法:exec 前注册,dataclass 等特性依赖它
        spec.loader.exec_module(module)
        return getattr(module, "execute", None)
    except Exception:
        return None


class SkillCustomTool(Tool):
    """目录型 Skill 的专属工具。

    - is_skill_tool=True:权限走专用路径——default/accept/bypass 自动放行
      (激活该 Skill 即已同意),plan 模式 ask(保持只读契约)。见 0.5 修正记录。
    - 实现延迟导入:注册时零代码执行。
    - params_model 由 tool.json 的 JSON Schema 生成(复用 MCP 转换器,真校验)。
    """

    is_skill_tool = True

    def __init__(
        self,
        tool_name: str,
        description: str,
        schema: dict[str, Any],
        references_dir: Path,
    ) -> None:
        self.name = tool_name
        self.description = description
        self._schema = schema
        self._references_dir = references_dir
        self._impl = None
        self._import_attempted = False
        from archcode.mcp.tool_wrapper import _build_params_model

        self.params_model = _build_params_model(
            tool_name, schema.get("parameters", {})
        )

    def get_schema(self) -> dict[str, Any]:
        input_schema = self._schema.get("parameters") or {
            "type": "object",
            "properties": {},
        }
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": input_schema,
        }

    def _ensure_impl(self):
        """首次调用时才导入实现(import 在权限放行之后)。结果缓存。"""
        if self._import_attempted:
            return self._impl
        self._import_attempted = True
        self._impl = _load_tool_implementation(self._references_dir, self.name)
        return self._impl

    async def execute(self, params: BaseModel) -> ToolResult:
        impl = self._ensure_impl()
        if impl is None:
            return ToolResult(
                output=(
                    f"Error: 工具 '{self.name}' 没有可用的实现"
                    f"(references/{self.name}.py 缺失或导入失败)"
                ),
                is_error=True,
            )
        try:
            kwargs = params.model_dump()
            if asyncio.iscoroutinefunction(impl):
                result = await impl(**kwargs)
            else:
                result = await asyncio.to_thread(impl, **kwargs)
            return ToolResult(output=str(result))
        except Exception as exc:
            return ToolResult(output=f"Tool execution error: {exc}", is_error=True)


def register_skill_tools(
    skill_dir: Path, registry: ToolRegistry, owner: str
) -> tuple[list[str], list[str]]:
    """注册目录型 Skill 的专属工具。返回 (已注册名列表, 诊断列表)。

    - 重名:同 owner 幂等跳过(重复激活);异 owner → 拒绝 + 诊断(绝不覆盖);
    - schema 无法转换 → 拒绝该工具 + 诊断,不影响其他工具;
    - 无实现的工具照常注册,首次调用时报错(fail on use,见延迟导入)。
    """
    registered: list[str] = []
    diagnostics: list[str] = []
    tool_json = skill_dir / "tool.json"
    if not tool_json.is_file():
        return registered, diagnostics

    schemas, error = parse_tool_json(tool_json)
    if error:
        diagnostics.append(f"[skills] {error}")
        return registered, diagnostics

    references_dir = skill_dir / "references"
    for schema in schemas:
        tool_name = str(schema.get("name", "")).strip()
        if not tool_name:
            diagnostics.append(
                f"[skills] tool.json 中有缺 name 的工具声明,已跳过({skill_dir})"
            )
            continue
        existing = registry.get(tool_name)
        if existing is not None:
            if registry.owner_of(tool_name) == owner:
                continue  # 幂等:同 owner 重复激活
            diagnostics.append(
                f"[skills] 工具 '{tool_name}' 与现有工具冲突,已拒绝注册(来源: {skill_dir})"
            )
            continue
        description = str(schema.get("description", ""))
        try:
            tool = SkillCustomTool(tool_name, description, schema, references_dir)
        except Exception as exc:
            diagnostics.append(
                f"[skills] 工具 '{tool_name}' 的参数 schema 无法转换,已拒绝: {exc}"
            )
            continue
        registry.register(tool, owner=owner)
        registered.append(tool_name)
    return registered, diagnostics
