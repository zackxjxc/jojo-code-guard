# 啾啾代码守护用法

jojo-code-guard 由客户端按任务原生发现，只在访问本地文本文件、诊断编码/BOM/EOL 或检查异常 diff 时
加载。项目 `AGENTS.md` 由 Codex 原生发现，插件不通过会话 Hook 重复注入 Skill 或项目规则。旧版迁移边界
见 [原生发现与旧版迁移](自动加载规则.md)。

## 文件规则

- 已有文件保持编辑前编码、BOM、换行和末尾换行；未经明确授权不批量迁移。
- 新文件优先遵循项目 `.editorconfig`、`.gitattributes` 和明确工具链要求；项目没有声明时使用 UTF-8 无 BOM
  以及目录或项目的主流换行。
- Git 的 `core.autocrlf` 或 `core.eol` 可能改写工作区时先报告并确认属性规则。Git 索引无法还原历史工作区
  的原始换行，工具不会猜测或执行 `git add --renormalize`。
- `.vscode/settings.json` 只是可选编辑器提示；缺失时不创建，存在时只报告可能重写老文件的保存设置。

已有 PowerShell 脚本始终保持原字节属性。新建 `.ps1/.psm1/.psd1` 明确面向 Windows PowerShell 5.1 且
含非 ASCII 字符时使用 UTF-8 BOM + LF；其他新脚本在项目规则未另行要求时使用 UTF-8 无 BOM + LF，Unix
shebang 脚本禁止 BOM。Visual Studio/MSVC 的新增 `.rc/.rc2` 默认使用 UTF-8 BOM + LF，这是旧资源工具链
例外。新增 `.bat/.cmd` 使用 UTF-8 无 BOM + CRLF，并建议以 `*.bat text eol=crlf` 和
`*.cmd text eol=crlf` 固定检出格式。已有仓库补充属性前必须说明后续 checkout/reset/暂存的转换影响；插件
不自动改写现有脚本或暂存区。

## 生命周期检查

插件只注册以下事件：

- `SessionStart`：只匹配 `compact`，向压缩后的即时续跑发送一条有界恢复提示；不读取 Skill、项目规则或 Git。
- `PreToolUse`：潜在写入前用一次 Git 状态查询和目标文件内容指纹记录轻量快照，不运行完整编码检查。
- `PostToolUse`：再次比较快照；工作区未变化时静默退出，真实变化时执行一次完整检查。
- `Stop`：本轮没有潜在写入状态时静默退出；只在 Post 缺失、失败或检查后又变化时兜底。

白名单中的单一只读 shell 命令在访问仓库前直接跳过；包含管道、重定向、命令连接或未知语义的 shell
命令按潜在写入处理。Hook 状态按仓库、会话、回合、代理和工具调用隔离，避免并发工具覆盖。相同文件在
工具调用前后字节未变化时，其历史诊断不阻断本轮；基线缺失时保持严格检查。

Hook 输出只包含压缩后的短恢复提示或本轮新阻断项，最多返回有限条诊断，不复制 `last_assistant_message`、
完整工具结果或已经反馈过的无变化内容。Hook 不能撤销已经完成的写入，也不能覆盖所有专用工具路径；未运行
或覆盖不足时按主 Skill 的写后闭环手工验证。

Windows 生命周期入口直接使用 `py -3 -B`，Unix 使用 `python3 -B`，不再经过 `cmd → Git Bash → 多次
Python` 的启动链。Python 3 是自动生命周期检查的必需运行时；缺失时主 Skill 仍可指导手工检查。

## 主动入口

### 检查当前差异

同时检查暂存区、未暂存修改和未跟踪新增文本：

```text
python "<jojo-code-guard>/scripts/check_diff.py" --repo .
```

可选参数：

- `--staged-only`：只检查暂存区。
- `--json`：输出机器可读诊断。
- `--tracked-revision REVISION`：检查提交树中的全部 tracked 普通文本，适用于 clean checkout CI。
- `--allow-initial-baseline`：明确导入无 HEAD 的老项目时，只放宽可解释的历史编码/EOL 属性。
- `--allow-migration KIND:PATH`：精确允许单一路径的 `encoding`、`bom` 或 `eol` 迁移，可重复使用。

Git Hook 可通过 JSON 字符串数组 `JOJO_CODE_GUARD_ALLOW_MIGRATIONS` 传入相同的一次性迁移许可。路径必须
精确且不支持 glob。无 HEAD 仓库默认仍严格检查首个提交；已安装的 pre-commit 只有在显式设置
`JOJO_CODE_GUARD_ALLOW_INITIAL_BASELINE=1` 时才接受一次历史基线例外，完成后应立即取消该变量。

### 诊断环境

```text
python "<jojo-code-guard>/scripts/doctor.py" --repo .
```

doctor 默认只读检查核心运行时、当前仓库规则、插件 Hook 清单、可选 pre-commit 和旧版重复加载配置。它不
联网检查更新，不安装 CMake/Ninja/gsudo 等设备工具，不写入用户级规则，也不解析客户端内部信任数据库。

仓库缺少基础配置时，先运行 `--repair` 预览；确认后追加 `--yes`。安装可选仓库私有 pre-commit 使用
`--install-hook --yes`。repair 只创建缺失文件和设置仓库 local Git 保护项，不覆盖已有配置，不修改已有
脚本、暂存区或全局 Git 设置。

doctor 会报告 0.2.x 可能留下的用户级自动加载节和手工 `SessionStart` Hook。它不自动删除用户文件中的
内容；迁移时应审阅精确来源，只删除 jojo 管理的旧节或旧 Hook，保留同文件中的其他用户规则。

## Git pre-commit

pre-commit 是提交阶段的可选机械门禁，不替代编辑前后检查。安装器拒绝覆盖第三方 `pre-commit`、链接型
hooks 目录或自定义 `core.hooksPath`。发现误报时先审阅 staged diff；只有用户明确决定后才使用
`--no-verify`，Skill 不自动绕过门禁。

## 安装与升级

Codex：

```text
codex plugin marketplace add zackxjxc/jojo-code-guard
codex plugin add jojo-code-guard@jojo-code-guard
```

Claude Code：

```text
/plugin marketplace add zackxjxc/jojo-code-guard
/plugin install jojo-code-guard@jojo-code-guard
```

升级由客户端插件管理器完成，插件自身不检查远端版本或复制安装目录。升级后新开线程，使新的 Skill 与 Hook
清单进入上下文；Hook 内容变化时按客户端提示重新审阅信任。

Codex 正式入口保留：

- `jojo-code-guard`：文件编辑和字节属性守护。
- `jojo-code-guard-doctor`：核心环境诊断。
- `jojo-code-guard-check-diff`：当前差异验收。

低频需求也可以直接用自然语言提出，例如“检查这个文件是否被整份换行重写”或“只允许迁移这个文件的
编码”。持续有效的团队规则应写入项目 `AGENTS.md`；临时例外不修改用户全局配置。
