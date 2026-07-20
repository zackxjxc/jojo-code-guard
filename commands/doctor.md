---
description: 诊断当前设备、Git 环境和仓库保护配置，并逐步引导安全修复
---

使用 jojo-code-guard 的 doctor 流程。先只读检查操作系统、Git 全局/本地配置、Git LFS、ripgrep、
Python、
CMake/Ninja（Windows 还检查 PowerShell 7、gsudo、winget），再检查根目录 AGENTS.md（如果存在）、
.editorconfig、.gitattributes、.gitignore、.vscode/settings.json（含是否被 Git 忽略）、Git pre-commit
和当前状态；同时核对已登记的 Claude 插件是否包含 SessionStart 会话注入和 PostToolUse 写入检查，以及仓库 Hook 复制脚本是否与当前 Skill 版本匹配。
Codex 使用原生 Skill Discovery；插件生命周期 Hook 是否可用取决于客户端版本，本 doctor 不把它当作已启用能力。
需要确定性检查时，Claude 可运行
`python "${CLAUDE_PLUGIN_ROOT}/skills/jojo-code-guard/scripts/doctor.py" --repo .`；其他客户端使用当前实际加载的
Skill 目录中的同名脚本。输出 OK、WARNING、
ACTION_REQUIRED、BLOCKED；缺失项先展示影响，确认后才修复。AGENTS.md 是可选项目规则文件，
doctor 不会自动创建；用户需要时可自行创建并写入规则。Claude 插件缺失或禁用时只报告
安装、启用方法，
不复制 hook 或改写用户设置。未经用户确认，不会自动转码、批量格式化或执行外部状态变更。

同时比较 `~/.claude/CLAUDE.md` 和 `~/.codex/AGENTS.md`。用户选择同步时，先分别使用
`--sync-global-rules overwrite` 或 `--sync-global-rules merge` 预览整文件覆盖与受管块合并的差异；
只有明确确认后才追加 `--yes`。合并必须保留目标原文，并幂等更新 jojo-code-guard 受管块。

Windows 使用 `--install-tools --yes` 安装设备工具时，如果当前终端没有管理员权限，doctor 会生成临时 PowerShell 安装脚本并通过 UAC 请求提权；请在系统提示中由使用者自行确认授权。
