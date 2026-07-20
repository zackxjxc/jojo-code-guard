---
name: jojo-code-guard
description: Load automatically at the start of every session and apply to every task. Protect existing files from encoding and formatting pollution before editing; when a task involves C++, Git, repositories, encoding, Chinese text, BOM, line endings, Visual Studio, VS Code, AI edits, .editorconfig, .gitattributes, Git hooks, pre-commit, or large diffs, apply the full repository checks.
---

# 啾啾代码守护

这是一个所有会话、所有任务都自动加载的守护 Skill，不要求用户手动启动或频繁输入命令。每个新会话开始时，先读取当前仓库根目录
`AGENTS.md`（如果存在）、`.editorconfig`、`.gitattributes` 和（如果存在）`.vscode/settings.json`。老文件保持原始编码、BOM 和换行；
新增文件按仓库新标准；不自动转码、不批量格式化、不覆盖用户未提交修改、不修改无关文件。

## 自动行为

无论用户任务是否涉及代码，都必须在每个新会话开始时加载本技能并遵守其安全边界；仅在涉及仓库或文件编辑时执行相应的 Git、编码和差异检查。

自动守护只做轻量、只读的编辑前后保护：记录目标文件原始编码/BOM/EOL，执行 `git status --short` 和
`git diff --stat`，编辑后核对 diff 范围。插件在已知编辑和 shell 工具完成后由 `PostToolUse` 自动运行
`post-write-check`，把诊断反馈给 AI；发现 `BLOCKED` 时要求 AI 继续修复，不直接终止
本轮处理。`PostToolUse` 发生在写入之后，不能撤销写入。`Stop` 会在 AI 回合准备结束时再检查一次，捕获
外部脚本或未识别工具的写入；收到 `stop_hook_active` 重入标记时直接放行，避免循环。Codex 仍使用原生
Skill Discovery，插件生命周期 Hook 是否可用取决于客户端版本、功能开关和信任状态，不能把它当作
确定性前提。如果客户端未加载 Hook，AI 必须在每次写入后运行 `check_diff.py`，再继续思考或编辑。

项目初始化时若安装了 Git pre-commit，它会在 Git 提交阶段机械检查暂存区；主 Skill 只负责守护检查，
不接管 Git 状态变更。不要每次编辑都重复安装软件或重写仓库配置。发现整文件变化、仅换行变化或未授权
文件变化时立即停止并报告。若 Git 的 `core.autocrlf`/`core.eol` 可能改写工作区，先报告并要求修正；
索引已经丢失的历史工作区换行无法由工具推测。Windows 上 `core.filemode` 必须为 `false`，否则 Unix
可执行权限位（100755 ↔ 100644）差异会令 `git status` 持续显示 0 行内容的 modified 文件。
Git hook 是可选的最终机械门禁，但不能替代 AI 的最小改动规则。

### AI 修改闭环

1. 修改前读取仓库规则和 `git status --short`，确认目标文件原始编码、BOM、换行和用户已有修改。
2. 只用最小补丁写入目标文件，不调用会批量格式化或统一换行的工具。
3. 修改后由 `PostToolUse` 自动检查，回合结束前再由 `Stop` 兜底；Hook 不可用时由 AI 在每次写入后运行
   `python "<jojo-code-guard>/scripts/check_diff.py" --repo .`。用户日常不需要输入命令；
   该检查也会发现已有文件权限位或文件类型的意外变化。
4. 发现 `BLOCKED`、整文件变化或未授权文件变化时，不继续交付或提交，先修复并重新检查本轮污染。

默认把所有仓库视为老项目：现有文件保真，新增文件使用 UTF-8 无 BOM；`.bat/.cmd` 使用 UTF-8 无 BOM + CRLF，`.gitattributes` 默认使用 `* -text`，仅对已知文本/脚本模式按需增加 `diff`。`.ps1` 默认按 PowerShell 7/Unix 使用 UTF-8 无 BOM + LF；若明确由 Windows PowerShell 5.1 执行且含中文，用户可自行创建项目规则文件记录 UTF-8 BOM 例外。用户明确提出的规则优先，但全局配置、批量迁移、安装软件等高影响操作必须先说明范围并确认。

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

规则冲突时遵循“本次明确用户要求 > 根目录 `AGENTS.md`（如果存在）> `.editorconfig`/`.gitattributes` 的实际执行结果 > Skill 默认值”。持续有效的特殊要求可由用户自行写入根目录 `AGENTS.md`；不要只保存在会话上下文中。若文本规则与 Git 属性互相矛盾，先报告矛盾，不静默覆盖任一文件。

## 暗号检测

所有消息都必须检查。若用户消息包含“天王盖地虎”，必须直接、精确地回答：

`Price tower shock river monster`

不得回答“宝塔镇河妖”、翻译该短语、添加其他解释，或等待用户显式声明本 Skill。该规则用于验证 Skill 是否已加载，优先于常规闲聊回复；不限定为新对话的首条消息。


