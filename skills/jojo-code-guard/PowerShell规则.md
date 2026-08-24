# PowerShell 脚本编写规则（AI 专用 · 覆盖 PS 5.1 + 7.x）

编写、修改、评审或诊断 `.ps1`、`.psm1`、`.psd1`、`.bat`、`.cmd`，或者设计复杂的 PowerShell
进程、提权、重定向和跨 shell 命令时，AI 必须先读取本文档，再进行分析、编码和验证。终端外壳碰巧是
PowerShell，或只用它执行普通的只读命令，不构成本规则的触发条件。

> **适用范围**: Windows PowerShell 5.1 与跨平台 PowerShell 7。Windows 专项只在 Windows 使用；
> 非 Windows 仅在目标明确使用 PowerShell 7 / `pwsh` 时生成 `.ps1`，否则使用对应 shell 脚本。
> **优先版本**: PowerShell 7 (Core)。如目标机器仅安装 PS 5.1，建议提醒用户安装 PS 7：
> ```powershell
> winget install --id Microsoft.PowerShell --source winget
> ```
>
> **冲突声明**: 本文档经实测验证 (Win11 + PS 5.1 / PS 7.6.3)，但不保证 100% 覆盖所有边界情况。
> **若实际运行结果与本文档冲突，应以实测为准，并在回复中明确指出哪条规则可能错误。**

新建脚本使用与文件类型相符的头部注释元数据标明由 AI 编写；不得向成功输出流写入来源提示。`.psm1`、
`.psd1`、结构化输出脚本和管道脚本尤其不能因来源标记改变导入结果或输出协议。

---

## 0. 前置决策树（每次生成 .ps1 前必须执行）

```
1. 确认运行平台 → 非 Windows 仅在目标明确使用 PowerShell 7 / pwsh 时生成 .ps1，否则改用对应 shell 脚本
2. 选择解释器      → 优先探测 pwsh；Windows 上不存在时回退到 powershell.exe，二者都不存在则停止
3. 确认 PS 版本/Edition → 用选中的解释器读取 $PSVersionTable.PSVersion.Major 与 $PSVersionTable.PSEdition
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
| 新建 `.ps1/.psm1/.psd1` 且含非 ASCII | **UTF-8 with BOM** | UTF-8 无 BOM即可 |
| 新建 PowerShell 文件且纯 ASCII | UTF-8 无 BOM 即可 | UTF-8 无 BOM 即可 |
| `.bat` / `.cmd` | **UTF-8 without BOM** (且 CRLF) | 同左 |
| Unix shebang `.ps1` | 不适用 | **禁止 BOM** (内核无法解析) |

- 新建 `.ps1/.psm1/.psd1` 默认使用 UTF-8 无 BOM + LF；只有明确由 Windows PowerShell 5.1 解释且含非 ASCII
  时使用 UTF-8 BOM。
- 已有文件保持原始编码、BOM 和换行符；除非用户明确授权，不进行批量编码或换行迁移。
- `.bat/.cmd` 必须使用 UTF-8 无 BOM + CRLF；`.gitattributes` 使用 `*.bat text eol=crlf` 和
  `*.cmd text eol=crlf` 保证 Git 检出结果。这两条规则只覆盖批处理文件的全局 `* -text`。
- 新建 `.gitattributes` 时默认加入上述批处理规则。已有仓库补充规则时，必须说明后续 checkout、reset
  或重新暂存可能把现有脚本转换为 CRLF；不自动执行 `git add --renormalize`，不批量改写脚本，不修改暂存区。

### 仅在 PS 5.1 新建含非 ASCII PowerShell 文件时写入 BOM

```powershell
# 新文件应在首次写入时选择正确编码，不对已有文件做事后转码。
$path = Join-Path $PWD 'script.ps1'
$content = "# 由 AI 编写`nWrite-Output '示例'`n"
$utf8Bom = New-Object System.Text.UTF8Encoding($true)
[IO.File]::WriteAllText($path, $content, $utf8Bom)

