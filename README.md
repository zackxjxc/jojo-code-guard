# jojo-code-guard

**防止 AI 修改代码时大面积污染旧代码，并为 C++、PowerShell 与 Git 工作流提供可靠的工程约束。**

jojo-code-guard（啾啾代码守护）是面向 Codex 和 Claude Code 的工程守护插件。它通过会话级规则、修改前后
基线检查和可选的 Git 门禁，约束 AI 从理解任务、编辑文件到验证和提交的整个过程。

AI 生成代码通常不是最难的部分，难的是让一次修改尊重已有工程：只改几行逻辑，却把整个文件的编码或换行
重写了；在 PowerShell 里丢失参数边界、误判退出码，或反复踩中 PS 5.1 与 7 的兼容差异；提交时又把用户
尚未完成的改动一并带了进去。这些问题往往不显眼，却会让代码审查和后续维护变得很痛苦。

这个项目尤其适合 C/C++、Visual Studio/MSVC、Windows 老项目和混合编码仓库。同时，文件守护、AI 通用
行为规则、Git 提交边界和验证流程并不依赖语言，也适用于 TypeScript、Rust、Swift、Python 等其他项目。

## 它提供哪些守护能力

| 能力 | 主要解决的问题 |
| --- | --- |
| 文件与 diff 守护 | 记录编码、BOM、换行和已有修改，发现整文件重写、异常 diff、Git 空白错误和权限位变化 |
| AI 通用代码规范 | 控制任务范围，避免过度设计和无关重构，优先复用现有能力，并约束命名、注释、文档与验证质量 |
| C++/MSVC 专项规则 | 保护混合编码老文件，避免顺手整理 include 或全仓格式化，并处理资源文件与旧工具链兼容例外 |
| PowerShell/批处理规则 | 处理 PS 5.1/7 编码差异、参数边界、`Start-Process`、退出码、窗口与重定向、提权、进程树和跨 shell 调用 |
| Git 与提交规范 | 提交前检查状态和 diff，不混入无关修改；按可审阅、可回退的功能单元提交，并遵循项目提交信息约定 |
| 长任务与故障诊断 | 控制构建和测试日志规模，同时保留真实退出码、最早根因、关键警告和足够的诊断证据 |
| 自动检查与环境体检 | 在会话开始、工具写入后和交付前检查；通过 `doctor`、`check-diff` 与可选 `pre-commit` Hook 补充机械门禁 |

PowerShell 规则并不是泛泛的代码风格建议，而是针对那些经常让 AI 与脚本反复“搏斗”的真实陷阱；通用代码
规范也不会要求全仓统一格式，而是优先保证修改范围合理、实现不过度、结果可验证。

## 它是怎么工作的

一次正常的编辑大致会经过四步：

1. **先看现场**：记录 Git 状态，以及目标文件的编码、BOM、换行和已有未提交修改。
2. **只改目标**：保留老文件原有字节特征，尽量让局部编辑保持为局部 diff。
3. **改完复查**：检查实际变更、Git 空白错误、权限位和意外新增文件。
4. **发现异常就停下**：能够依据基线安全恢复时做最小修复；无法确定时明确报告，不猜测原始状态。

插件的生命周期 Hook 会尽量自动完成这些检查；如果客户端没有启用或信任 Hook，主 Skill 仍会要求 AI
执行同样的修改前后检查。你也可以选择安装 Git `pre-commit` Hook，给提交阶段再加一道机械门禁。

## 它不会替你做什么

- 不会为了“统一”而批量转码、批量换行或运行 `git add --renormalize`。
- 不会把已有未提交修改当成错误并擅自恢复。
- 不会自动覆盖仓库配置、安装工具或修改用户级规则。
- 不会假装知道一个历史文件原本应该是什么编码；证据不足时宁可停下来问你。
- 不会代替代码审查、测试或备份。它守住的是文件与 diff 的边界。

需要写入配置、安装 Hook/工具或同步用户级规则时，`doctor` 会先展示影响，只有得到明确确认后才会执行。

## 安装

### Codex

```bash
codex plugin marketplace add zackxjxc/jojo-code-guard
codex plugin add jojo-code-guard@jojo-code-guard
```

### Claude Code

在 Claude Code 会话中执行：

```text
/plugin marketplace add zackxjxc/jojo-code-guard
/plugin install jojo-code-guard@jojo-code-guard
```

安装完成后重新打开会话。插件会从 GitHub 获取，不需要手动 clone，也不会把 Skill 复制进你的业务仓库。

如果客户端提示需要信任 Hook，请先审阅再确认。是否能自动运行生命周期 Hook，取决于客户端版本、功能开关
和信任状态；可以用下面的 `doctor` 检查当前安装是否完整。

