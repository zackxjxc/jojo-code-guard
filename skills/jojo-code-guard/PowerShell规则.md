# PowerShell 脚本编写规则（AI 专用 · 覆盖 PS 5.1 + 7.x）

涉及 PowerShell、`.ps1`、`.bat` 或 `.cmd` 的任务，AI 必须先读取本文档，再进行分析、编码和验证，并严格遵守本文档的完整规则与检查清单。

> **适用范围**: Windows 平台 ONLY。macOS / Linux 不使用 PowerShell。
> **优先版本**: PowerShell 7 (Core)。如目标机器仅安装 PS 5.1，建议提醒用户安装 PS 7：
> ```powershell
> winget install --id Microsoft.PowerShell --source winget
> ```
>
> **冲突声明**: 本文档经实测验证 (Win11 + PS 5.1 / PS 7.6.3)，但不保证 100% 覆盖所有边界情况。
> **若实际运行结果与本文档冲突，应以实测为准，并在回复中明确指出哪条规则可能错误。**

注意：新建脚本的开头需要添加信息输出，显示脚本来自ai编写

---

## 0. 前置决策树（每次生成 .ps1 前必须执行）

```
1. 确认运行平台 → 非 Windows 则禁止生成 .ps1，改用对应 shell 脚本
2. 确认 PS 版本   → pwsh -Command '$PSVersionTable.PSVersion.Major'
3. 确认 PS Edition → pwsh -Command '$PSVersionTable.PSEdition'
   - "Desktop" = PS 5.1 (Windows 内置)
   - "Core"    = PS 6+ / 7 (跨平台)
4. 按版本查下表，跳过不适用的规则
```

| 检查项 | 命令 |
|--------|------|
| 操作系统 | `$env:OS` (仅 Windows 有此变量) |
| PS 主版本 | `$PSVersionTable.PSVersion.Major` |
| PS Edition | `$PSVersionTable.PSEdition` |

---

## 1. 文件编码

| 场景 | PS 5.1 (Desktop) | PS 7+ (Core) |
|------|------------------|--------------|
| `.ps1` 含中文 | **必须 UTF-8 with BOM** | UTF-8 无 BOM 即可 |
| `.ps1` 纯英文 | 建议 UTF-8 with BOM | UTF-8 无 BOM 即可 |
| `.bat` / `.cmd` | **UTF-8 without BOM** (且 CRLF) | 同左 |
| Unix shebang `.ps1` | 不适用 | **禁止 BOM** (内核无法解析) |

- 新建 `.ps1` 默认使用 UTF-8 无 BOM + LF；明确由 Windows PowerShell 5.1 执行且含中文时使用 UTF-8 BOM。
- 已有文件保持原始编码、BOM 和换行符；除非用户明确授权，不进行批量编码或换行迁移。

### PS 5.1 写入 .ps1 后必须补 BOM

```bash
# 检查 BOM
head -c 3 script.ps1 | xxd          # 期望: efbb bf

# 补 BOM
printf '\xef\xbb\xbf' | cat - script.ps1 > tmp.ps1 && mv tmp.ps1 script.ps1
```

### PS 7 无需此操作
PS 7 默认 UTF-8，中文注释/字符串无 BOM 可正常解析。但如有 BOM 也不报错 (Windows)。

---

## 2. 始终显式指定编码

虽然 PS 7 默认已是 UTF-8，但为兼容 PS 5.1，**生成代码时始终显式指定**：

```powershell
Get-Content -Encoding UTF8
Set-Content -Encoding UTF8
Out-File   -Encoding UTF8      # PS5.1 默认 UTF-16LE! PS7 默认 UTF-8
```

| Cmdlet | PS 5.1 默认 | PS 7 默认 |
|--------|-----------|----------|
| `Get-Content` | ANSI | UTF-8 |
| `Out-File` | UTF-16 LE | UTF-8 |
| `Set-Content` | ANSI | UTF-8 |
| `Add-Content` | ANSI | UTF-8 |

---

## 3. 禁止使用 `$Args` 作为变量/参数名

