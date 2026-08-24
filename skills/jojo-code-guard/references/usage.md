# 啾啾代码守护用法

Skill 自动加载后，每个任务先执行轻量路由并读取通用行为规则；纯对话不读取当前目录或项目配置。任务实际
访问本地项目时，才按需读取根目录 `AGENTS.md`。只读诊断编码、BOM、EOL/换行和当前 diff，或发生文件修改时，再加载通用文件守护，并遵守
`.editorconfig`、`.gitattributes` 和可选的 `.vscode/settings.json`，保护旧文件原始编码、BOM 和换行。
C++、PowerShell/批处理及 Git 操作规则只在对应目标或操作出现时加载。`AGENTS.md` 是可选文件，不会自动创建。

如果 Git 的 `core.autocrlf` 或 `core.eol` 会自动转换工作区，检查会先告警，暂存检查会阻止提交；先在仓库 local 配置中关闭转换并重新确认 diff。Git 索引无法保存历史工作区的原始换行，工具不会猜测或批量修复。

已有 PowerShell 脚本始终保留编辑前编码、BOM 和换行。新建 `.ps1/.psm1/.psd1` 明确面向 Windows PowerShell 5.1（PS 5.1）且含非 ASCII 字符时使用 UTF-8 BOM + LF；其他新脚本在仓库规则或数据协议没有另行要求时使用 UTF-8 无 BOM + LF，Unix shebang 脚本禁止 BOM。Visual Studio/MSVC 的 `.rc/.rc2` 新文件使用 UTF-8 BOM + LF，这是为旧资源工具链保留的明确例外。`.bat/.cmd` 必须使用 UTF-8 无 BOM + CRLF，并用 `*.bat text eol=crlf` 和 `*.cmd text eol=crlf` 保证 Git 检出结果。这两条规则只覆盖批处理文件的全局 `* -text`，其他历史文件仍保留原始字节。新建 `.gitattributes` 时默认加入这些规则；已有仓库补充规则时，后续 checkout、reset 或重新暂存可能把现有批处理转换为 CRLF。Skill 不自动执行 `git add --renormalize`，不批量改写已有脚本，也不修改暂存区。

主动操作包括以下入口：

- `doctor`：检查设备、Git、当前仓库、全局规则和远端发布版本；默认只读。仓库修复、Hook/工具安装和
  全局规则同步是三类独立写入，分别展示影响并取得确认；其中同步只增改两个用户级文件中的自动加载节。
  工具安装直接调用已解析的包管理器绝对路径，不生成临时提权脚本；安装器需要 UAC 时由使用者确认。
- `check-diff`：按需检查未提交修改的范围、Git 空白错误以及意外权限位/文件类型变化。
- `help`：显示本说明和安全边界。

## 用户级自动加载节同步

- `doctor.py --sync-global-rules` 先只读检查并预览拟议差异；只有用户审阅后显式追加 `--yes` 才写入。
- doctor 只管理 `## jojo-code-guard 自动加载（必须严格遵守）` 一节，不覆盖整个文件，也不修改节外标题、
  正文或自定义规则。兼容旧标题 `## jojo-code-guard 自动加载`，更新时改为当前标题。
- 目标不存在时创建普通 `# 全局规则` 标题和受管节；已有目标缺少该节时只追加。同一文件存在多个新旧节时，
  保留最靠前的位置并更新为当前内容，删除其余重复节，其他章节保持原样。
- 同步只接受严格 UTF-8 文本，并保留已有 UTF-8 BOM 和原换行类型。使用混合换行、无法严格解码、目标
  或父路径为符号链接/junction、目标为硬链接，或未闭合 fenced code block/HTML 注释时，拒绝写入并报告。
  文件开头的 YAML front matter、行首原始 HTML 块、链接引用定义与紧邻 Setext 边界含糊的写法，以及
  列表或引用块之后归属含糊的缩进 H1/H2 或 Setext 标题也会拒写，因为其中形似 Markdown 标题的文本不能
  安全地作为顶层节边界。
- 两个目标在任何写入前都会完成快照预检，并在各目标卷真实探测发布与回滚共用的 no-clobber rename；每个
  变更在同目录暂存并完整复核后，用系统原子交换/替换原语捕获实际被置换版本。同步同时保真并复核 mode、
  Windows DACL/文件属性/ADS、POSIX uid/gid/xattr，以及 macOS ACL/文件 flags；平台缺少安全原语或元数据
  无法保真时失败关闭。备份保留到两个目标均完成写入后复核；写入、中断、复核或回滚时若检测到并发编辑，
  则恢复或隔离实际外部版本并报告位置，不用旧快照覆盖用户修改。
- Windows 上，doctor 的私有隔离清理和两套插件适配器用于暂存、回滚或隔离的私有目录，都依赖 Python
  对 `0700` 目录创建 owner-only DACL 的安全补丁：最低版本分别为 Python 3.9.20、3.10.15、3.11.10 和
  3.12.4；Python 3.13 及以上可直接使用。更旧补丁级会在创建私有目录前失败关闭，不把待恢复内容放入
  可能被其他本机账户读取的目录。