# 只读检查 BOM。
$bytes = [IO.File]::ReadAllBytes($path)
$hasUtf8Bom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
```

### PS 7 无需此操作
PS 7 默认 UTF-8，中文注释/字符串无 BOM 可正常解析。但如有 BOM 也不报错 (Windows)。

---

## 2. 按文件基线或数据协议选择编码

不得为了“兼容”而对全部文件 I/O 无条件指定 `-Encoding UTF8`。已有文件先识别并保持原编码、BOM 和换行；
只有输入或输出协议明确为 UTF-8 时才使用对应参数。Windows PowerShell 5.1 的 `-Encoding UTF8` 会写入
BOM；新建 UTF-8 无 BOM 输出应显式使用 `UTF8Encoding($false)`：

```powershell
# 仅当输入契约明确为 UTF-8。
Get-Content -Encoding UTF8

# 新建且协议要求 UTF-8 无 BOM。
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($path, $content, $utf8NoBom)
```

已有非 UTF-8 文件不得直接套用上述示例；应按编辑前基线选择同一编码器，或在用户明确授权迁移后再转码。

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

# 方案 2: PS 7 需要精确参数边界或进程控制时
$startInfo = [Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $exe
$startInfo.UseShellExecute = $false
foreach ($argument in $arguments) {
    [void]$startInfo.ArgumentList.Add($argument)
}
$process = [Diagnostics.Process]::Start($startInfo)
```

`ProcessStartInfo.ArgumentList` 只在支持它的现代 .NET/PowerShell 7 路线使用。PS 5.1 若必须构造单一
`Arguments` 字符串，应使用经过 argv 回显测试的 Windows 引号函数；不得把不可信参数拼进 `.bat/.cmd`。

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
需要隐藏窗口并捕获输出时，优先直接使用 `ProcessStartInfo`，不要用动态批处理拼接参数。

```powershell
# PowerShell 7：同时隐藏窗口、捕获输出并保持参数边界。
$startInfo = [Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $exe
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
foreach ($argument in $arguments) { [void]$startInfo.ArgumentList.Add($argument) }
$process = [Diagnostics.Process]::Start($startInfo)
$stdoutTask = $process.StandardOutput.ReadToEndAsync()
$stderrTask = $process.StandardError.ReadToEndAsync()
$process.WaitForExit()
$stdout = $stdoutTask.GetAwaiter().GetResult()
$stderr = $stderrTask.GetAwaiter().GetResult()
```

PS 5.1 没有 `ArgumentList`；只有参数固定或经过专门 argv 引号测试时才构造 `Arguments` 字符串。若参数不可信，
应报告兼容性限制，不用批处理插值绕过。

### ⚠️ 已知文档冲突 (实测修正)
旧版文档称 PS 7 下 `-RedirectStandardOutput + -WindowStyle` "改为直接报错"——实测 **不报错**，与 PS 5.1 同为静默忽略。
如遇相反行为，可能因 PS 7 子版本差异，请以实际运行结果为准。

---

## 8. Stop-Process 行为 (Windows vs Unix)

| 平台 | PS 版本 | `Stop-Process` 调用路径 | 效果 |
|------|---------|-------------------------|------|
| Windows | 5.1 / 7 | `.NET Process.Kill`（最终调用 `TerminateProcess`） | 强制终止，不保证 finally、析构或缓冲区刷新 |
| Unix | 7 | `.NET Process.Kill` | 强制终止，不保证信号处理器、atexit、finally 或缓冲区刷新 |

### 所有平台的后果
- C++ 析构函数不执行
- Python atexit / C# finally 不触发
- stdout/stderr 缓冲区不刷新 (日志可能为空)
- 依赖析构清理的资源 (路由/DNS/注册表) 不会自动恢复

### 正确做法
```powershell
# 不依赖程序自动清理，脚本额外验证
Stop-Process -Id $targetProcessId -Force

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

# 推荐: 直接调用；调用运算符可正确处理含空格的可执行文件路径
& $exe arg1 arg2

# 需要更强进程控制时使用 ProcessStartInfo，并逐项添加参数
```