`$Args` 是自动变量，表示未声明参数数组。即使显式 `param([string]$Args)`：
- 单参数时: `$Args` = 空字符串 (参数丢失)
- 多参数时: `$Args` = 最后一个参数 (前面全部丢失)
- **不会产生任何错误**

```powershell
# 错误
param([string]$Args)

# 正确
param([string]$ExeArgs)   # 或 $CmdArgs / $ProcArgs
```

**此陷阱跨 PS 版本、跨平台均存在。**

---

## 4. Start-Process `-ArgumentList` 参数传递

### 字符串 (安全)
```powershell
Start-Process -FilePath $exe -ArgumentList '"path with spaces" arg2'
# → 正确传递 2 个参数: "path with spaces", "arg2"
```

### 数组 (陷阱 — 元素边界静默丢失)
```powershell
# 禁止: 数组元素 "a b" 被拆分
Start-Process -FilePath $exe -ArgumentList @("a b", "c", "d e")
# 实际传参: a, b, c, d, e (5个参数!) — 元素边界完全丢失
```

### 推荐替代方案
```powershell
# 方案 1: & 运算符 (简单参数, 正确保持边界)
& $exe "a b" "c" "d e"

# 方案 2: 临时批处理文件 (需要窗口控制时)
"@echo off`r`n`"$exe`" $ExeArgs >`"$out`" 2>`"$err`"" | Out-File $bat -Encoding ASCII
Start-Process -FilePath $bat -WindowStyle Hidden -Wait -PassThru
```

**此陷阱跨 PS 版本、跨平台均存在。PS 7 行为与 PS 5.1 完全一致。**

---

## 5. Start-Process 默认异步

`Start-Process` 不会等待程序结束。需要同步时必须：

```powershell
# 方式 1
Start-Process $exe -Wait

# 方式 2 (推荐 — 可获取退出码)
$p = Start-Process $exe -Wait -PassThru
$p.ExitCode
```

**跨 PS 版本、跨平台均一致。**

---

## 6. `$LASTEXITCODE` 与 Start-Process

`Start-Process` **不会更新** `$LASTEXITCODE`：

```powershell
# 错误
Start-Process $exe -Wait
if ($LASTEXITCODE -ne 0) { ... }   # LASTEXITCODE 并未被更新!

# 正确
$p = Start-Process $exe -Wait -PassThru
if ($p.ExitCode -ne 0) { ... }
```

---

## 7. Start-Process 窗口控制参数冲突

此规则在 PS 5.1 和 PS 7 上行为**不同**，需分情况处理：

### 冲突矩阵

| 参数组合 | PS 5.1 | PS 7 | 说明 |
|---------|--------|------|------|
| `-RedirectStandardOutput` + `-WindowStyle` | **静默忽略** WindowStyle | **静默忽略** WindowStyle (不报错) | UseShellExecute 被强置 false |
| `-RedirectStandardError` + `-WindowStyle` | **静默忽略** WindowStyle | **静默忽略** WindowStyle (不报错) | 同上 |
| `-NoNewWindow` + `-WindowStyle` | **直接报错** | **直接报错** | `Cannot specify both -NoNewWindow and -WindowStyle` |
| `-RedirectStandardOutput` + `-NoNewWindow` | 正常 | 正常 | 可同时使用 |

### 结论
无论 PS 5.1 还是 PS 7，只要 `UseShellExecute = false`，`-WindowStyle` 就一定不生效。
**解决方案**: 在批处理文件内部做重定向，PS 侧只使用 `-WindowStyle`。

