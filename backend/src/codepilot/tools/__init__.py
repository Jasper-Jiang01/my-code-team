"""统一工具注册：子图通过 ``from codepilot.tools import ...`` 引用工具。"""

from codepilot.tools.analyze_data import (
    analyze_data,
    ba_agent_analysis,
    filter_rows,
    group_aggregate,
    plot_chart,
)

# __tools_data__：本模块对外暴露的数据分析工具清单，便于子图批量收集
# （如 for t in tools.__tools_data__）。其中 ba_agent_analysis 是兜底工具：
# 仅在本地四个原子工具无法解决用户问题（需外部业务数据/行业研究/
# 经营诊断/完整报告）时由 LLM 调用，转交 BA-Agent 后端分析。
__tools_data__ = [analyze_data, filter_rows, group_aggregate, plot_chart, ba_agent_analysis]
