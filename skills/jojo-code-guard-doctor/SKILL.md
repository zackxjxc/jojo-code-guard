---
name: jojo-code-guard-doctor
description: 按需诊断当前设备、Git 环境和仓库保护配置；默认只读，修复或安装工具前必须取得用户确认。
---

# 啾啾代码守护：检查环境

这是 `jojo-code-guard` 的低频诊断入口，不替代主 Skill 的日常编辑规则。

## 执行要求

1. 先读取当前仓库根目录的 `AGENTS.md`（如果存在）、`.editorconfig`、`.gitattributes` 和可选的 `.vscode/settings.json`。
2. 使用主 Skill 目录中的 `scripts/doctor.py` 执行只读诊断：

```text
python "<jojo-code-guard>/scripts/doctor.py" --repo .
```

3. 报告 `OK`、`WARNING`、`ACTION_REQUIRED` 和 `BLOCKED`，说明影响范围。
4. 不自动转码、格式化、提交、安装工具或写入仓库配置。
5. 只有用户明确确认后，才使用 `--repair`、`--install-hook`、`--install-tools` 和 `--yes`。
6. Claude 插件缺失或禁用时只报告安装、启用命令，不复制 hook 或改写 Claude 用户设置。

## 适用场景

- 用户要求检查设备、Git 或仓库配置。
- 准备安装 pre-commit hook。
- 需要确认 PowerShell 7、Python、Git LFS 等工具是否可用。
