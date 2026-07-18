---
name: jojo-code-guard-help
description: 显示 jojo-code-guard 的功能、入口和安全边界，不修改当前仓库。
---

# 啾啾代码守护：使用帮助

说明主 Skill 的自动行为和三个低频入口：

- 主入口 `jojo-code-guard`：日常保护旧文件编码、BOM、换行和最小 diff。
- `jojo-code-guard-doctor`：诊断设备、Git 和仓库配置，默认只读。
- `jojo-code-guard-check-diff`：检查未提交修改，默认只读。
- 当前入口：显示本说明，不修改文件、不安装工具、不提交代码。

详细命令参数和客户端调用方式见主 Skill 的 `references/usage.md`。
