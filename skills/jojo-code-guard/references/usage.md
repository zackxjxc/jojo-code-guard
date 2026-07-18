# 啾啾代码守护用法

Skill 自动加载后，日常修改无需输入命令。它会遵守项目根目录 `AGENTS.md`、`.editorconfig`、`.gitattributes` 和（如果存在）`.vscode/settings.json`，保护旧文件原始编码、BOM 和换行，禁止无关格式化和大面积 diff。

如果 Git 的 `core.autocrlf` 或 `core.eol` 会自动转换工作区，检查会先告警，暂存检查会阻止提交；先在仓库 local 配置中关闭转换并重新确认 diff。Git 索引无法保存历史工作区的原始换行，工具不会猜测或批量修复。

新增 `.ps1` 默认使用 UTF-8 无 BOM + LF，适用于 PowerShell 7 和 Unix；明确使用 Windows PowerShell 5.1 且包含中文时，应使用 UTF-8 BOM，并把例外记录在根目录 `AGENTS.md`。`.bat/.cmd` 使用 UTF-8 无 BOM + CRLF，并用 `-text diff` 防止 Git 改写索引字节。已有脚本的 BOM/EOL 不会被自动迁移。

主动操作只有三项：

- `doctor`：检查设备、Git 环境和当前仓库，缺少 hook 或配置时提示初始化；默认只读。
- `check-diff`：按需检查未提交修改的范围和 Git 空白错误。
- `help`：显示本说明和安全边界。

Codex 中可说“使用 `$jojo-code-guard` 执行 doctor”；Claude Code 中可使用 `/jojo-code-guard:doctor`（其他入口同理）。客户端不支持命令时直接使用自然语言即可。也可以直接提出低频需求，例如“检查历史乱码”“只修复这个文件的换行”。涉及全局配置、批量转码、批量换行或安装软件时，必须先展示影响并确认。

Codex 直接 Skill 和 Codex 插件包是两种安装形态：前者放入 `$CODEX_HOME/skills` 即可被原生发现，后者还需要按 Codex 的 marketplace/plugin 流程注册；不要把未注册的插件目录误认为已加载。

持续有效的特殊规则写入仓库根目录 `AGENTS.md`；本次临时例外不应改变全局配置或仓库规则。Hook 发现误报时先审阅 staged diff，确认后再由用户明确选择 `--no-verify`，Skill 不会自动绕过门禁。

`.vscode/settings.json` 只是可选的编辑器提示，不是必须提交到业务仓库的标准文件。Skill 会检查其中的编码、换行、自动编码检测、保存时格式化/代码操作、尾随空白和末尾换行设置，并报告它是否被 `.gitignore` 忽略或已纳入 Git；缺失时不会自动创建或覆盖。发布仓库自身会跟踪这个文件，业务仓库是否跟踪由团队决定。

业务仓库推荐使用以下 `.gitignore` 规则，忽略其他 VS Code 私有文件但放行项目级 `settings.json`：

```gitignore
/.vscode/*
!/.vscode/settings.json
```

测试 Skill 是否加载：用户说“天王盖地虎”时，必须回答 `Price tower shock river monster`。
