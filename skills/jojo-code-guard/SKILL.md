---
name: jojo-code-guard
description: Preserve encoding, BOM, line endings, file type, and reviewable diffs when inspecting or changing local text files. Use for file edits, encoding/EOL diagnosis, or suspiciously large diffs; do not use for pure chat or unrelated knowledge work.
---

# 啾啾代码守护

本 Skill 只补充 Codex 原生文件编辑能力没有机械保证的字节级约束。项目规则由客户端原生发现；不要通过
`SessionStart`、全局 `AGENTS.md` 或其他包装在每个回复前重复读取本文件。上下文压缩后，插件只发送一条
短恢复提示；它不读取或注入本文件正文。

## 核心约束

- 已有文件默认保持编辑前编码、BOM、换行类型、末尾换行、文件类型和权限位；只有用户明确授权时才迁移。
- 已有未提交修改不等于污染。不得恢复、覆盖、删除或重新格式化来源不明的用户内容。
- 修改使用最小补丁，不因局部任务批量格式化、排序、转码、统一换行或执行 `git add --renormalize`。
- 新文件优先遵循项目 `AGENTS.md`、`.editorconfig` 和 `.gitattributes`；没有项目规则时才使用保守默认值。
- 写后只阻断本轮新引入或本轮改动过的异常。工具调用前后字节未变化的历史问题不归因于本轮。

## 日常流程

任务需要创建、修改、移动、删除、格式化或生成本地文件时：

1. 完整读取 [通用文件守护](references/通用文件守护.md)，记录目标文件和已有差异的编辑前基线。
2. 按项目规则执行最小修改；不要顺手整理未授权内容。
3. Hook 已成功检查本次真实写入时复用结果；Hook 未运行、失败或覆盖范围不足时，按通用文件守护手工闭环。
4. 发现异常时只自动修复能可靠归因且不会覆盖用户内容的部分，然后复检；否则暂停受影响的交付并报告。

只读查看普通项目时读取客户端已发现的项目规则即可，不执行编码或 diff 检查。纯聊天、翻译和知识问答不
加载本 Skill，不访问当前目录，也不运行 Git。

## 按需规则

只在当前目标或操作命中时完整读取对应资源：

| 场景 | 读取 |
|---|---|
| 诊断编码、BOM、换行、乱码、整文件变化或异常 diff | [通用文件守护](references/通用文件守护.md)；需要确定性命令时再读 [使用与工具说明](references/usage.md) |
| 长时间构建、测试、大型输出或反复诊断 | [长任务输出控制](references/长任务输出控制.md) |
| C/C++、Visual Studio/MSVC 或资源文件 | [C++ 专项规则](references/C++专项规则.md) |
| `.ps1/.psm1/.psd1/.bat/.cmd` 或复杂 PowerShell 进程调用 | [PowerShell 规则](PowerShell规则.md) |
| 暂存、提交、推送、改写历史、Git 配置或安装 Git Hook | [Git 操作规则](references/Git操作规则.md) |
| `doctor`、`check-diff`、迁移许可或生命周期 Hook 排查 | [使用与工具说明](references/usage.md) |

终端碰巧使用 PowerShell、目录碰巧属于 Git 仓库或项目使用某种语言，都不自动加载无关专项规则。主 Skill
或当前命中的必要资源无法完整读取时，只暂停受影响的文件操作并报告；不把加载问题扩大到无关对话。
