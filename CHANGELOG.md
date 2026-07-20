# 更新日志

本文件记录 jojo-code-guard 的重要变更。

## [0.2.3] - 2026-07-20

- 增加 Claude/Codex 文件写入后的 PostToolUse 差异检查，并让 Codex 同步脚本复制 Hook 资源。
- 增加 Codex 可发现的 `jojo-code-guard-commit` Skill，明确两端的提交入口。
- 为 Codex manifest 显式登记 `hooks/hooks.json`，并同步 PostToolUse 资源。
- PostToolUse 发现问题时改为以结构化上下文反馈，不把已完成的写入伪装成 Hook 执行失败。
- 严格检查首个提交、未知文本后缀、替换字符和已有文件末尾换行变化。
- 修复本地 Hook 复制脚本漂移，避免未知二进制被通配属性强制生成文本 diff。
- 已知源码和文档保留字节的同时启用 Git diff；新增工具文件的编码、BOM、换行和权限位错误统一阻断。

## [0.2.2] - 2026-07-20

- 完善全局规则同步与 doctor 诊断流程。
- 增加跨平台回归覆盖，并改进 Claude 插件诊断和 hook 启动流程。
- 补充 Skill 手动升级、定时自动升级和版本检查说明。

## [0.2.0]

- 建立编码、BOM、换行和最小 Git diff 的自动守护规则。
- 提供 `doctor`、`check-diff` 和 `help` 入口。
- 支持 Codex Skill 与 Claude Code 插件适配包。
- 增加 Git hook、全局规则同步及 PowerShell/Windows 环境检查能力。