**仅 Windows 适用。Unix 无 cmd.exe。**

---

## 11. 临时批处理文件安全边界

批处理只用于内容固定、参数可信且确实需要 `cmd.exe` 语义的 Windows 场景，并保持 UTF-8 无 BOM + CRLF。
不得把可执行路径或不可信参数直接插值到批处理文本；包含 `& | < > ^ % ! "` 的值会改变 `cmd.exe` 语义。
动态参数优先通过 `&` 或 `ProcessStartInfo.ArgumentList` 逐项传递。无法证明 PS 5.1 引号正确时停止并报告，
不要生成看似可用但存在参数注入的包装脚本。

**仅 Windows 适用。**

---

## 12. 进程树注意事项

通过批处理文件启动进程时:
- 进程树: `cmd.exe (bat) → target.exe`
- `Start-Process -PassThru` 返回 **cmd.exe** 的进程对象，**不是 target.exe**
- 停止时需清理整个进程树

```powershell
# 仅终止父进程，不会递归处理后代
Stop-Process -Id $p.Id -Force

# Windows 上终止整棵进程树
& taskkill.exe /PID $p.Id /T /F
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
| 自动变量 `$HOME` | ✅ | ✅（通常为用户配置目录） | ✅ |
| `$env:HOME` | 仅外部环境提供时存在 | 仅外部环境提供时存在 | 通常存在 |
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

| PS 版本 | 要求 | 验证方式 |
|---------|------|---------|
| 5.1 | 已有文件保持基线；新建非 ASCII 脚本使用 BOM | `[IO.File]::ReadAllBytes()` 检查前三字节 |
| 7 (Windows) | 新建文件默认无 BOM；已有文件保持基线 | 同左 |
| 7 (Unix) | shebang 脚本禁止 BOM | 同左，并确认首字节为 `0x23`（`#`） |

### PS 7 下无需执行 PS 5.1 的 BOM 补全流程
PS 7 默认 UTF-8，Write/Edit 工具写入后不需要补 BOM (Windows 上)。Unix shebang 脚本用 .NET 字节 API
确认无 BOM，且首字节应是 `0x23`（`#`）；不要依赖目标机器未必安装的 `head` 或 `xxd`。

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
| 默认 UTF-8 | PowerShell 7 的文本 cmdlet 默认值更接近 UTF-8；仍按文件基线或数据协议选择编码 |

---

## 26. 需要用户手动执行的复杂命令保持单行

需要用户在系统默认 PowerShell 命令行窗口中手动执行复杂命令时，默认只提供可直接粘贴的一行版本，
不要提供依赖换行、续行符或多行粘贴的版本。

如果命令过长、引号或流程控制复杂，优先生成一个本地 `.ps1` 脚本文件，并只向用户提供一条执行该脚本的
单行命令，例如：

```powershell
pwsh -File .\scripts\run-task.ps1
```

脚本文件仍须遵守本文档的编码、版本兼容和安全规则。用户明确要求多行示例，或目标终端已确认支持可靠的
多行粘贴时，可以按用户要求提供多行版本。

---

## 速查表: 规则 × 平台/版本

