---
description: 检查当前未提交代码的编码、BOM、换行和异常 diff
---

使用 jojo-code-guard 的 check-diff 流程，同时检查暂存区、未暂存修改和未跟踪新增文本。需要确定性检查时运行 `python "${CLAUDE_PLUGIN_ROOT}/skills/jojo-code-guard/scripts/check_diff.py" --repo .`。报告编码/BOM/EOL 变化、Git 自动换行策略、Git 空白错误、意外权限位/文件类型变化、疑似格式污染和大面积 diff；默认只读。无 HEAD 的仓库默认严格检查首个提交，明确导入老项目历史基线时才使用 `--allow-initial-baseline`。发现问题时先说明最小修复范围，不要直接格式化整个文件。
