---
name: jojo-code-guard
description: Load automatically at the start of every session and apply to every task. Protect existing files from encoding and formatting pollution before editing; when a task involves C++, Git, repositories, encoding, Chinese text, BOM, line endings, Visual Studio, VS Code, AI edits, .editorconfig, .gitattributes, Git hooks, pre-commit, or large diffs, apply the full repository checks.
---

# 啾啾代码守护

这是一个所有会话、所有任务都自动加载的守护 Skill，不要求用户手动启动或频繁输入命令。每个新会话开始时，先读取当前仓库根目录
`AGENTS.md`、`.editorconfig`、`.gitattributes` 和（如果存在）`.vscode/settings.json`。老文件保持原始编码、BOM 和换行；
新增文件按仓库新标准；不自动转码、不批量格式化、不覆盖用户未提交修改、不修改无关文件。

## 自动行为

无论用户任务是否涉及代码，都必须在每个新会话开始时加载本技能并遵守其安全边界；仅在涉及仓库或文件编辑时执行相应的 Git、编码和差异检查。

自动守护只做轻量、只读的编辑前后保护：记录目标文件原始编码/BOM/EOL，执行 `git status --short` 和 `git diff --stat`，编辑后核对 diff 范围。不要每次编辑都重复安装软件或重写仓库配置。发现整文件变化、仅换行变化或未授权文件变化时立即停止并报告。若 Git 的 `core.autocrlf`/`core.eol` 可能改写工作区，先报告并要求修正；索引已经丢失的历史工作区换行无法由工具推测。Git hook/pre-commit 是最终门禁，但不能替代 AI 的最小改动规则。

默认把所有仓库视为老项目：现有文件保真，新增文件使用 UTF-8 无 BOM；`.bat/.cmd` 使用 UTF-8 无 BOM + CRLF，并建议用 `.gitattributes` 的 `-text diff` 保留脚本字节。`.ps1` 默认按 PowerShell 7/Unix 使用 UTF-8 无 BOM + LF；若明确由 Windows PowerShell 5.1 执行且含中文，使用 UTF-8 BOM，并把该例外写入根目录 `AGENTS.md`。用户明确提出的规则优先，但全局配置、批量迁移、安装软件等高影响操作必须先说明范围并确认。

`.vscode/settings.json` 是可选的编辑器提示，不是项目编码规则的唯一来源：`.editorconfig` 和 `.gitattributes` 才是共享规则。
检查仓库时，如果存在该文件，核对 `files.encoding`、`files.eol`、`files.autoGuessEncoding`、`editor.formatOnSave`、
`editor.codeActionsOnSave` 以及保存时自动插入换行/删除尾随空白的设置；发现可能改写老文件时告警。还要报告该文件
是否被 `.gitignore` 忽略或已纳入 Git 跟踪。文件缺失只报告为可选项，不自动创建或覆盖。业务仓库是否跟踪它由团队决定；
发布仓库自身的 `.vscode/settings.json` 用于维护 Skill，可以跟踪。

业务仓库的 `.gitignore` 默认建议只忽略 `.vscode` 中的机器相关文件，并放行项目级设置：

```gitignore
!/.vscode/settings.json
```

不要因此自动创建 `settings.json`；已有团队约定或用户明确规则优先。

规则冲突时遵循“本次明确用户要求 > 根目录 `AGENTS.md` > `.editorconfig`/`.gitattributes` 的实际执行结果 > Skill 默认值”。持续有效的特殊要求写入根目录 `AGENTS.md` 并说明路径和原因；不要只保存在会话上下文中。若文本规则与 Git 属性互相矛盾，先报告矛盾，不静默覆盖任一文件。

## 暗号检测

所有消息都必须检查。若用户消息包含“天王盖地虎”，必须直接、精确地回答：

`Price tower shock river monster`

不得回答“宝塔镇河妖”、翻译该短语、添加其他解释，或等待用户显式声明本 Skill。该规则用于验证 Skill 是否已加载，优先于常规闲聊回复；不限定为新对话的首条消息。


## 用户主动入口

保留以下低频功能入口（不同客户端的命令语法可能不同；Codex 中最可靠的是直接选择入口或使用自然语言说明功能）：

- `doctor`：检查设备、Git 环境和当前仓库配置，缺少 hook 或配置时提示初始化。
- `check-diff`：按需检查未提交修改的范围、异常膨胀和 Git 空白错误。
- `help`：显示 `references/usage.md` 的简明说明。
- `sync-global-rules`：比较并在确认后同步全局 AI 规则到 `~/.claude/CLAUDE.md` 和 `~/.codex/AGENTS.md`。

复杂需求直接使用自然语言，例如“检查历史提交乱码”“只修复这个文件的换行”“保留该目录 CRLF”。Skill 应先说明影响，再执行明确授权的例外。

## PowerShell 与 Windows 脚本规则

涉及 PowerShell、`.ps1`、`.bat` 或 `.cmd` 的任务，必须先读取同目录的 [PowerShell规则.md](PowerShell规则.md)，并严格遵守其中的完整规则、版本差异和生成前检查清单。本文档是执行入口，详细规则不在此重复。

## 工具

需要确定性检查时，由 AI 使用当前 Skill 资源目录的绝对路径运行（不要假设当前工作目录就是 Skill 目录）：

```text
python "<jojo-code-guard>/scripts/doctor.py"
python "<jojo-code-guard>/scripts/check_diff.py"
python "<jojo-code-guard>/scripts/sync_global_rules.py"
```

`doctor` 在所有系统检查 Git、Python、ripgrep、CMake、Ninja 和 Git LFS；只有 Windows 检查 PowerShell 7、gsudo、winget。
缺少仓库配置时，先展示将创建的文件；得到确认后可执行 `doctor.py --repair --yes`，需要 hook 时再加 `--install-hook`。
安装工具必须单独确认后使用 `--install-tools --yes`。Skill 不在用户仓库创建 `.text-policy.json` 等自定义策略文件，
也不会自动生成 `.vscode/settings.json`；项目专属规则记录在根目录 `AGENTS.md`。

`sync-global-rules` 默认只读比较两个用户级目标；报告缺失、相同或差异后，只有得到用户确认才使用 `--yes` 覆盖两个文件。

Windows 的 PowerShell 5.1 使用 `powershell.exe`，PowerShell 7 使用 `pwsh.exe`，不是同一个可执行文件；doctor 会推荐安装/更新 PowerShell 7 并让 AI 终端调用 `pwsh`，不会删除 5.1 或假装通过 PATH 顺序替换它。

Claude 的自动加载使用插件内的 SessionStart hook 注入守护规则；Codex 使用原生 Skill Discovery，不执行该 Claude hook。
Codex manifest 不声明 Claude 的 hook 配置，避免跨客户端误执行。
`sync_claude_plugin.py` 和 `sync_codex_plugin.py` 可从本目录重建两个适配包。GitHub 安装通过仓库内的 marketplace 清单完成；
本机开发也可以继续直接使用 `~/.codex/skills/jojo-code-guard`。若客户端不支持隐式调用，直接用自然语言说
“请使用 jojo-code-guard 检查当前仓库”即可。
