---
name: jojo-code-guard-check-diff
description: 按需检查当前仓库的编码、BOM、换行、Git 空白错误和异常 diff；只读，不自动格式化。
---

# 啾啾代码守护：检查差异

这是 `jojo-code-guard` 的低频差异验收入口，不替代主 Skill 的日常编辑规则。

## 执行要求

1. 先读取当前仓库根目录的 `AGENTS.md`、`.editorconfig` 和 `.gitattributes`。
2. 使用主 Skill 目录中的 `scripts/check_diff.py` 检查暂存区、未暂存修改和未跟踪新增文本：

```text
python "<jojo-code-guard>/scripts/check_diff.py" --repo .
```

3. 需要时使用 `--staged-only` 只检查暂存区，使用 `--json` 输出机器可读结果。
4. 报告编码、BOM、换行、Git 空白错误、异常膨胀和疑似格式污染。
5. 发现问题时只提出最小修复范围，不直接格式化整个文件。

## 适用场景

- 修改已有文本文件或新增源代码文件后验收。
- 处理中文、BOM、LF/CRLF 或编码异常。
- 工作区已有用户修改或 diff 规模异常。
- 用户要求检查当前未提交修改。