```powershell
# 需要 隐藏窗口 + 捕获输出 时的正确做法:
$batContent = "@echo off`r`n`"$exe`" $ExeArgs >`"$outFile`" 2>&1"
$batContent | Out-File -FilePath $batFile -Encoding ASCII
Start-Process -FilePath $batFile -WindowStyle Hidden -Wait -PassThru
```

### ⚠️ 已知文档冲突 (实测修正)
旧版文档称 PS 7 下 `-RedirectStandardOutput + -WindowStyle` "改为直接报错"——实测 **不报错**，与 PS 5.1 同为静默忽略。
如遇相反行为，可能因 PS 7 子版本差异，请以实际运行结果为准。

---

## 8. Stop-Process 行为 (Windows vs Unix)

| 平台 | PS 版本 | Stop-Process 调用的底层 API | 效果 |
|------|---------|--------------------------|------|
| Windows | 5.1 / 7 | `TerminateProcess` | 硬杀: 信号处理器不触发, atexit 不执行, 析构不调用, 缓冲区不刷新 |
| Unix | 7 | `SIGTERM` | 可优雅退出: 信号处理器触发, atexit 执行 |

### Windows 上的后果
- C++ 析构函数不执行
- Python atexit / C# finally 不触发
- stdout/stderr 缓冲区不刷新 (日志可能为空)
- 依赖析构清理的资源 (路由/DNS/注册表) 不会自动恢复

### 正确做法
```powershell
# 不依赖程序自动清理，脚本额外验证
Stop-Process -Id $pid -Force

# 验证清理结果 (不要依赖日志)
Get-NetRoute          # 确认路由已恢复
Get-NetIPAddress      # 确认 IP 已恢复
```

---

## 9. 不要依赖日志验证

由于 Stop-Process 可能导致日志丢失，验证程序行为应使用**外部状态查询**：
- `Get-NetRoute`、`Get-NetIPAddress`、`Get-NetTCPConnection`
- 注册表查询
- 网络接口状态

---

## 10. 避免复杂 `cmd /c` 调用

`cmd /c` 引号解析规则复杂，含空格路径极易出错。

```powershell
# 不推荐
cmd /c "C:\path with spaces\app.exe" arg1 arg2

# 推荐: 直接调用 (路径无空格)
& $exe arg1 arg2

# 或: 使用批处理文件 (路径含空格或参数复杂)
```

**仅 Windows 适用。Unix 无 cmd.exe。**

---

## 11. 临时批处理文件规范

```powershell
# ASCII 编码 + CRLF 换行
$batContent = "@echo off`r`n`"$exe`" $ExeArgs"
$batContent | Out-File -FilePath $batFile -Encoding ASCII
```

**仅 Windows 适用。**

---

## 12. 进程树注意事项

通过批处理文件启动进程时:
- 进程树: `cmd.exe (bat) → target.exe`
- `Start-Process -PassThru` 返回 **cmd.exe** 的进程对象，**不是 target.exe**
- 停止时需清理整个进程树

```powershell
# 递归终止进程树
Get-Process -Id $p.Id -IncludeUserName | Stop-Process -Force
# 或
taskkill /PID $p.Id /T /F
```

**仅 Windows + cmd.exe 场景适用。**

---

## 13. 网络 cmdlet 可用性

### 读取类 (无需管理员，PS 5.1 / 7 均可用)
```
Get-NetAdapter    Get-NetRoute       Get-NetIPAddress
Get-NetUDPEndpoint  Get-NetTCPConnection  Get-DnsClientServerAddress
```

### 写入类 (需要管理员，可用性因模块而异)
| Cmdlet | 状态 |
|--------|------|
| `Remove-NetRoute` | ✅ 可用 |
| `New-NetRoute` | ✅ 可用 |
| `Disable-NetAdapter` | ✅ 可用 |
| `Enable-NetAdapter` | ✅ 可用 |
| `Remove-NetAdapter` | ❌ **不存在** |
| `New-NetAdapter` | ❌ **不存在** |

### Remove-NetAdapter 不存在时的替代方案
```powershell
# 方案 A: netsh
netsh interface set interface name="xxx" admin=disable

# 方案 B: try/catch 容错
try {
    Remove-NetAdapter -Name $name -Confirm:$false -ErrorAction Stop
} catch [System.Management.Automation.CommandNotFoundException] {
    Write-Host "Remove-NetAdapter not available, skipping"
}
```

**仅 Windows 适用。**

---

## 14. 环境变量语法