| # | 规则 | PS5.1 Win | PS7 Win | PS7 Unix | 关键差异 |
|----|------|:---------:|:-------:|:--------:|---------|
| 1 | 编码 BOM | 新建非ASCII脚本需要 | 无BOM即可 | ⚠️禁BOM | 已有文件始终保真; Unix shebang禁BOM |
| 2 | 显式编码 | 按基线/协议 | 按基线/协议 | 按基线/协议 | PS5.1 的 UTF8 输出会带 BOM |
| 3 | 禁用$Args | ✅ | ✅ | ✅ | 语言级陷阱 |
| 4 | ArgList数组 | ✅ | ✅ | ✅ | 陷阱跨版本 |
| 5 | 异步 | ✅ | ✅ | ✅ | 跨平台一致 |
| 6 | LASTEXITCODE | ✅ | ✅ | ✅ | 跨平台一致 |
| 7 | WindowStyle | ✅ | ✅ | 🔴不适用 | -RedirectStd*+WinStyle=静默忽略; -NoNew+WinStyle=报错 |
| 8 | Stop-Process | ✅ Process.Kill | ✅ Process.Kill | ✅ Process.Kill | 所有平台均为强制终止，不保证清理 |
| 9 | 不依赖日志 | ✅ | ✅ | ✅ | 原则 |
| 10 | cmd /c | ✅ | ✅ | 🔴不存在 | Unix无cmd.exe |
| 11 | 批处理安全边界 | ✅ | ✅ | 🔴不存在 | 不拼接不可信参数; 仅Windows |
| 12 | 进程树 | ✅ | ✅ | 🔴不存在 | 仅Win+cmd.exe场景 |
| 13 | 网络cmdlet | ✅ | ✅ | 🔴不存在 | Remove/New-NetAdapter不存在 |
| 14 | env语法 | ✅ | ✅ | ⚠️变量不同 | `$HOME` 是自动变量；`$env:HOME` 不保证存在 |
| 15 | Join-Path | ✅ | ✅ | ✅ | 跨平台 |
| 16 | 禁Invoke-Expr | ✅ | ✅ | ✅ | 语言级 |
| 17 | 对象接口 | ✅ | ✅ | ⚠️命令不同 | Win用Get-*, Unix用Unix命令 |
| 18 | BOM检查 | 按基线；新建非ASCII查有BOM | 查无BOM | ⚠️查无BOM | Unix确认首字符是# |
| 19 | 禁数组ArgList | ✅ | ✅ | ✅ | 同#4 |
| 20 | 2>&1 ErrRec | ✅ | ✅ | ✅ | 语言级 |
| 21 | NoNewWindow | ✅ | ✅ | 🔴不可用 | Unix不支持控制台子系统 |
| 22 | bash→PS转义 | ✅ | ✅ | ⚠️不同shell | 用-File代替-Command |
| 23 | RunAs CWD | ✅ | ✅ | 🔴不适用 | 提权后CWD→System32 |
| 24 | ErrorAction | ✅ | ✅ | ✅ | Continue+try/catch |
| 25 | 总结 | ✅ | 参考 | 参考 | — |
| 26 | 手动复杂命令单行化 | ✅ | ✅ | 🔴不适用 | 过于复杂时生成 .ps1，并给出单行执行命令 |

---

## AI 生成 PowerShell 代码检查清单

生成 `.ps1` 脚本后，逐项确认：

- [ ] 已确认目标平台? (非 Windows 仅在目标明确使用 PowerShell 7 / pwsh 时生成 .ps1)
- [ ] 确认了 PS 版本并选择了对应规则?
- [ ] 已有文件保持原编码/BOM/换行；新建 PS5.1 非 ASCII 脚本才使用 BOM；Unix shebang 确认无 BOM?
- [ ] 文件 I/O 按已知基线或数据协议选择编码，未无条件套用 `-Encoding UTF8`?
- [ ] 没有使用 `$Args` 作为参数名?
- [ ] 没有向 `-ArgumentList` 传入数组?
- [ ] `Start-Process` 后正确获取退出码 (`-PassThru` + `.ExitCode`)?
- [ ] 没有 `-WindowStyle` + `-NoNewWindow` 同时使用?
- [ ] 需要窗口隐藏 + 重定向时使用安全进程 API，未把不可信参数拼进批处理?
- [ ] 路径使用 `Join-Path` 而非字符串拼接?
- [ ] 没有使用 `Invoke-Expression`?
- [ ] 优先使用对象接口 (Get-Net* / Get-Process)?
- [ ] 跨 shell 调用使用 `-File` 而非 `-Command`?
- [ ] `Start-Process -Verb RunAs` 使用了绝对路径 + `-WorkingDirectory`?
- [ ] 对可能不存在的 cmdlet (如 Remove-NetAdapter) 做了 try/catch?
- [ ] 需要用户手动执行的复杂命令只提供单行版本，或生成了 `.ps1` 并提供单行执行命令?
