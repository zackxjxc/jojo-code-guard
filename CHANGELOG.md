# 更新日志

本文件记录 jojo-code-guard 的重要变更。

## [0.2.5] - 2026-07-20

- `PostToolUse` 改用可让 AI 继续修复的跨客户端阻断反馈，不再用 `continue: false` 直接终止处理。
- `SessionStart` 改为结构化上下文；Python 不可用时保留两端都支持的普通 stdout 回退。
- `PostToolUse` 增加 Bash 和 PowerShell 覆盖，并新增带重入保护的 `Stop` 回合结束兜底检查。
- doctor 增加 Codex 插件缓存、版本、启用状态和 Hook 功能检查，并将信任与实际执行明确为人工验收项。
- 两端同步器清理不再发布的临时设计文档，测试覆盖协议、Windows 路径、Stop 重入和同步结果。

## [0.2.4] - 2026-07-20

- 移除提交专用 Skill 和 Claude/Codex 提交命令，恢复初始化后由主 Skill 和 Hook 自动守护的定位。
- 修复 Codex 适配器同步时遗留旧 `commands/commit.md` 的问题，并补充回归覆盖。
- 明确 Git pre-commit 只是可选的初始化后机械门禁，不再提供 AI 提交流程入口。
- 修正 Codex marketplace 的本地插件源格式，确保安装后能发现并加载 0.2.4 Skill。
- 修正 Codex Hook 发现和执行路径：当前实测版本读取 `hooks/hooks.json`，并通过客户端注入的插件根路径从业务仓库 cwd 定位插件脚本。
- 两端同步器只清理自身已知的旧入口，不删除插件目录中未标记的其他文件。
- 保留发布仓库原有 UTF-8/LF 配置；`unset` 仅用于缺少配置的业务老项目初始化模板，不覆盖现有规则。
- 增加规则生效与验收矩阵，并将缺失的 pre-commit 明确为可选初始化门禁。
- doctor 现在同时验收 Claude 的 SessionStart 会话注入和 PostToolUse 写入检查。

## [0.2.3] - 2026-07-20（未发布开发版）

- 增加 Claude/Codex 文件写入后的 PostToolUse 差异检查，并让 Codex 同步脚本复制 Hook 资源。
- （开发版，已在 0.2.4 移除）增加 Codex 可发现的 `jojo-code-guard-commit` Skill。
- （开发版；0.2.4 改为客户端默认发现）曾为 Codex manifest 显式登记 `hooks/hooks.json`，并同步 PostToolUse 资源。
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
