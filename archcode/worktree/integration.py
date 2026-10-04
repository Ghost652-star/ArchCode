"""worktree 与子 agent 的串联辅助(worktree-design §5.1)。"""

from __future__ import annotations

_WORKTREE_NOTICE_TEMPLATE = """\
[WORKTREE CONTEXT]
你继承了父 Agent 的对话上下文。
你当前在一个隔离的 Git Worktree 中工作：{wt_path}
父 Agent 的工作目录是：{parent_cwd}

重要：
- 父对话中提到的文件路径指向的是父目录（{parent_cwd}），不是你当前目录；
- 读写这些文件前，先把路径换算到 worktree 下的对应路径；
- 编辑前必须重新读取文件——你的副本内容可能与父对话中提到的版本已经不同。
[/WORKTREE CONTEXT]"""


def build_worktree_notice(parent_cwd: str, wt_path: str) -> str:
    """上下文通知:拼在任务文本最前(子 agent 首条消息必带,一次注入全程有效)。

    没有它,子 agent 不知道自己在副本里——会拿父对话里指向主目录的绝对路径
    读写(写穿隔离),或按父对话的旧版本理解副本文件(认知偏差,编辑必错)。
    """
    return _WORKTREE_NOTICE_TEMPLATE.format(parent_cwd=parent_cwd, wt_path=wt_path)
