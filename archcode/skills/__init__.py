# 可插拔技能:用户或社区定义的 markdown 模板(写文档、跑测试、Code Review 等,将来用)。
#
# 待实现的子模块:
# - loader.py     从 .archcode/skills/ 加载 SKILL.md
# - executor.py   执行技能逻辑并注入 system-reminder
# - TODO(recovery):executor.py 的 inline / fork 路径在 Skill 实际激活后，
#   必须调用 Agent 的 RecoveryState.record_skill_invocation()。记录渲染后的
#   指令、执行模式及 fork 子会话标识，确保 Layer 2 压缩后能恢复 Skill 工作现场。
