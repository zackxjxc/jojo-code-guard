---
description: 诊断当前设备、Git 环境和仓库保护配置，并逐步引导安全修复
---

先完整读取 `${CLAUDE_PLUGIN_ROOT}/skills/jojo-code-guard/SKILL.md`，再完整读取
`${CLAUDE_PLUGIN_ROOT}/skills/jojo-code-guard/references/通用行为规则.md`；任一资源不可读时暂停当前任务并报告。

使用 jojo-code-guard 的 doctor 流程。先只读检查操作系统、Git 全局/本地配置、Git LFS、ripgrep、
Python、
CMake/Ninja（Windows 还检查 PowerShell 7、gsudo、winget），再检查根目录 AGENTS.md（如果存在）、
.editorconfig、.gitattributes、.gitignore、.vscode/settings.json（含是否被 Git 忽略）、Git pre-commit
和当前状态；同时核对 Claude/Codex 插件缓存版本、启用状态、Hook 功能，以及 `SessionStart`、
`PostToolUse`、`Stop` 和仓库 Hook 复制脚本是否与当前 Skill 版本匹配。Codex Hook 信任和两端实际执行
只能提示人工验收，本 doctor 不根据配置或缓存把它们当作已启用能力。
doctor 还会只读查询远端发布版本；Skill 不会自行更新，发现新版本时应提示用户通过对应客户端更新并重启。
需要确定性检查时，Claude 可运行
`python "${CLAUDE_PLUGIN_ROOT}/skills/jojo-code-guard/scripts/doctor.py" --repo .`；其他客户端使用当前实际加载的
Skill 目录中的同名脚本。输出 OK、WARNING、
ACTION_REQUIRED、BLOCKED；缺失项先展示影响，确认后才修复。AGENTS.md 是可选项目规则文件，
doctor 不会自动创建；用户需要时可自行创建并写入规则。Claude 或 Codex 插件缺失、陈旧或禁用时只报告
安装、升级和启用方法，
不复制 hook 或改写用户设置。未经用户确认，不会自动转码、批量格式化或执行外部状态变更。

同时检查 `~/.claude/CLAUDE.md` 和 `~/.codex/AGENTS.md` 中的 jojo-code-guard 自动加载节。
用户选择同步时，先使用 `--sync-global-rules` 预览节级差异，只有明确确认后才追加 `--yes`。
已有文件只新增或更新该节，不修改标题和其他规则；文件不存在时才创建普通标题。

Windows 使用 `--install-tools --yes` 安装设备工具时，doctor 以包管理器解析后的绝对路径直接执行，不生成临时提权脚本；安装器需要 UAC 时，必须由使用者在系统提示中自行确认授权。
