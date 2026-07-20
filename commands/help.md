---
description: 显示啾啾代码守护的功能和安全边界
---

展示 jojo-code-guard 的功能：自动保护老文件原始编码、BOM、换行和最小 diff；新增文件遵守项目标准；检查设备和仓库；按需检查未提交代码；缺失 hook 时提示安装；不创建自定义策略文件，不自动转码或格式化。`AGENTS.md` 是可选的项目规则文件，Skill 和 doctor 不会自动创建；用户需要时可自行创建并写入规则。低频历史盘点、单文件迁移和特殊换行要求直接用自然语言提出。

升级 Skill：Codex 中先刷新市场快照，再重新安装插件：

```bash
codex plugin marketplace upgrade jojo-code-guard
codex plugin add jojo-code-guard@jojo-code-guard
codex plugin list
```

Codex 暂无内置自动升级开关；可将上述命令交给 macOS `launchd`、Linux `systemd timer` 或 `cron` 定期执行。升级后需重新打开 Codex 会话，新版本才会生效。