| 上下文 | 语法 | 示例 |
|--------|------|------|
| PowerShell | `$env:VARNAME` | `$env:TEMP`、`$env:PATH` |
| CMD / 批处理 | `%VARNAME%` | `%TEMP%`、`%PATH%` |

**不要混用。**

### Windows PS 7 特供: 同时存在 Windows + Unix 变量

| 变量 | PS 5.1 | PS 7 (Windows) | PS 7 (Unix) |
|------|--------|---------------|-------------|
| `$env:USERPROFILE` | ✅ | ✅ | ❌ |
| `$env:HOME` | ❌ | ✅ (映射到 USERPROFILE) | ✅ |
| `$env:TEMP` | ✅ | ✅ | ❌ |
| `$env:TMPDIR` | ❌ | ❌ (空) | ✅ |

### PS 7 跨平台检测变量
```powershell
$IsWindows    # True on Windows, False elsewhere
$IsLinux      # True on Linux, False elsewhere
$IsMacOS      # True on macOS, False elsewhere
```

---

## 15. 路径使用 Join-Path

```powershell
# 错误
$path = $dir + "\file.txt"     # 分隔符问题

# 正确
$path = Join-Path $dir "file.txt"   # 自动使用系统分隔符
```

**跨平台一致。**

---

## 16. 禁止使用 Invoke-Expression

```powershell
# 禁止: 安全风险 / 引号解析陷阱 / 命令注入
Invoke-Expression "$exe $ExeArgs"

# 正确: 使用调用运算符
& $exe @args
```

---

## 17. 优先使用对象接口, 不解析文本

```powershell
# 不推荐 (依赖文本格式/系统语言/轻易变动)
ipconfig | findstr "IPv4"
tasklist | findstr "app"

# 推荐 (对象接口稳定)
Get-NetIPAddress -AddressFamily IPv4
Get-Process -Name "app*"
```

### Windows 特有命令 → PS 对象接口映射
| 传统命令 | PS 对象接口 |
|---------|-----------|
| `ipconfig` | `Get-NetIPAddress` |
| `tasklist` | `Get-Process` |
| `netstat` | `Get-NetTCPConnection` |
| `route print` | `Get-NetRoute` |

**Unix 无 `ipconfig`/`tasklist`/`netstat`，用对应 Unix 命令 (`ip`/`ps`/`ss`)。**

---

## 18. BOM 检查规则 (按版本区分)

| PS 版本 | 要求 | 验证命令 |
|---------|------|---------|
| 5.1 | **必须有 BOM** (`efbb bf`) | `head -c 3 script.ps1 \| xxd` |
| 7 (Windows) | 无 BOM 即可 (有也不报错) | 同上 |
| 7 (Unix) | **禁止有 BOM** (shebang 失效) | 同上 |

### PS 7 下无需执行 PS 5.1 的 BOM 补全流程
PS 7 默认 UTF-8，Write/Edit 工具写入后不需要补 BOM (Windows 上)。
Unix 上反而要确认无 BOM (`head -c 3 script.ps1 | xxd` 首字节应是 `23` 即 `#`)。

---

## 19. Start-Process -ArgumentList 禁止传入数组

与规则 #4 相同，此处单独强调：

```powershell
# 禁止
Start-Process $exe -ArgumentList @("a b", "c")  # 数组边界丢失

# 推荐
& $exe "a b" "c"                                  # 使用调用运算符
```

---

## 20. `2>&1` 重定向的 ErrorRecord 行为

stderr 被重定向到 stdout 后，**每行 stderr 被包裹为 ErrorRecord 对象**：

```powershell
$output = & $exe 2>&1

# 遍历时注意类型:
foreach ($line in $output) {
    if ($line -is [System.Management.Automation.ErrorRecord]) {
        # stderr 行
    } else {
        # stdout 行 (String)
    }
}

# 需要纯文本时统一转换为字符串:
$textLines = $output | ForEach-Object { "$_" }
```

**跨 PS 版本一致。**

---

## 21. `-NoNewWindow` 行为

