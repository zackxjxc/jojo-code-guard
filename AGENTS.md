# 仓库测试选择规则

- 修改后先运行 `python scripts/select_tests.py` 查看计划；本地迭代可用 `python scripts/select_tests.py --run` 执行选出的测试。
- 检查已提交区间时使用 `python scripts/select_tests.py --base <基线提交> --head HEAD`；默认模式只检查相对 HEAD 的暂存、未暂存和未跟踪文件。
- `scripts/select_tests.py` 是文件到测试模块映射的唯一事实来源；不要在本文复制映射表。
- 选择器给出的是最低验证集合。改动涉及权限/ACL、原子替换、同步事务、符号链接或 reparse、编码/换行、Git Hook、CI，或影响范围无法确定时，必须升级验证，不得减少测试。
- `cross-platform: yes` 表示本地定向测试不足以证明完成；合并前必须由 GitHub Actions 的相关平台矩阵验证。
- `scope: full`、未知路径、共享测试基础设施或选择器无法运行时，执行 `python -B -m unittest discover -s tests`。
- `master`、发布和手动完整验证继续运行完整测试；选测只用于缩短本地迭代和将来的 PR 快速反馈，不替代最终门禁。
- 修改选择器或本规则时至少运行 `python -B -m unittest tests.test_select_tests`；不得通过同时修改规则与测试来静默缩小覆盖范围。
- 测试输出遵守长任务日志控制：成功只报告摘要，失败先报告最早根因和有限上下文。
