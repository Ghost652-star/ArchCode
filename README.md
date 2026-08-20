# ArchCode

ArchCode 是一个终端 AI 编程助手，基于 Textual 构建 TUI 界面。支持流式对话、对话历史管理、5 层权限系统、HITL 权限弹窗、可插拔工具、计划模式（Plan Mode）、上下文自动压缩，以及通过 MCP 协议接入任意外部工具 server。

## 快速开始

```bash
# 安装依赖
uv sync

# 配置 API Key
cp .archcode/config.yaml.example .archcode/config.yaml
# 编辑 config.yaml，填入 base_url / model / api_key
# 或设置环境变量 OPENAI_API_KEY / ANTHROPIC_API_KEY

# 启动 TUI 交互界面
uv run archcode

# 指定工作目录（沙箱以该目录为根，plan 文件落盘于此）
uv run archcode -w F:/myproject

# 单次提问（纯文本输出，无 TUI）
uv run archcode -p "用 Python 写一个快速排序"
```

## 交互界面

### 快捷键

| 按键 | 功能 |
|------|------|
| `Enter` | 发送消息 |
| `Shift+Enter` / `Ctrl+J` | 输入框内换行 |
| `Ctrl+L` | 清空当前对话 |
| `Ctrl+C` | 退出 |

### 输入框命令

| 命令 | 功能 |
|------|------|
| `/clear` | 清空当前对话 |
| `/compact` | 手动触发上下文压缩（不受自动阈值限制） |
| `/quit` / `/exit` | 退出 ArchCode |
| `/plan` | 进入 Plan 模式（只读工具 + 写计划文件） |
| `/exit-plan` | 退出 Plan 模式 |
| `/mode <default\|accept\|bypass>` | 切换权限模式（`default` 写操作需确认，`bypass` 全部放行） |

### 权限/提问弹窗（HITL）

工具调用需要用户确认时，弹出内嵌选项：

| 按键 | 功能 |
|------|------|
| `↑` / `↓` | 在选项间移动 |
| `1` - `9` | 直接选第 N 项（数字热键） |
| `Enter` | 确认当前选项 |
| `Esc` | 拒绝 / 取消 |
| `Space` | 多选模式切换勾选（仅 AskUserQuestion） |

弹窗的两种场景：
- **权限询问**：Yes / No 二选一
- **AskUserQuestion**：LLM 提问的多项选择题，支持多选

## 架构

ArchCode 采用严格分层设计：

```
Presentation    app.py / driver.py / styles.tcss       # Textual TUI，渲染 AgentEvent
       │
Orchestration  agent.py                              # 用户消息 → LLM 流 → AgentEvent → 工具执行
       │
Communication  llm/client.py                         # LLMClient + 各厂商协议实现
                llm/events.py                        # 协议无关的 StreamEvent
       │
Data           conversation/models.py                 # Message, ToolUseBlock, ToolResultBlock
                conversation/manager.py              # ConversationManager（历史 + token anchor）
                context/                             # 上下文压缩（工具结果预算 + LLM 摘要 + 恢复附件）
       │
Config         config.py / prompts/                  # YAML 配置 + 系统提示词
                mcp/                                 # MCP 协议适配（stdio + HTTP）
                tools/                               # 本地工具 + 工具注册中心
                permissions/                         # 5 层权限校验
```

### 权限系统（5 层）

```
Layer 0: AskUserQuestion 永远 HITL
Layer 1: Plan mode 专用路径
Layer 2: 安全命令放行 / 危险命令拒绝
Layer 3: 路径沙箱（文件工具必须在 work_dir 内）
Layer 4: 模式矩阵（default / accept / bypass）
Layer 5: HITL 弹窗（上面都没决定的）
```

`/mode bypass` 直接放行所有命令；`/mode default` 是默认安全模式。

### MCP 工具接入

ArchCode 通过 MCP（Model Context Protocol）接入任意外部工具 server：

- 支持 stdio（本地子进程）和 streamable HTTP（远程）两种传输
- 工具默认延迟加载（LLM 通过 `ToolSearch` 按需加载 schema）
- 自动重连（单次尝试）
- 部分 server 失败不影响其他

配置示例见下方「配置」章节。

### 上下文压缩

长对话接近模型窗口上限时自动压缩，`archcode/context/` 分两层：

