---
description: 诊断当前设备、Git 环境和仓库保护配置，并逐步引导安全修复
---

使用 jojo-code-guard 的 doctor 流程。先只读检查操作系统、Git 全局/本地配置、Git LFS、ripgrep、Python、
CMake/Ninja（Windows 还检查 PowerShell 7、gsudo、winget），再检查根目录 AGENTS.md（如果存在）、
.editorconfig、.gitattributes、.gitignore、.vscode/settings.json（含是否被 Git 忽略）、Git pre-commit 和当前状态。
需要确定性检查时运行
`python "${CLAUDE_PLUGIN_ROOT}/skills/jojo-code-guard/scripts/doctor.py" --repo .`。输出 OK、WARNING、
ACTION_REQUIRED、BLOCKED；缺失项先展示影响，确认后才修复。AGENTS.md 是可选项目规则文件，doctor
不会自动创建；用户需要时可自行创建并写入规则。Claude 插件缺失或禁用时只报告安装、启用方法，不复制 hook
或改写用户设置。禁止自动转码、批量格式化或提交。

Windows 使用 `--install-tools --yes` 安装设备工具时，如果当前终端没有管理员权限，doctor 会生成临时 PowerShell 安装脚本并通过 UAC 请求提权；请在系统提示中由使用者自行确认授权。
