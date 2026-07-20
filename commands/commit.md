---
description: 验证并创建 Git 提交
---

使用 jojo-code-guard 的提交闭环。仅在用户明确要求创建 Git 提交时执行：

Claude 使用本命令；Codex 使用 `jojo-code-guard-commit` Skill。两者遵循同一套检查顺序。

1. 读取项目规则和当前 `git status --short`，审阅已有暂存区和未暂存修改，不覆盖或带入用户未授权的修改。
2. 运行项目已有的最小相关测试或构建；无法运行时报告原因。
3. 只暂存本次变更文件和明确授权的 hunk；同一文件存在用户修改时使用分块暂存并复核
   `git diff --cached`，再定位当前客户端加载的 Skill 目录并运行
   `python "<jojo-code-guard>/scripts/check_diff.py" --repo . --staged-only`。
4. 检查通过后使用中文、一句话的 commit 信息执行 `git commit`。
5. 提交完成后读取 `git status --short` 和提交摘要确认结果。

检查失败、测试失败、存在未授权文件或 Hook 阻止提交时立即停止，不使用 `--no-verify` 绕过门禁。
