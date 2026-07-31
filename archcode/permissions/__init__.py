# 权限系统：在工具调用前按策略决定 allow / ask / deny。
#
# 模式参考：DEFAULT(默认,非读问用户) / ACCEPT_EDITS(写自动允许) / PLAN(只读+plan 文件)
# / BYPASS(全允许) / CUSTOM(规则配置)
#
# 待实现的子模块：
# - modes.py      模式枚举与策略矩阵
# - checker.py    工具执行前的权限判断
# - rules.py      YAML 规则引擎
# - dialog.py     ask 模式下的用户确认 UI
