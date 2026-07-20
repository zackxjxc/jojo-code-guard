---
name: jojo-code-guard-doctor
description: 按需诊断设备、Git、仓库保护和全局规则；默认只读，修复、同步或安装工具前必须取得用户确认。
---

# 啾啾代码守护：检查环境

这是 `jojo-code-guard` 的低频诊断入口，不替代主 Skill 的日常编辑规则。

## 执行要求

1. 先读取当前仓库根目录的 `AGENTS.md`（如果存在）、`.editorconfig`、`.gitattributes` 和可选的 `.vscode/settings.json`。
2. 使用主 Skill 目录中的 `scripts/doctor.py` 执行只读诊断：

```text
python "<jojo-code-guard>/scripts/doctor.py" --repo .
```

3. 报告 `OK`、`WARNING`、`ACTION_REQUIRED` 和 `BLOCKED`，包括两个用户级全局规则目标的差异状态。
4. 不自动转码、格式化、提交、安装工具或写入仓库配置。
5. 全局规则同步先用 `--sync-global-rules overwrite` 或 `--sync-global-rules merge` 预览差异。
6. 只有用户明确确认后，才为写入操作追加 `--yes`。
7. Claude 插件缺失或禁用时只报告安装、启用命令，不复制 hook 或改写 Claude 用户设置。

## 适用场景

- 用户要求检查设备、Git 或仓库配置。
- 比较、覆盖或合并 Claude 与 Codex 的用户级全局规则。
- 准备安装 pre-commit hook。
- 需要确认 PowerShell 7、Python、Git LFS 等工具是否可用。