插件在已知编辑和 shell 工具完成后会由 `PostToolUse` 自动运行 `post-write-check`，将诊断反馈给 AI；
发现 `BLOCKED` 时反馈阻断诊断并要求 AI 继续修复，但不能撤销已经完成的写入。`Stop` 在回合结束前检查
外部脚本或未识别工具的写入，并通过 `stop_hook_active` 避免重复阻断。Codex 使用原生 Skill Discovery；
当前实测的 Codex 0.142.3 会发现插件的 `hooks/hooks.json`，执行仍取决于 Hook 功能和信任状态。Hook 从
业务仓库的当前工作目录启动；Codex 注入 `PLUGIN_ROOT` 和兼容变量
`CLAUDE_PLUGIN_ROOT`，Claude 使用后者，脚本据此定位插件资源。未加载或未信任时回退到主 Skill 指导的
检查路径，由客户端/模型能力决定。
`hooks.json` 的 `timeout` 单位是秒：SessionStart 显式为 10 秒，PostToolUse/Stop 为 60 秒。若目标是
3000 毫秒，应写 `3`；写 `3000` 会得到 3000 秒。
项目初始化时如需让 Git 在提交阶段自动拦截污染，可在审阅 doctor 报告后运行
`doctor.py --install-hook --yes` 安装仓库私有 `pre-commit`；这属于初始化后的可选机械门禁，不是日常 AI 命令。
如果仓库还缺少 `.editorconfig`、`.gitattributes` 或安全的 Git local 配置，应改用
`doctor.py --repair --install-hook --yes` 一次补齐。
Codex 插件安装或升级后重新打开会话，使主 Skill Discovery 生效；需要自动生命周期检查时，还要确认客户端
已发现并信任 `hooks/hooks.json` 中的 `SessionStart`、`PostToolUse` 和 `Stop`。Hook 内容变化的升级可能需要
重新信任，日常会话不需要重复操作。
Skill 不会自行更新；doctor 发现远端新版本时会给出 Claude/Codex 的更新命令，网络失败只报告警告。
doctor 还会用内置 SHA-256 清单校验两端客户端 manifest、Hook、规则与脚本、公开 Skill 的
`agents/openai.yaml`，以及 Claude commands。`doctor.py` 作为校验器自身只能检查存在性、普通文件类型和路径
未越出插件目录，不声称能够用自身内置值验证自身内容。同步包校验和 doctor 还会拒绝清单外、会被客户端
自动发现的 command、Skill、agent、MCP/LSP 或其他运行入口，避免附加入口绕过受管资源检查。
Claude/Codex 适配器同步脚本要求私有 staging 在构建前为空；复制后必须精确匹配当前发布源，并在写入
ownership marker 前逐文件 fsync、再自底向上刷新目录。发布、回滚、崩溃恢复或清理前后都会复核根目录
身份、相对路径、文件类型和内容哈希。无 marker 的目录只有完整匹配当前发布源，或匹配对应 manifest 版本
登记的已知历史 clean-tree 摘要时才允许迁移；未知文件、伪造 journal 和同步期间的内部文件变化都会失败
关闭并保留可恢复对象。构建失败且尚无 marker 的 partial staging 不做递归清理，而是保留并报告精确路径。
Windows 上的 no-clobber rename 还会请求 write-through 并刷新相关父目录。

无 HEAD 的新仓库默认严格检查首个提交；只有明确导入老项目历史基线时，才使用
`check_diff.py --allow-initial-baseline`，并应记录这次例外的风险。若要让已安装的本地 Hook 同步接受一次例外，
需显式设置 `JOJO_CODE_GUARD_ALLOW_INITIAL_BASELINE=1`，完成这次导入后应立即取消该环境变量。
明确授权的单文件迁移可重复使用 `--allow-migration encoding:path/to/file`、`bom:path/to/file` 或
`eol:path/to/file`；路径必须精确且不支持 glob。Git Hook 可通过 JSON 数组
`JOJO_CODE_GUARD_ALLOW_MIGRATIONS` 传入同样的一次性许可。

Codex 中可说“使用 `$jojo-code-guard` 执行 doctor”；Claude Code 中可使用 `/jojo-code-guard:doctor`（其他入口同理）。客户端不支持命令时直接使用自然语言即可。也可以直接提出低频需求，例如“检查历史乱码”“只修复这个文件的换行”。涉及全局配置、批量转码、批量换行或安装软件时，必须先展示影响并确认。

Codex 插件还提供以下独立入口，便于在输入框中按名称选择：

- `jojo-code-guard`：日常自动守护。
- `jojo-code-guard-doctor`：检查环境。
- `jojo-code-guard-check-diff`：检查差异。
- `jojo-code-guard-help`：查看帮助。

Codex 直接 Skill 和 Codex 插件包是两种安装形态：前者放入 `$CODEX_HOME/skills` 即可被原生发现，后者还需要按 Codex 的 marketplace/plugin 流程注册；不要把未注册的插件目录误认为已加载。

持续有效的特殊规则可由用户自行写入仓库根目录 `AGENTS.md`；本次临时例外不应改变全局配置或仓库规则。Hook 发现误报时先审阅 staged diff，确认后再由用户明确选择 `--no-verify`，Skill 不会自动绕过门禁。

`.vscode/settings.json` 只是可选的编辑器提示，不是必须提交到业务仓库的标准文件。Skill 会检查其中的编码、换行、自动编码检测、保存时格式化/代码操作、尾随空白和末尾换行设置，并报告它是否被 `.gitignore` 忽略或已纳入 Git；缺失时不会自动创建或覆盖。发布仓库自身会跟踪这个文件，业务仓库是否跟踪由团队决定。

业务仓库推荐使用以下 `.gitignore` 规则，忽略其他 VS Code 私有文件但放行项目级 `settings.json`：

```gitignore
/.vscode/*
!/.vscode/settings.json
```

测试 Skill 是否加载：用户说“天王盖地虎”时，必须回答 `Price tower shock river monster`。
