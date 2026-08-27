# 更新日志

本文件记录 jojo-code-guard 的重要变更。

## [Unreleased]

- 改用客户端原生 Skill 与 `AGENTS.md` 发现，不再由 SessionStart 注入加载指令或要求每个回复重读规则。
- 删除 UserPromptSubmit 全量扫描；PreToolUse 只记录轻量快照，PostToolUse 仅在真实变化后完整检查，Stop 只兜底本轮写入状态。
- 生命周期入口合并为单个跨平台 Python 脚本，移除 Git Bash、Windows 批处理和多次 Python JSON 解析链。
- Hook 反馈不再复制 `last_assistant_message`，并限制模型可见诊断数量，避免相同结果重复占用上下文。
- 删除会被 Codex 迁移成重复 Skills 的 Claude commands 与 help Skill，正式入口收敛为主守护、doctor 和 check-diff。
- doctor 收敛到 Python/Git、仓库规则、生命周期 Hook、可选 pre-commit 和旧版重复配置诊断；移除联网更新、设备工具安装、全局规则写入和客户端缓存哈希。
- 删除两套高度重复的插件目录事务同步器，插件安装与升级交由客户端原生 marketplace 管理。
- 新增纯聊天零仓库访问、只读命令零检查、真实写入单次检查、Stop 不复制答案、并行工具状态隔离和未改专项规则字节保持测试。

## [0.2.13] - 2026-08-11

- 修复 Git 特殊路径、Unicode 重命名、大文件读取和 staged-only 批处理误扫，并增加精确单文件迁移许可。
- CI 改为检查实际提交范围和完整 HEAD 树，避免 clean checkout 上的空检查。
- 生命周期 Hook 覆盖 fork 会话，显式使用秒级超时，并增加不依赖 Windows PATH 的 Git Bash 启动器。
- SessionStart 改为短加载指令，避免超过上下文上限；PostToolUse/Stop 不再把会话前修改归因于本轮。
- Git hook 安装器拒绝 marker 碰撞、符号链接和任意作用域的 `core.hooksPath`，不再覆盖第三方 hook。
- doctor 增加关键资源 SHA-256 完整性验证，修复 CRLF 配置写入、CODEX_HOME、Git 失败误报、UAC 等待与工具误升级。
- Claude/Codex 同步器改为先完整构建校验再替换，自动清理旧版托管残留。

## [0.2.12] - 2026-07-28

- SessionStart 直接将通用规则注入上下文，避免 AI 依赖相对路径执行额外读取动作。

## [0.2.11] - 2026-07-28

- 通用规则明确如无特殊要求，使用中文与用户对话。

## [0.2.10] - 2026-07-28

- 主 Skill 改为自动加载同目录的通用规则文件，用户级全局文件只保留稳定的自动加载入口。
- doctor 取消全局规则整文件覆盖与合并模式，只新增、更新或去重 jojo-code-guard 自动加载节。
- 已有用户文件保持标题、其他规则、UTF-8 BOM 和换行；目标不存在时才生成普通全局标题。
- 自动加载节增加“必须严格遵守”标识，并兼容旧标题；适配包升级时清理旧的完整全局规则源文件。
- 通用规则补充工程决策、范围复盘、鲁棒性和可安全恢复污染的自动修复闭环。

## [0.2.9] - 2026-07-27

- 复杂 PowerShell 命令默认提供可直接粘贴的单行形式；过长时生成脚本并提供单行执行入口。

## [0.2.8] - 2026-07-27

- 全局规则增加范围控制与停机条件，允许必要支撑和合理补全，同时限制辅助系统取代主体目标。
- 对威胁模型、权限边界、部署形态和通用回滚等范围扩张增加用户确认门禁。
- 辅助问题连续处理超过 30 分钟或工作量明显失衡时，要求暂停并汇报风险和最小替代方案。

## [0.2.7] - 2026-07-21

- 默认 `.gitattributes` 为 `.bat/.cmd` 增加 `text eol=crlf`，同时保留其他老文件的 `* -text` 策略。
- doctor 使用 Git 最终属性诊断批处理规则覆盖，并分别报告脚本编码、BOM 和换行问题。
- doctor 修复保留已有属性内容且不 renormalize、不改脚本或暂存区；guard 与测试同步验证 CRLF 工作区字节。

## [0.2.6] - 2026-07-21

- doctor 增加远端版本检查；发现新版本时明确提示 Skill 不会自行更新，并给出 Claude/Codex 更新入口。
- 远端不可访问时只报告警告，不阻断仓库、插件和全局规则的其他诊断。

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
