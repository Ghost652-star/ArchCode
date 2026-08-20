"""上下文管理:长会话接近模型窗口上限时自动压缩 / 摘要旧消息。

子模块:
- ``manager.py``    Layer 1:工具结果预算(三 Pass 替换 + 决策冻结)
- ``compactor.py``  Layer 2:LLM 摘要(auto / force / 熔断器 / drop-oldest 重试)
- ``recovery.py``   RecoveryState(线程安全的文件 / skills 记录)+ 附件渲染
"""

from __future__ import annotations

from archcode.context.compactor import (
    AUTO_COMPACT_SAFETY_MARGIN,
    KEEP_MAX_TOKENS,
    KEEP_RECENT_TOKENS,
    KEEP_RECENT_TURNS,
    MANUAL_COMPACT_SAFETY_MARGIN,
    MIN_SUMMARIZE_PREFIX_TOKENS,
    MIN_VALID_SUMMARY_TOKENS,
    SUMMARY_OUTPUT_RESERVE,
    SUMMARY_PROMPT,
    CompactCircuitBreaker,
    CompactEvent,
    ForceCompactBreaker,
    auto_compact,
    build_compact_messages,
    compute_compact_threshold,
    extract_summary,
    force_compact,
    should_auto_compact,
)
from archcode.context.manager import (
    AGGREGATE_CHAR_LIMIT,
    KEEP_RECENT_TURNS as _MGR_KEEP_RECENT_TURNS,
    OLD_RESULT_SNIP_CHARS,
    PREVIEW_CHARS,
    SINGLE_RESULT_CHAR_LIMIT,
    TOOL_RESULTS_DIR,
    ContentReplacementRecord,
    ContentReplacementState,
    apply_tool_result_budget,
    cleanup_tool_results,
    ensure_session_dir,
    make_persisted_preview,
    persist_tool_result,
)
from archcode.context.recovery import (
    FileReadRecord,
    RecoveryState,
    SkillInvocationRecord,
    build_recovery_attachment,
)

__all__ = [
    # compactor
    "AUTO_COMPACT_SAFETY_MARGIN",
    "KEEP_MAX_TOKENS",
    "KEEP_RECENT_TOKENS",
    "KEEP_RECENT_TURNS",
    "MANUAL_COMPACT_SAFETY_MARGIN",
    "MIN_SUMMARIZE_PREFIX_TOKENS",
    "MIN_VALID_SUMMARY_TOKENS",
    "SUMMARY_OUTPUT_RESERVE",
    "SUMMARY_PROMPT",
    "CompactCircuitBreaker",
    "CompactEvent",
    "ForceCompactBreaker",
    "auto_compact",
    "build_compact_messages",
    "compute_compact_threshold",
    "extract_summary",
    "force_compact",
    "should_auto_compact",
    # manager
    "AGGREGATE_CHAR_LIMIT",
    "OLD_RESULT_SNIP_CHARS",
    "PREVIEW_CHARS",
    "SINGLE_RESULT_CHAR_LIMIT",
    "TOOL_RESULTS_DIR",
    "ContentReplacementRecord",
    "ContentReplacementState",
    "apply_tool_result_budget",
    "cleanup_tool_results",
    "ensure_session_dir",
    "make_persisted_preview",
    "persist_tool_result",
    # recovery
    "FileReadRecord",
    "RecoveryState",
    "SkillInvocationRecord",
    "build_recovery_attachment",
]