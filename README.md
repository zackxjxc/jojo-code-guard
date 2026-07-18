# jojo-code-guard

啾啾代码守护用于保护 C++ 老项目的编码、BOM、换行和最小 Git diff。

发布仓库中的 `.bat/.cmd` 使用 UTF-8 无 BOM + CRLF，并通过 `.gitattributes` 的 `-text diff` 保留脚本字节。

## 安装

### Codex

```bash
codex plugin marketplace add ZACKhdn/jojo-code-guard
codex plugin add jojo-code-guard@jojo-code-guard
```

### Claude Code

```bash
# 在 Claude Code 会话内执行
/plugin marketplace add ZACKhdn/jojo-code-guard
/plugin install jojo-code-guard@jojo-code-guard
```

安装后重新打开会话。插件管理器会从 GitHub 获取仓库，不需要用户手动 clone 或复制 Skill。Skill 不会复制到业务仓库；项目只需要自己的 `AGENTS.md`、`.editorconfig` 和 `.gitattributes`。

首次提交本仓库时，在 Windows 工作树上也要为 Claude hook 写入 Unix 可执行位：

```bash
git add --chmod=+x hooks/run-hook.cmd hooks/session-start
```

否则 macOS/Linux 从 GitHub 安装后可能无法直接启动 SessionStart hook。

日常修改会自动遵守最小 diff 规则。低频检查可以使用 `doctor`、`check-diff` 和 `help`，也可以直接用自然语言提出要求。

新增 `.ps1` 默认采用 UTF-8 无 BOM + LF；只有明确使用 Windows PowerShell 5.1 且包含中文时，才在项目 `AGENTS.md` 中声明 UTF-8 BOM 例外。

Codex 中会分别显示以下入口：

- `jojo-code-guard`：日常自动守护编码、换行和最小 diff。
- `jojo-code-guard:doctor`：按需执行设备、Git 和仓库诊断。
- `jojo-code-guard:check-diff`：按需检查编码、换行和未提交 diff。
- `jojo-code-guard:help`：查看功能和安全边界。

主动入口示例：

```text
Codex：选择对应的 `jojo-code-guard` 入口，或使用 `$jojo-code-guard` 后说明要执行的功能
Claude Code：/jojo-code-guard:doctor
```

`check-diff` 和 `help` 以同样方式调用。不要把 `codex doctor` 当作 Skill 命令；那是 Codex 自带的运行环境诊断。

本仓库跟踪 `.vscode/settings.json`，仅用于统一插件源码自身的编辑设置。Skill 会检查业务仓库已有的 VS Code 设置、是否被 `.gitignore` 忽略以及是否已纳入 Git，但不会自动创建、覆盖或强制跟踪该文件。业务仓库通常优先跟踪 `.editorconfig` 和 `.gitattributes`；只有确定设置是团队共享内容时，才跟踪 `.vscode/settings.json`。
