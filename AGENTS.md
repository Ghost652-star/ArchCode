# ArchCode Agent Instructions

## 项目概述

ArchCode 是一个终端 AI 编程助手，基于 Textual TUI。当前已实现模块见下方状态表；设计文档在 `workstatus/`（本地，gitignored），实施文档在 `docs/`（gitignored）。

## 模块状态（2026-09-21）

| 模块 | 状态 | 设计文档 | 实施文档 |
|------|------|----------|----------|
| Agent 主循环 + 工具 + 权限 | ✅ 已实现 | — | — |
| MCP 协议接入 | ✅ 已实现 | — | — |
| 上下文压缩（Layer 1 + Layer 2） | ✅ 已实现 | — | — |
| 项目指令文档（AGENTS.md 三层） | ✅ 已实现 | `worksession-persistence-design.md` | `docs/session-persistence-*` |
| 会话持久化 | ✅ 已实现 | 同上 | 同上 |
| 命令系统（slash commands） | ✅ 已实现 | `workstatus/slash-command-design.md` | — |
| Skill 系统（单文件 + 目录型） | ✅ 已实现 | `workstatus/skills-design.md` | `docs/skills-implementation-*` |
| Hook 系统（8 事件 + 条件 DSL + 4 执行器） | ✅ 已实现 | `workstatus/hooks-design.md` | `docs/hooks-implementation-*` |
| 长期记忆（MemoryManager） | ⏳ 设计冻结 | `workstatus/long-term-memory-design.md` | — |
| 子 Agent（agents/） | ⏳ 未设计 | — | — |

## 关键命令

```bash
uv run pytest -q                          # 全量测试（261 项）
uv run archcode -w F:/TestProject          # 启动 TUI（指定工作目录）
uv run archcode -p "提问" -w F:/TestProject  # 非交互模式
```

## 配置文件层次（三层合并）

```
F:\ArchCode\.archcode\config.yaml        # 应用级（API provider + 用户级 hooks）
F:\TestProject\.archcode\config.yaml    # 项目级（hooks，providers 继承自应用级）
F:\TestProject\.archcode\config.local.yaml  # 本地覆盖（gitignored）
```

## GitHub 操作

- 使用 `gh` CLI + HTTPS remote + 系统 credential helper；不主动读写 token / SSH key。
- 不主动改 remote 为 SSH；不改全局 Git 配置。
- 提交格式：`feat(hooks): ...` / `fix(agent): ...` / `docs: ...`

## 源码规范

- 测试放 `tests/`（gitignored，不入版本控制）。
- 设计文档放 `workstatus/`（gitignored，本地工作笔记）。
- 实施文档（plan / tasks / review）放 `docs/`（gitignored）。
- `workstatus/hooks-design.md` 的 §9 延后清单、`workstatus/deferred-designs.md` #3/#4 为将来扩展预留。

