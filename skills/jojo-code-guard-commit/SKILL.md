---
name: jojo-code-guard-commit
description: 在用户明确要求创建 Git 提交时，运行测试、审阅暂存区并安全创建提交。
---

# jojo-code-guard：提交闭环

仅当用户明确要求创建 Git 提交时使用本 Skill；普通修改、检查或解释请求不得自行提交。

1. 先读取仓库根目录 `AGENTS.md`（如果存在）、`.editorconfig`、`.gitattributes` 和当前状态，确认已有暂存区、未暂存修改及用户未授权内容。
2. 运行项目已有的最小相关测试或构建；无法运行时说明原因并停止提交。
3. 只暂存本次变更文件和明确授权的 hunk。一个文件同时包含用户修改时，使用分块暂存并复核 `git diff --cached`。
4. 从当前客户端实际加载的 Skill 目录定位 `scripts/check_diff.py`，执行：
   `python "<jojo-code-guard>/scripts/check_diff.py" --repo . --staged-only`
5. 检查结果没有 `BLOCKED`、测试通过且暂存区内容与用户意图一致时，用中文一句话提交信息执行 `git commit`。
6. 提交后读取 `git status --short`、`git show --stat --oneline HEAD`，确认提交摘要和工作区状态。

检查失败、存在未授权文件或 Hook 阻止提交时立即停止，不用 `--no-verify` 绕过门禁，不覆盖用户已有修改。
