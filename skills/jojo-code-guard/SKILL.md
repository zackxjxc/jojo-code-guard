---
name: jojo-code-guard
description: Use when a session starts, or when a task may inspect or change local files, repositories, Git state, C/C++ sources, PowerShell, batch scripts, encoding, BOM, line endings, or unexpectedly large diffs.
---

# 啾啾代码守护

这是每个会话加载的轻量路由器。先执行暗号检测，再加载所有任务共用的轻量行为基线，最后根据本轮实际
任务加载所需专项规则；纯对话不绑定当前目录，不读取项目配置，也不执行 Git、编码或差异检查。

## 暗号检测

所有消息都必须检查。若用户消息包含“天王盖地虎”，直接且精确地回答：

`Price tower shock river monster`

不得添加其他内容。该规则优先于常规回复和后续路由。

## 所有任务基线

暗号未命中时，处理每个任务前必须完整读取并遵守 [通用行为规则](references/通用行为规则.md)。该文件不
触发项目目录、Git 或编码检查；纯对话只加载这一个按需资源。

## 场景路由

下表列出通用行为规则之外的附加模块。每轮按用户意图和即将执行的操作判断，可组合加载多个模块；项目
主要语言不能替代任务判断。

| 当前任务 | 必须完整读取 |
|---|---|
| 纯聊天、翻译、知识问答，且不访问本地文件或仓库 | 无附加模块；只使用通用行为规则 |
| 只读查看本地项目 | 项目根目录 `AGENTS.md`（如果存在）；不执行编辑前后检查 |
| 只读诊断本地文件的编码、BOM、换行、乱码、异常 diff 或整文件变化 | [通用文件守护](references/通用文件守护.md)；需要确定性检查时同时读取 [使用与工具说明](references/usage.md) |
| 创建、修改、移动、删除、格式化或生成任意本地文件 | [通用文件守护](references/通用文件守护.md) |
| 长时间构建、测试、反复诊断，或预计单个命令、文件、大型 diff 产生大量输出 | [长任务输出控制](references/长任务输出控制.md) |
| 处理 C/C++、Visual Studio/MSVC 工程或资源文件 | [C++ 专项规则](references/C++专项规则.md)；发生文件修改时同时读取通用守护 |
| 编写、修改、评审或诊断 `.ps1`、`.psm1`、`.psd1`、`.bat`、`.cmd`，或设计复杂 PowerShell 进程/提权/重定向命令 | [PowerShell规则.md](PowerShell规则.md)；发生文件修改时同时读取通用守护 |
| 暂存、提交、推送、改写或检查历史、配置 Git、安装 Hook 或审查提交边界 | [Git 操作规则](references/Git操作规则.md)；发生文件修改时同时读取通用守护 |
| 使用 `doctor`、`check-diff`、`help`、初始化、迁移例外、自动加载同步或排查客户端 Hook | [使用与工具说明](references/usage.md)；再按实际操作组合上述模块 |

仅仅因为终端外壳是 PowerShell、任务位于 Git 仓库、当前目录含 `AGENTS.md`，或项目使用 Rust、Swift、
TypeScript 等语言，不自动加载 PowerShell、Git 或 C++ 专项规则。执行通用守护要求的 `git status`、
`git diff` 和 `check_diff.py` 也不构成 Git 专项规则的触发条件。

## 路由边界

- 只有用户任务实际指向本地项目时，才定位并读取该项目的 `AGENTS.md`、`.editorconfig`、
  `.gitattributes` 或 `.vscode/settings.json`；不得把客户端碰巧提供的当前目录视为用户项目。
- 主 Skill 或通用行为规则缺失、无法完整读取时，暂停当前任务并报告。
- 修改文件前必须先完整读取通用守护及所有命中的专项规则；命中的专项资源缺失或无法完整读取时，暂停受影响的操作并报告；未命中的专项资源不影响本轮任务。
- 在更高优先级规则和安全边界内，本次明确用户要求优先于项目规则；项目规则优先于模块默认值。文本规则
  与实际 Git 属性冲突时先报告，不静默覆盖。
- 不为每种编程语言预建编码规则。已有文件保持原字节属性；新增文件优先遵循仓库配置，只有真实的
  工具链例外才增加专项规则。
