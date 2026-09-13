# 증분 빌드를 Windows 작업 스케줄러에 등록/실행하는 스크립트 (OS 스케줄러 + CLI 방식, 권장).
#   .\setup\schedule_build.ps1 -Register -Time 02:30      # 매일 02:30 증분 빌드 작업 등록 (현재 사용자)
#   .\setup\schedule_build.ps1 -Unregister
#   .\setup\schedule_build.ps1                            # 지금 1회 실행 (스케줄러가 호출하는 것과 동일)
# 빌드는 파일 락(data/build.lock)으로 서버 워처/다른 스케줄과 충돌하지 않으며, 실패 시 종료 코드 ≠ 0 과 logs/build.log 에 기록된다.
param(
  [switch]$Register,
  [switch]$Unregister,
  [string]$Time = "02:30",
  [string]$TaskName = "LLMWiki-IncrementalBuild",
  [switch]$Full
)
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = (Get-Command python).Source
$args = "-m llmwiki build" + $(if ($Full) { " --full" } else { "" }) + " --json"
if ($Register) {
  $action = New-ScheduledTaskAction -Execute $python -Argument $args -WorkingDirectory $root
  $trigger = New-ScheduledTaskTrigger -Daily -At $Time
  $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 6) -MultipleInstances IgnoreNew -StartWhenAvailable
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "LLM Wiki incremental build" -Force | Out-Null
  Write-Host "registered '$TaskName' daily at $Time  ->  $python $args  (cwd $root)"
  Write-Host "확인: Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
  exit 0
}
if ($Unregister) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false; Write-Host "unregistered $TaskName"; exit 0 }
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"
& $python -m llmwiki health --for-build --quick
& $python -m llmwiki build $(if ($Full) { "--full" })
$code = $LASTEXITCODE
if ($code -ne 0) { Write-Host "build failed (exit $code) — python -m llmwiki logs tail --file build / build status" }
exit $code