| 场景 | Windows | Unix |
|------|---------|------|
| 可用性 | ✅ (PS 5.1 / 7) | ❌ 不支持 |
| + `-RedirectStandardOutput` | ✅ 可同时使用 | 不适用 |
| + `-WindowStyle` | ❌ 直接报错 | 不适用 |

```powershell
# 正确用法: NoNewWindow + Redirect
$p = Start-Process $exe -NoNewWindow -Wait -RedirectStandardOutput $outFile -PassThru

# 报错: NoNewWindow + WindowStyle
Start-Process $exe -NoNewWindow -WindowStyle Hidden   # ERROR!
```

---

## 22. bash → PowerShell 变量转义

从 Git Bash / WSL / Msys 调用 `pwsh -Command "..."` 时，**bash 先解析 `$` 变量**：

```bash
# 错误: bash 把 $_.Name 展开为空
pwsh -Command "Get-Process | ForEach-Object { $_.Name }"

# 正确: 单引号保护
pwsh -Command 'Get-Process | ForEach-Object { $_.Name }'

# 正确: 反斜杠转义
pwsh -Command "Get-Process | ForEach-Object { \$_.Name }"

# 最佳: 用 -File 传脚本, 避免 -Command
pwsh -File ./script.ps1
```

**推荐**: 复杂调用始终使用 `-File` 而非 `-Command`。gsudo 同样适用此规则。

---

## 23. Start-Process -Verb RunAs 改变工作目录

提权后新进程的 CWD 变为 `C:\Windows\System32` (Windows UAC 行为，与 PS 版本无关):

```powershell
# 错误: 相对路径失效
Start-Process -FilePath 'build\debug\app.exe' -Verb RunAs

# 正确: 绝对路径 + -WorkingDirectory
$exe = Join-Path $PSScriptRoot 'build\debug\app.exe'
Start-Process -FilePath $exe -WorkingDirectory $PSScriptRoot -Verb RunAs
```

### gsudo 替代 (推荐)

gsudo 保持当前工作目录，且支持 stdin/stdout 重定向：
```bash
gsudo pwsh -File ./script.ps1
```

**仅 Windows 适用。**

---

## 24. ErrorActionPreference 选择

```powershell
# 普通脚本: 默认 Continue
$ErrorActionPreference = "Continue"

# 测试/清理脚本: Continue + 单点 try/catch
$ErrorActionPreference = "Continue"
try {
    Remove-NetAdapter -Name $name -Confirm:$false -ErrorAction Stop
} catch {
    Write-Host "cleanup step failed, continuing: $_"
}

# 严格模式: Stop (谨慎使用 — 单个 cmdlet 失败即中断整脚本)
$ErrorActionPreference = "Stop"
```

### PS 7 额外选项
```powershell
# 控制 $ErrorActionPreference 是否影响原生命令 (PS 7+):
$PSNativeCommandUseErrorActionPreference = $true   # 默认 $false
```

---

## 25. PS 7 新增特性速查

| 特性 | 说明 |
|------|------|
| `$IsWindows` / `$IsLinux` / `$IsMacOS` | 跨平台检测布尔变量 |
| `$PSStyle.OutputRendering` | 控制 ANSI 转义输出 (`Host` / `PlainText` / `Ansi`) |
| `$PSNativeCommandUseErrorActionPreference` | 原生命令是否响应 `$ErrorActionPreference` |
| `Foreach-Object -Parallel` | 并行处理 (PS 7.0+) |
| `??` / `?.` 运算符 | Null 合并 / 条件访问 (PS 7.0+) |
| 默认 UTF-8 | 所有文件操作默认 UTF-8，无需显式指定 (但仍建议保留以兼容 PS 5.1) |

---

## 速查表: 规则 × 平台/版本

