---
name: jojo-code-guard-sync-global-rules
description: 检查并同步 Skill 内置的全局 AI 规则到 ~/.claude/CLAUDE.md 和 ~/.codex/AGENTS.md；覆盖前必须报告差异并取得用户确认。
---

# 啾啾代码守护：同步全局规则

此入口将同一份 Skill 内置规则写入两个固定目标，不判断当前使用的是 Claude 还是 Codex：

- `~/.claude/CLAUDE.md`
- `~/.codex/AGENTS.md`

## 执行流程

1. 使用主 Skill 目录中的 `scripts/sync_global_rules.py` 执行预览：

```text
python "<jojo-code-guard>/scripts/sync_global_rules.py"
```

2. 分别报告每个目标是 `MISSING`、`IDENTICAL`、`DIFFERENT` 还是 `BLOCKED`。
3. 对已存在且不同的文件，展示编码、BOM、换行、字节摘要和文本差异范围。
4. 将预览结果和覆盖范围告知用户，未得到明确确认时停止，不得写入目标文件。
5. 用户确认后，使用同一脚本追加 `--yes`，同时覆盖两个目标；不根据当前智能体环境跳过任一目标。
6. 覆盖后再次读取两个目标，确认字节内容与 Skill 源文件一致，并报告结果。

脚本会创建缺失的父目录；目标为符号链接时停止并报告，不跟随链接覆盖其他文件。
