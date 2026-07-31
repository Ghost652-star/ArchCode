# 钩子系统:用户在 agent 生命周期的关键节点配置脚本(将来用)。
#
# 四个标准事件:
# - turn_start    每轮循环开头(注入 plan / memory / skills)
# - turn_end      每轮循环结束(清理 / 状态保存)
# - pre_send      LLM API 调用前(权限检查 / 修改 prompt)
# - post_receive  LLM API 返回后(日志 / 改结果)
#
# 待实现的子模块:
# - engine.py     钩子引擎(注册 / 调用 / 错误处理)
# - loader.py     从配置加载用户钩子
