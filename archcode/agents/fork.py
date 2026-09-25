"""Fork 路径的对话构建与防递归兜底(sub-agent-design §5.4/5.5/5.6)。

buildForkedMessages 三件事(§5.4):
  1. 深拷贝父对话历史 → 子 agent 拿到父的完整上下文;
  2. 末尾"未完成的 tool_use"补 placeholder tool_result(消息序列合法性——
     LLM API 要求每个 tool_use 必须紧跟对应 tool_result);
  3. 末尾追加 FORK_BOILERPLATE(硬约束)+ 任务文本。

防递归兜底(§5.5):开头扫整段父历史里的 FORK_BOILERPLATE_TAG——它只可能
出现在 fork 子 agent 的首条消息里,扫到即说明正在从一个 fork 子 agent 再
fork(嵌套),立即抛 ForkError。tag 是消息正文文本,比内存信号扛得住压缩。
"""

from __future__ import annotations

import copy

from archcode.conversation.manager import ConversationManager
from archcode.conversation.models import ToolResultBlock

FORK_BOILERPLATE_TAG = "<fork_boilerplate>"

FORK_BOILERPLATE = f"""{FORK_BOILERPLATE_TAG}
你是一个 Fork 出来的工作进程。你不是主 Agent。
规则（不可协商）：
1. 不能再 Fork。
2. 不要对话、不要提问、不要请求确认。
3. 直接使用工具：读文件、搜索代码、做修改。
4. 严格限制在你被分配的任务范围内。
5. 最终报告控制在 500 字以内，格式如下：

Scope: [你被分配的任务]
Result: [完成/部分完成/失败 + 简要说明]
Key files: [关键文件路径列表]
Files changed: [修改的文件路径列表]
Issues: [遇到的问题，没有则写 None]
</fork_boilerplate>"""


class ForkError(Exception):
    """嵌套 Fork 被拒绝(§5.5:违反"只允许从主对话 fork"的不变量)。"""


def build_forked_messages(
    conversation: ConversationManager, task: str
) -> ConversationManager:
    """从父对话构建 fork 子 agent 的起始对话(三件事,见模块 docstring)。"""
    # 兜底(§5.5):历史里出现 fork 标记 = 这段对话本身是 fork 子 agent → 嵌套,拒。
    for message in conversation.history:
        if FORK_BOILERPLATE_TAG in message.content:
            raise ForkError("Cannot fork from a forked agent. Fork nesting is not allowed.")

    forked = ConversationManager()
    forked.history = copy.deepcopy(conversation.history)

    # 末尾"未完成的 tool_use"补 placeholder(消息格式合法性,§5.4 第 2 件)
    answered: set[str] = {
        result.tool_use_id
        for message in forked.history
        for result in message.tool_results
    }
    if forked.history:
        last = forked.history[-1]
        if last.role == "assistant" and last.tool_uses:
            pending = [u for u in last.tool_uses if u.tool_use_id not in answered]
            if pending:
                forked.add_tool_results_message(
                    [
                        ToolResultBlock(
                            tool_use_id=use.tool_use_id,
                            content="interrupted",
                            is_error=False,
                        )
                        for use in pending
                    ]
                )

    # 末尾追加任务指令(§5.4 第 3 件):硬约束 boilerplate + 具体任务
    forked.add_user(f"{FORK_BOILERPLATE}\n\n你的任务：\n{task}")
    return forked