- **Layer 1（工具结果预算，`manager.py`）**：每轮 agent loop 前扫描所有 `tool_result`。单条超限落盘只留 preview、单消息聚合超限按长度倒序裁、超过保留轮次的旧结果剪成 `<snipped>` 片段。每个 `tool_use_id` 只评估一次（决策冻结），保证 prompt cache 前缀字节级稳定。
- **Layer 2（LLM 摘要，`compactor.py`）**：token 数达到阈值（`context_window − 20K 摘要预留 − 13K 余量`）时，用独立摘要 prompt 把旧历史压成 9 段结构化摘要并原子替换 history。带熔断器（连续失败自动停）、drop-oldest 1/5 重试、摘要质量校验。
- **恢复附件（`recovery.py`）**：线程安全记录本会话读过的文件 / 激活的 skills，压缩后拼成 Markdown 附件挂在摘要后，提示模型需要原文时用工具按需加载。

`/compact` 可随时手动触发压缩（跳过阈值检查）。压缩进度、token 用量在 TUI 状态栏实时显示。摘要也可走独立 provider（默认 MiniMax，见配置章节）。

## 配置

配置文件按优先级合并，后者覆盖前者：

1. `~/.archcode/config.yaml`
2. `.archcode/config.yaml`
3. `.archcode/config.local.yaml`

### LLM Providers

支持的协议：

| 协议 | 说明 |
|------|------|
| `openai-compat` | Chat Completions，适用于 OpenAI 兼容中转、vLLM、Ollama、DeepSeek 等 |
| `openai` | OpenAI Responses API |
| `anthropic` | Anthropic Messages API（支持 thinking） |

配置示例：

```yaml
providers:
  - name: deepseek
    protocol: openai-compat
    base_url: https://api.deepseek.com
    model: deepseek-v4-flash
    api_key: sk-...
    max_output_tokens: 16384
```

`api_key` 支持 `${VAR}` 模板，从环境变量读取（不进 git）。

### MCP Servers

任意遵循 MCP 协议的 server 都可接入。两种 transport 二选一：

```yaml
mcp_servers:
  # ── stdio: 本地子进程 ─────────────────────────────

  # GitHub（npx 拉取官方 server）
  - name: github
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_TOKEN: "${GITHUB_TOKEN}"

  # 本地文件系统（限制在指定目录）
  - name: filesystem
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/project"]

  # 自己写的 Python server
  - name: my_server
    command: python
    args: ["-m", "my_mcp_server"]

  # uvx 运行的 PyPI 包
  - name: doc_search
    command: uvx
    args: ["some-mcp-server"]

  # ── streamable_http: 远程服务 ─────────────────────

  # 自托管 MCP 网关
  - name: corp_gateway
    url: https://mcp.corp.example.com
    headers:
      Authorization: "Bearer ${MCP_TOKEN}"

  # SaaS 平台的 MCP endpoint
  - name: notion
    url: https://api.notion.com/mcp
    headers:
      Authorization: "Bearer ${NOTION_KEY}"
```

**任意 server 都遵循同样流程**：
1. 启动时连接（某个失败不影响其他）
2. 调用 `tools/list` 拿工具清单
3. 包成 `MCPToolWrapper`（`should_defer=True`）
4. LLM 通过 `ToolSearch` 按需加载 schema

发现新 server：搜索 `mcp-server-*`（npm / PyPI）、看 [MCP 官方 server 列表](https://github.com/modelcontextprotocol/servers)。

### 上下文压缩配置

所有阈值都有默认值，可按需覆盖：

```yaml
compression:
  enabled: true            # 总开关
  single_char_limit: 50000 # Layer 1: 单条 tool_result 落盘阈值（字符）
  aggregate_char_limit: 200000  # Layer 1: 单消息内聚合阈值
  preview_chars: 2000      # 落盘后 preview 长度
  keep_recent_turns: 10    # Layer 2: 保留最近 N 轮原文
  max_summary_failures: 3  # auto_compact 熔断阈值
  # 摘要可走独立 provider（默认关闭，走主对话 client）
  summary_provider:
    enabled: false
    protocol: openai-compat
    base_url: https://api.MiniMax.io/v1
    model: MiniMax-M3
    api_key_env: MINIMAX_API_KEY
```

## 工作目录与沙箱

`-w` 参数指定工作目录，工具读写的相对路径以它为基准，plan 文件落到 `<work_dir>/.archcode/plans/`。

```bash
# 沙箱以 F:/myproject 为根，AI 只能读写其内文件（+临时目录）
uv run archcode -w F:/myproject
```

不指定 `-w` 时，默认用当前目录（`os.getcwd()`）。

## 测试

```bash
uv run pytest                           # 全部测试
uv run pytest tests/test_mcp_*.py       # MCP 相关
uv run pytest -k conversation           # 按名字过滤
```

## 环境要求

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/) 包管理器
- （可选）Node.js + npx，用于 stdio 类型的 MCP server

## 详细目录结构与开发状态

见 [`workstatus/`](./workstatus) 目录。