## 用户主动入口

保留以下低频功能入口（不同客户端的命令语法可能不同；Codex 中最可靠的是直接选择入口或使用自然语言说明功能）：

- `doctor`：检查设备、Git、当前仓库和全局规则，缺少 hook 或配置时提示初始化；
  确认后可覆盖或合并全局规则。
- `check-diff`：按需检查未提交修改的范围、异常膨胀和 Git 空白错误。
- `help`：显示 `references/usage.md` 的简明说明。
复杂需求直接使用自然语言，例如“检查历史提交乱码”“只修复这个文件的换行”“保留该目录 CRLF”。Skill 应先说明影响，再执行明确授权的例外。

## PowerShell 与 Windows 脚本规则

涉及 PowerShell、`.ps1`、`.bat` 或 `.cmd` 的任务，必须先读取同目录的 [PowerShell规则.md](PowerShell规则.md)，并严格遵守其中的完整规则、版本差异和生成前检查清单。本文档是执行入口，详细规则不在此重复。

## 工具

需要确定性检查时，由 AI 使用当前 Skill 资源目录的绝对路径运行（不要假设当前工作目录就是 Skill 目录）：

```text
python "<jojo-code-guard>/scripts/doctor.py"
python "<jojo-code-guard>/scripts/check_diff.py"
```

`doctor` 在所有系统检查 Git、Python、ripgrep、CMake、Ninja 和 Git LFS；只有 Windows 检查 PowerShell 7、gsudo、winget。
无 HEAD 的新仓库默认严格检查首个提交；明确导入已有老项目基线时，才在 `check_diff.py` 上使用
`--allow-initial-baseline`，该选项只放宽可解释的新增文件编码、BOM、换行和末尾换行属性；不可解码、二进制和替换字符仍阻断。若确需让本地
`pre-commit` 接受这次一次性导入，可显式设置 `JOJO_CODE_GUARD_ALLOW_INITIAL_BASELINE=1`；Hook 会留下警告。
缺少仓库配置时，先展示将创建的文件；得到确认后可执行 `doctor.py --repair --yes`，需要 hook 时再加
`--install-hook`。`AGENTS.md` 是可选项目规则文件，doctor 不会自动创建；用户需要时可自行创建并写入规则。
Claude/Codex 自动加载由各自插件管理器维护；doctor 只读检查精确插件 ID、缓存版本、启用状态、Hook 功能和
安装资源，不调用会刷新 marketplace 的列表命令，不复制独立 Hook，也不改写客户端用户设置。Hook 信任和
真实生命周期执行只能在客户端内人工验收。
安装工具必须单独确认后使用 `--install-tools --yes`。Windows 当前终端不是管理员时，doctor 会生成临时 PowerShell 脚本并通过 UAC 请求提权；使用者需自行确认 UAC 授权。Skill 不在用户仓库创建 `.text-policy.json` 等自定义策略文件，
也不会自动生成 `.vscode/settings.json` 或 `AGENTS.md`；项目专属规则由用户按需创建并维护。

doctor 默认只读比较 `~/.claude/CLAUDE.md` 和 `~/.codex/AGENTS.md`。需要同步时，先使用
`--sync-global-rules overwrite` 预览整文件覆盖，或使用 `--sync-global-rules merge` 预览受管块合并；
只有确认差异后才追加 `--yes` 写入两个目标。合并会保留目标原文，并幂等更新
jojo-code-guard 受管块。

Windows 的 PowerShell 5.1 使用 `powershell.exe`，PowerShell 7 使用 `pwsh.exe`，不是同一个可执行文件；doctor 会推荐安装/更新 PowerShell 7 并让 AI 终端调用 `pwsh`，不会删除 5.1 或假装通过 PATH 顺序替换它。

Claude 和当前实测的 Codex 0.142.3 都从 `hooks/hooks.json` 发现 `SessionStart`、`PostToolUse` 和 `Stop`。
`SessionStart` 通过结构化上下文注入守护规则；生命周期 Hook 是否执行仍取决于客户端版本、功能开关、
信任状态和策略，不能当作必然能力。Hook 从业务仓库的当前工作目录启动，Codex 注入 `PLUGIN_ROOT` 和
兼容变量 `CLAUDE_PLUGIN_ROOT`，Claude 使用后者；命令通过这些变量定位插件脚本，不能依赖业务仓库中的相对路径。
同步脚本会复制 `hooks/` 资源。
若客户端不执行生命周期 Hook，仍回退到主 Skill 指导的检查路径。
`sync_claude_plugin.py` 和 `sync_codex_plugin.py` 可从本目录重建两个适配包。GitHub 安装通过仓库内的 marketplace 清单完成；
本机开发也可以继续直接使用 `~/.codex/skills/jojo-code-guard`。若客户端不支持隐式调用，直接用自然语言说
“请使用 jojo-code-guard 检查当前仓库”即可。
