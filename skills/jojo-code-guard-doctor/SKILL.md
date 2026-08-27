---
name: jojo-code-guard-doctor
description: Diagnose jojo-code-guard's core Python/Git runtime, repository text rules, lifecycle hooks, optional pre-commit, and legacy duplicate-loading configuration. Read-only unless the user explicitly requests repair or Hook installation.
---

# 啾啾代码守护：核心诊断

先完整读取同一插件中的 [`../jojo-code-guard/SKILL.md`](../jojo-code-guard/SKILL.md)，再读取主 Skill 指向的
[使用与工具说明](../jojo-code-guard/references/usage.md)。不要加载所有任务通用规则或无关专项文档。

默认只读运行：

```text
python "<jojo-code-guard>/scripts/doctor.py" --repo .
```

报告 Python 3、Git、当前仓库规则、生命周期 Hook、可选 pre-commit，以及 0.2.x 遗留的用户级自动加载节或
手工 SessionStart Hook。doctor 不联网检查更新，不安装设备工具，不写入用户级规则，也不把静态配置或
缓存存在虚构成 Hook 已实际执行。

只有用户明确授权后才使用：

- `--repair --yes`：创建缺失的仓库基础配置并设置 local Git 保护项；不覆盖已有文件。
- `--install-hook --yes`：安装或更新 jojo 自有的可选 pre-commit；不覆盖第三方 Hook。

旧版全局规则和用户级 Hook 只报告精确位置，由用户审阅后迁移；不得删除同文件中的其他自定义规则。