## 装好以后怎么用

日常使用不需要背命令。像平时一样描述任务即可，例如：

```text
帮我修复这个解析函数，保留文件现在的编码和换行，不要动其他模块。
```

jojo-code-guard 会在会话中自动加载。第一次在一个仓库里使用时，建议先说：

```text
请使用 jojo-code-guard 的 doctor 检查当前仓库。
```

`doctor` 默认只读；只读诊断会检查字符编码、BOM、EOL/换行和当前 diff，也会检查设备、Git、仓库配置、插件完整性和远端版本。
看完报告后，再决定是否补充配置或安装可选的 Git Hook。

几个低频入口也可以直接用自然语言调用：

| 需求 | 可以这样说 |
| --- | --- |
| 日常守护 | `使用 jojo-code-guard 守护当前仓库` |
| 环境体检 | `使用 jojo-code-guard 的 doctor 检查当前环境` |
| 验收当前 diff | `使用 jojo-code-guard 的 check-diff 检查未提交修改` |
| 查看帮助 | `使用 jojo-code-guard 的 help 说明功能和安全边界` |

Codex 中也可以直接选择 `jojo-code-guard`、`jojo-code-guard-doctor`、
`jojo-code-guard-check-diff` 和 `jojo-code-guard-help`；Claude Code 对应的命令形式是
`/jojo-code-guard:doctor`、`/jojo-code-guard:check-diff` 和 `/jojo-code-guard:help`。

想确认 Skill 是否真的加载了吗？在新会话里说一句“天王盖地虎”。如果它回答
`Price tower shock river monster`，说明暗号接头成功。

## 升级

Codex 目前需要手动刷新市场快照并重新安装：

```bash
codex plugin marketplace upgrade jojo-code-guard
codex plugin add jojo-code-guard@jojo-code-guard
codex plugin list
```

Claude Code 中执行：

```text
/plugin marketplace update jojo-code-guard
/plugin install jojo-code-guard@jojo-code-guard
```

升级后请重新打开会话。已经打开的会话不会自动换成新版本；Skill 本身也不会在运行中静默更新自己。

## 常见问题

### 只能用于 C++ 项目吗？

不是。项目最初重点处理 C++ 老仓库常见的混合编码与 Windows 换行问题，但编码、BOM、EOL 和最小 diff
检查适用于任何文本项目。只有真的遇到 C++、PowerShell、批处理或 Git 操作时，才会继续加载对应专项规则。

### PowerShell 和批处理文件有什么特殊之处？

已有文件仍然保持原样。只有新建文件有两条例外需要提前知道：

- 明确由 PS 5.1 解释且含非 ASCII 字符的 `.ps1/.psm1/.psd1` 使用 UTF-8 BOM + LF。
- 新建 `.bat/.cmd` 使用 UTF-8 无 BOM + CRLF。

这样做是为了兼容各自的实际解释器，不是借机统一仓库里的历史文件。

### 安装后会修改我的仓库吗？

不会。安装只把插件交给客户端管理。日常守护以读取规则和检查 diff 为主；只有你审阅并确认 `doctor` 的
修复建议后，它才可能补充仓库配置或安装可选的 Git Hook。

### 仓库本来就有混合编码，还能用吗？

可以，这正是它想保护的场景。老文件默认保持原样，不会因为和“推荐编码”不同就被整批转换。已有问题会被
区分为既有状态；只有本轮新引入的问题才会阻断交付。

### 我确实想迁移某个文件的编码或换行呢？

直接明确目标文件和迁移要求即可。jojo-code-guard 支持对单个精确路径授权迁移，但不会把一次单文件许可
扩大成整个目录或整个仓库的批量改写。

## 进一步了解

- [完整用法与安全边界](./skills/jojo-code-guard/references/usage.md)
- [主 Skill 规则](./skills/jojo-code-guard/SKILL.md)
- [更新日志](./CHANGELOG.md)

README 有意只保留使用者最需要知道的部分。Hook 时序、全局规则同步、适配包事务安全、特殊文件类型和一次性
迁移参数等实现细节，都记录在上面的完整文档与源码中。

## 本地开发

修改后先让测试选择器给出最低验证集合：

```bash
python scripts/select_tests.py
python scripts/select_tests.py --run
```

发布、`master` 或需要完整验证时运行：

```bash
python -B -m unittest discover -s tests
```

从 Windows 发布 Hook 变更时，还要确认 Unix 可执行位没有丢失：

```bash
git add --chmod=+x hooks/session-start hooks/post-write-check
```

如果这个项目替你挡住过一次“只改三行，却整份文件变红”的事故，那它就已经完成了自己的工作。
