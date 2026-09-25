# ArchCode

ArchCode 是一个终端 AI 编程助手，基于 Textual 构建 TUI 界面。支持流式对话、对话历史管理、5 层权限系统、HITL 权限弹窗、可插拔工具、计划模式（Plan Mode）、上下文自动压缩、MCP 协议接入任意外部工具 server、项目指令文档（AGENTS.md）、Skill 系统（单文件 + 目录型 + 专属工具 + allowedTools），以及 Hook 系统（事件 + 条件 + 动作的生命周期钩子）。

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
| `/skill list` | 列出已加载的 Skill（三层来源 + 描述） |
| `/skill info <name>` | 显示指定 Skill 的 frontmatter 与文件路径 |
| `/skill reload` | 重新扫描 Skill 目录（下一 Task 生效） |
| `/skill-name [args]` | 激活指定 Skill（`$ARGUMENTS` 替换后钉入对话） |

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

### Skill 系统

通过 SKILL.md 向 Agent 注入领域知识与操作流程。放在项目级 `<work_dir>/.archcode/skills/` 或用户级 `<archcode-root>/.archcode/skills/`，启动时扫描建目录、按需激活。

```text
skills/
├── review.md              # 单文件型:frontmatter(name/description) + 正文(SOP 模板)
└── deploy/                # 目录型:可附带 tool.json 声明专属工具
    ├── SKILL.md
    └── tools.json
```

- **激活方式**：模型调 `LoadSkill` 工具，或用户 `/skill-name args` 命令
- **激活后**：正文渲染 `$ARGUMENTS` 后钉入对话顶部 `<active-skills>` 消息；目录型的专属工具注册到 ToolRegistry（所有权归属该 Skill）
- **allowedTools**（可选 frontmatter）：激活期间只允许声明内的工具（模型看不见 + 硬调进不去）
- **生命周期**：`/clear` 清激活集合 + 注销专属工具；`/session resume` 激活集合从零开始
- 三层优先级（project > user > builtin），同名遮蔽 + 诊断

### Hook 系统

在 Agent 生命周期的关键节点上声明式配置自动化动作。放在 `<work_dir>/.archcode/config.yaml` 的 `hooks:` 键（或用户级 config.yaml，追加合并）。

```yaml
hooks:
  # 写 .py 后自动格式化
  - id: auto-format
    event: post_tool_use
    if: 'tool == "WriteFile" && args.path ~= "*.py"'
    action:
      type: command
      command: "black $FILE_PATH"

  # 禁止修改 vendor 目录
  - id: block-vendor
    event: pre_tool_use
    if: 'tool == "WriteFile" && args.path ~= "vendor/**"'
    action:
      type: command
      command: "echo 'vendor 目录由包管理工具管理，请勿手动修改'"
    reject: true
```

- **8 个事件**：session_start / turn_start / pre_tool_use / permission_request / post_tool_use / post_tool_use_failure / turn_end / session_end
- **条件**（`if`，可选）：`==` `!=` 精确/反向，`=~` 正则，`~=` glob（同 .gitignore）；`&&` / `||` 组合（不可混用）
- **四种执行器**：command（shell 命令）、prompt（注入提示词）、http（发请求）、agent（子 Agent，预留）
- **占位符**：`$EVENT` `$TOOL_NAME` `$FILE_PATH` `$MESSAGE` `$ERROR` `$TOOL_ARGS.<key>`
- **执行控制**：`reject: true`（拦截工具调用）、`once: true`（仅首次触发）、`async: true`（后台执行不等待）
- **三层追加合并**：应用级 + 项目级 + local 的 hooks 叠加生效

### 子 Agent(SubAgent)

主 Agent 通过统一的 `Agent` 工具把子任务派发给专门的子 Agent(独立上下文 + 收窄工具集),像调用普通工具一样自然。两种派发模式,按任务性质选:

```
任务类型?
├─ 固定角色、可限制工具 → 指定 subagent_type → 定义式(独立上下文、可选模型)
│   项目级 .archcode/agents/*.md > 用户级 > 内置(Explore / Plan / general-purpose)
└─ 临时任务、需要对话上下文 → subagent_type 留空 → Fork(继承父对话、命中缓存)
```

