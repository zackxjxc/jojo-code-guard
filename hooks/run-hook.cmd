@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "JOJO_HOOK_NAME=%~1"
if /I "%JOJO_HOOK_NAME%"=="session-start" goto hook_name_ok
if /I "%JOJO_HOOK_NAME%"=="post-write-check" goto hook_name_ok
>&2 echo jojo-code-guard: unsupported Windows hook name.
exit /b 2

:hook_name_ok
set "JOJO_BASH="
if defined CLAUDE_CODE_GIT_BASH_PATH if exist "%CLAUDE_CODE_GIT_BASH_PATH%" set "JOJO_BASH=%CLAUDE_CODE_GIT_BASH_PATH%"
if not defined JOJO_BASH if exist "%ProgramFiles%\Git\bin\bash.exe" set "JOJO_BASH=%ProgramFiles%\Git\bin\bash.exe"
if not defined JOJO_BASH if exist "%ProgramW6432%\Git\bin\bash.exe" set "JOJO_BASH=%ProgramW6432%\Git\bin\bash.exe"
if not defined JOJO_BASH if exist "%ProgramFiles(x86)%\Git\bin\bash.exe" set "JOJO_BASH=%ProgramFiles(x86)%\Git\bin\bash.exe"
if not defined JOJO_BASH for %%I in (bash.exe) do set "JOJO_BASH=%%~$PATH:I"
if not defined JOJO_BASH goto bash_missing
if not exist "%JOJO_BASH%" goto bash_missing

for %%I in ("%~dp0..") do set "PLUGIN_ROOT=%%~fI"
set "CLAUDE_PLUGIN_ROOT=%PLUGIN_ROOT%"
if not exist "%~dp0%JOJO_HOOK_NAME%" (
    >&2 echo jojo-code-guard: bundled hook script is missing.
    exit /b 2
)

"%JOJO_BASH%" --norc --noprofile "%~dp0%JOJO_HOOK_NAME%"
set "JOJO_EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %JOJO_EXIT_CODE%

:bash_missing
>&2 echo jojo-code-guard: Git Bash was not found. Set CLAUDE_CODE_GIT_BASH_PATH or install Git for Windows.
exit /b 2
