# 安装事件触发的计划任务（右键“以管理员身份运行”本文件，或双击 安装计划任务.bat 自动提权）
# 触发器：网络连接 / 网络断开 / 用户登录 / 睡眠唤醒 —— 已在线时脚本 1 秒内退出，零打扰
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$taskName  = 'SRun校园网自动认证'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script    = Join-Path $scriptDir 'srun_auth.py'

$py = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source }
if (-not $py) {
    Write-Error '未找到 Python。请先安装 Python 3（安装时勾选 Add to PATH）后重试。'
    exit 1
}
Write-Host "Python: $py"

# 触发器1：任何网络连接成功（插网线/连WiFi）
$netSub = '<QueryList><Query Id="0" Path="Microsoft-Windows-NetworkProfile/Operational"><Select Path="Microsoft-Windows-NetworkProfile/Operational">*[System[EventID=10000]]</Select></Query></QueryList>'
$tNet = New-CimInstance -ClassName MSFT_TaskEventTrigger -Namespace Root/Microsoft/Windows/TaskScheduler `
    -Property @{Subscription = $netSub; Delay = 'PT5S'; Enabled = $true} -ClientOnly

# 触发器2：用户登录（覆盖开机后未认证场景）
$tLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$tLogon.Delay = 'PT15S'

# 触发器3：网络断开（拔网线后自动切无线并认证）
$discSub = '<QueryList><Query Id="0" Path="Microsoft-Windows-NetworkProfile/Operational"><Select Path="Microsoft-Windows-NetworkProfile/Operational">*[System[EventID=10001]]</Select></Query></QueryList>'
$tDisc = New-CimInstance -ClassName MSFT_TaskEventTrigger -Namespace Root/Microsoft/Windows/TaskScheduler `
    -Property @{Subscription = $discSub; Delay = 'PT5S'; Enabled = $true} -ClientOnly

# 触发器4：从睡眠唤醒
$resumeSub = '<QueryList><Query Id="0" Path="System"><Select Path="System">*[System[Provider[@Name="Microsoft-Windows-Kernel-Power"] and EventID=507]]</Select></Query></QueryList>'
$tResume = New-CimInstance -ClassName MSFT_TaskEventTrigger -Namespace Root/Microsoft/Windows/TaskScheduler `
    -Property @{Subscription = $resumeSub; Delay = 'PT5S'; Enabled = $true} -ClientOnly

# 注意：New-CimInstance 手搓的事件触发器缺少 MSFT_TaskTrigger 基类型名，需手动补上，否则 Register-ScheduledTask 拒绝
function Fix-TriggerType($t) {
    if ($t.PSTypeNames -notcontains 'Microsoft.Management.Infrastructure.CimInstance#MSFT_TaskTrigger') {
        $idx = [Math]::Min(1, $t.PSTypeNames.Count - 1)
        $t.PSTypeNames.Insert($idx, 'Microsoft.Management.Infrastructure.CimInstance#MSFT_TaskTrigger')
    }
    return $t
}
$tNet    = Fix-TriggerType $tNet
$tDisc   = Fix-TriggerType $tDisc
$tResume = Fix-TriggerType $tResume

$action = New-ScheduledTaskAction -Execute $py -Argument "`"$script`" --quiet --retries 3" -WorkingDirectory $scriptDir
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $tNet, $tLogon, $tDisc, $tResume `
    -Principal $principal -Settings $settings `
    -Description 'SRun 深澜校园网自动认证：网络连接/断开、登录、唤醒时自动检查并认证。' -Force | Out-Null

# 立即运行一次验证
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 8
$info = Get-ScheduledTaskInfo -TaskName $taskName
Write-Host ("任务已安装并测试运行，LastTaskResult = " + $info.LastTaskResult)
Write-Host ("日志文件: " + (Join-Path $scriptDir 'auth.log'))
pause