- **定义式**:一个 agent 类型 = 一个 Markdown 定义文件(YAML frontmatter + 正文系统提示)。`tools` / `disallowedTools` 白黑名单裁剪能力;`permissionMode` 定权限基调;`maxTurns` 限轮次。在项目里新建定义文件,下次调用即可用。
- **Fork**:继承父 Agent 完整对话,拿到任务从头跑到尾,**始终后台运行**,结果经 `<task-notification>` 异步回传,主 Agent 不阻塞。
- **四道防线**工具过滤:全局禁止(不能 spawn / 不能问用户 / 不能调度)+ 自定义收紧 + 后台白名单 + 定义黑白名单。
- 定义式子 agent 默认前台同步执行(`run_in_background: true` 或定义 `background: true` 可转后台);用 `TaskList` / `TaskGet` 查后台任务。

### 上下文压缩

长对话接近模型窗口上限时自动压缩，`archcode/context/` 分两层：

- **Layer 1（工具结果预算，`manager.py`）**：每轮 agent loop 前扫描所有 `tool_result`。单条超限落盘只留 preview、单消息聚合超限按长度倒序裁、超过保留轮次的旧结果剪成 `<snipped>` 片段。每个 `tool_use_id` 只评估一次（决策冻结），保证 prompt cache 前缀字节级稳定。
- **Layer 2（LLM 摘要，`compactor.py`）**：token 数达到阈值（`context_window − 20K 摘要预留 − 13K 余量`）时，用独立摘要 prompt 把旧历史压成 9 段结构化摘要并原子替换 history。带熔断器（连续失败自动停）、drop-oldest 1/5 重试、摘要质量校验。
- **恢复附件（`recovery.py`）**：线程安全记录本会话读过的文件 / 激活的 skills，压缩后拼成 Markdown 附件挂在摘要后，提示模型需要原文时用工具按需加载。

`/compact` 可随时手动触发压缩（跳过阈值检查）。压缩进度、token 用量在 TUI 状态栏实时显示。摘要也可走独立 provider（默认 MiniMax，见配置章节）。

## 配置

配置文件按优先级合并，后者覆盖前者：

1. `<archcode-root>/.archcode/config.yaml`
2. `<work_dir>/.archcode/config.yaml`
3. `<work_dir>/.archcode/config.local.yaml`

`<archcode-root>` 是 ArchCode 自身的安装或源码根目录；`<work_dir>` 是通过 `-w` 指定的当前工作项目目录。真实配置包含密钥，均由 Git 忽略；仓库只提供 `.archcode/config.yaml.example` 作为可复制模板。

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

`-w` 参数指定工作目录，工具读写的相对路径以它为基准。运行时数据区分为应用级与项目级；它们都不会提交到 Git。

```text
<archcode-root>/.archcode/        # ArchCode 自己的应用级数据
├─ config.yaml                    # 本机 API / Provider / hooks 配置
├─ AGENTS.md                      # 用户级指令文档（已实现，分支 codex/project-instructions）
├─ skills/                        # 用户级 Skill（跨项目通用）
└─ memory/                        # 用户级长期记忆

<work_dir>/.archcode/             # 当前工作项目的数据
├─ config.yaml                    # 项目级配置（可含 hooks）
├─ config.local.yaml              # 本地覆盖（不进 Git）
├─ AGENTS.md                      # 项目私有指令文档
├─ skills/                        # 项目级 Skill
├─ sessions/                      # 当前项目的会话 JSONL
├─ session/tool-results/          # 上下文压缩时的临时工具结果
├─ plans/                         # 当前项目的计划文件
├─ debug.log                      # 日志（追加模式）
└─ memory/                        # 项目级长期记忆
```

其中，`sessions/` 用于恢复某个具体对话，`session/tool-results/` 是可在压缩后清理的临时文件，`plans/` 保存项目工作计划，两个 `memory/` 分别保存用户级与项目级长期知识。实际运行时目录会随着工作项目和本机配置产生，不属于源码，也不应提交。

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
