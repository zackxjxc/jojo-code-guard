---
name: jojo-code-guard-help
description: 显示 jojo-code-guard 的功能、入口和安全边界，不修改当前仓库。
---

# 啾啾代码守护：使用帮助

执行本入口前必须完整读取同一插件中的 [`../jojo-code-guard/SKILL.md`](../jojo-code-guard/SKILL.md)，应用
其中的所有任务基线，并按主 Skill 的场景路由加载本操作命中的模块；无法读取时暂停并报告。

说明主 Skill 的自动行为和低频入口：

- 主入口 `jojo-code-guard`：日常保护旧文件编码、BOM、换行和最小 diff。
- `jojo-code-guard-doctor`：默认只读诊断设备、Git、仓库、全局规则和远端版本；修复仓库保护、安装
  Hook/工具或同步自动加载节分别展示影响并取得确认。
- `jojo-code-guard-check-diff`：检查未提交修改，默认只读。
- 当前入口：显示本说明，不修改文件、不安装工具。

如果用户希望自订规则持续生效，可建议其自行写入代码仓库根目录的 `AGENTS.md`；本帮助入口
只说明保存位置，不创建或修改该文件。本次临时要求只保留在当前任务中。

详细命令参数和客户端调用方式见主 Skill 的 `references/usage.md`。