| # | 规则 | PS5.1 Win | PS7 Win | PS7 Unix | 关键差异 |
|----|------|:---------:|:-------:|:--------:|---------|
| 1 | 编码 BOM | ✅ 必须有 | 无BOM即可 | ⚠️禁BOM | PS7默认UTF-8; Unix shebang禁BOM |
| 2 | 显式编码 | ✅ 必须 | 建议保留 | 建议保留 | PS7默认已是UTF-8 |
| 3 | 禁用$Args | ✅ | ✅ | ✅ | 语言级陷阱 |
| 4 | ArgList数组 | ✅ | ✅ | ✅ | 陷阱跨版本 |
| 5 | 异步 | ✅ | ✅ | ✅ | 跨平台一致 |
| 6 | LASTEXITCODE | ✅ | ✅ | ✅ | 跨平台一致 |
| 7 | WindowStyle | ✅ | ✅ | 🔴不适用 | -RedirectStd*+WinStyle=静默忽略; -NoNew+WinStyle=报错 |
| 8 | Stop-Process | ✅ Terminate | ✅ Terminate | ⚠️SIGTERM | Windows硬杀, Unix可优雅 |
| 9 | 不依赖日志 | ✅ | ✅ | ✅ | 原则 |
| 10 | cmd /c | ✅ | ✅ | 🔴不存在 | Unix无cmd.exe |
| 11 | 批处理规范 | ✅ | ✅ | 🔴不存在 | 仅Windows |
| 12 | 进程树 | ✅ | ✅ | 🔴不存在 | 仅Win+cmd.exe场景 |
| 13 | 网络cmdlet | ✅ | ✅ | 🔴不存在 | Remove/New-NetAdapter不存在 |
| 14 | env语法 | ✅ | ✅ | ⚠️变量不同 | Win有USERPROFILE/TEMP; Unix有HOME/TMPDIR; PS7 Win都有 |
| 15 | Join-Path | ✅ | ✅ | ✅ | 跨平台 |
| 16 | 禁Invoke-Expr | ✅ | ✅ | ✅ | 语言级 |
| 17 | 对象接口 | ✅ | ✅ | ⚠️命令不同 | Win用Get-*, Unix用Unix命令 |
| 18 | BOM检查 | ✅ 查有BOM | 查无BOM | ⚠️查无BOM | Unix确认首字符是# |
| 19 | 禁数组ArgList | ✅ | ✅ | ✅ | 同#4 |
| 20 | 2>&1 ErrRec | ✅ | ✅ | ✅ | 语言级 |
| 21 | NoNewWindow | ✅ | ✅ | 🔴不可用 | Unix不支持控制台子系统 |
| 22 | bash→PS转义 | ✅ | ✅ | ⚠️不同shell | 用-File代替-Command |
| 23 | RunAs CWD | ✅ | ✅ | 🔴不适用 | 提权后CWD→System32 |
| 24 | ErrorAction | ✅ | ✅ | ✅ | Continue+try/catch |
| 25 | 总结 | ✅ | 参考 | 参考 | — |

---

## AI 生成 PowerShell 代码检查清单

生成 `.ps1` 脚本后，逐项确认：

- [ ] 目标平台是 Windows? (非 Windows 则不该生成 .ps1)
- [ ] 确认了 PS 版本并选择了对应规则?
- [ ] PS5.1: 文件有 BOM? / PS7: 无 BOM (Win) 或 确认无 BOM (Unix)?
- [ ] 全部文件 I/O 显式指定了 `-Encoding UTF8`?
- [ ] 没有使用 `$Args` 作为参数名?
- [ ] 没有向 `-ArgumentList` 传入数组?
- [ ] `Start-Process` 后正确获取退出码 (`-PassThru` + `.ExitCode`)?
- [ ] 没有 `-WindowStyle` + `-NoNewWindow` 同时使用?
- [ ] 需要窗口隐藏 + 重定向时使用了批处理方案?
- [ ] 路径使用 `Join-Path` 而非字符串拼接?
- [ ] 没有使用 `Invoke-Expression`?
- [ ] 优先使用对象接口 (Get-Net* / Get-Process)?
- [ ] 跨 shell 调用使用 `-File` 而非 `-Command`?
- [ ] `Start-Process -Verb RunAs` 使用了绝对路径 + `-WorkingDirectory`?
- [ ] 对可能不存在的 cmdlet (如 Remove-NetAdapter) 做了 try/catch?
