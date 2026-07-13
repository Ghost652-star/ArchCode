# ArchCode

ArchCode 是一个终端 AI 编程助手，基于 Textual 构建 TUI 界面。支持流式对话、对话历史管理，以及可扩展的工具、权限、MCP 等模块。

## 快速开始

```bash
# 安装依赖
uv sync

# 配置 API Key
cp .archcode/config.yaml.example .archcode/config.yaml
# 编辑 config.yaml，填入 base_url / model / api_key
# 或设置环境变量 OPENAI_API_KEY

# 启动 TUI 交互界面
uv run archcode

# 单次提问（纯文本输出，无 TUI）
uv run archcode -p "用 Python 写一个快速排序"
```

交互界面快捷键：

| 按键 | 功能 |
|------|------|
| `Enter` | 发送消息 |
| `Shift+Enter` | 换行 |
| `Ctrl+L` | 清空对话 |
| `Ctrl+C` | 退出 |

输入框支持命令：

- `/clear` — 清空当前对话
- `/quit` 或 `/exit` — 退出

## 架构

ArchCode 采用分层设计：`conversation/` 管理协议无关的对话历史，`llm/` 封装各厂商 API 差异并产出统一的流式事件，`agent.py` 编排用户输入、模型调用与事件转发，`app.py` 负责终端渲染。工具、权限、记忆等能力以可插拔模块形式逐步加入。

详细的目录结构、开发状态与待办见 [`workstatus/`](./workstatus) 目录。

## 开发路线

| 版本 | 模块 | 功能 |
|------|------|------|
| v0.1 | 核心 + TUI | 对话、流式输出、Textual 界面 |
| v0.2 | `tools/` | 文件读写、命令执行、代码搜索 |
| v0.3 | `permissions/` | 权限模式、路径沙箱、规则引擎 |
| v0.4 | `commands/` | `/help`、`/compact` 等斜杠命令 |
| v0.5 | `context/` | 长对话自动压缩 |
| v0.6 | `memory/` | 会话持久化与记忆召回 |
| v0.7 | `mcp/` | MCP 工具接入 |
| v0.8 | `agents/` | 子代理与后台任务 |
| v0.9 | `hooks/` | 事件驱动的自动化 |
| v1.0 | `skills/`、`teams/`、`worktree/` | 技能系统、多代理协作、工作区隔离 |

## 配置

配置文件按优先级合并，后者覆盖前者：

1. `~/.archcode/config.yaml`
2. `.archcode/config.yaml`
3. `.archcode/config.local.yaml`

支持的 LLM 协议：

| 协议 | 说明 |
|------|------|
| `openai-compat` | Chat Completions，适用于 OpenAI 兼容中转、vLLM、Ollama 等 |
| `openai` | OpenAI Responses API |
| `anthropic` | Anthropic Messages API（支持 thinking） |

上层只通过统一的 `LLMClient.stream()` 消费 `StreamEvent`，协议差异在适配层内部处理。

配置示例：

```yaml
providers:
  - name: default
    protocol: openai-compat
    base_url: https://api.openai.com/v1
    model: gpt-4o-mini
    api_key: ${OPENAI_API_KEY}
    max_output_tokens: 4096
```

## 测试

```bash
uv run pytest
```

## 环境要求

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/) 包管理器
