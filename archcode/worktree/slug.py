"""slug 校验与扁平化(worktree-design §2.3.1/§2.3.2)。

name 来自 LLM 工具调用参数,不可信;它会拼进文件路径并成为 git 分支名。
防线是白名单:不识别"坏目录",只缴械逃逸能力(..、\、: 等不在允许字符集内),
任何通过校验的名字拼进路径后不可能落在统一落点之外。
"""

from __future__ import annotations

import re

MAX_SLUG_LENGTH = 64
# "+" 在字符集内:它是压平分隔符(flatten_slug 把 / 换成 +),组合名
# `team-<队>+<队员>`(agent-teams-design §7.1 步骤 2)依赖它;+ 不是任何平台的
# 路径分隔符,放行不削弱防穿越能力
_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9._+-]+$")


def validate_slug(name: str) -> str | None:
    """校验 worktree 名称。返回 None=通过;返回 str=错误原因(直接给 LLM 看)。"""
    if not name:
        return "name cannot be empty"
    if len(name) > MAX_SLUG_LENGTH:
        return f"name too long (max {MAX_SLUG_LENGTH} characters)"

    for seg in name.split("/"):
        if not seg:
            return "name contains empty segment"
        # 正则会放行 ".."(点在白名单字符集内),特殊路径段必须显式特判
        if seg in (".", ".."):
            return "name must not contain '.' or '..' as a segment"
        if not _SEGMENT_RE.match(seg):
            return f"invalid segment: {seg!r} (allowed: letters, digits, '.', '-', '_', '+')"

    return None


def flatten_slug(name: str) -> str:
    """/ → +:目录名与分支名共用压平结果(单层目录;消除分支引用 D/F 冲突)。"""
    return name.replace("/", "+")
