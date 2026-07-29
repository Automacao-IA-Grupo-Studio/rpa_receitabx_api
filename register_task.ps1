# ============================================================================
#  register_task.ps1 - Cria a tarefa agendada "ReceitaBX_Madrugada".
#  RODE UMA VEZ, ELEVADO (Administrador). Criar tarefa com "privilegios mais
#  altos" exige elevacao. Depois de criada, a tarefa dispara sozinha 03:00/dia.
#
#  Como rodar:
#    - Abra o PowerShell como Administrador e execute:
#        & 'C:\Users\automacao\Desktop\bx_api\register_task.ps1'
#    - OU, auto-elevando (abre UAC):
#        Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','C:\Users\automacao\Desktop\bx_api\register_task.ps1'
# ============================================================================

$ErrorActionPreference = 'Stop'

$action  = New-ScheduledTaskAction -Execute "C:\Users\automacao\Desktop\bx_api\rodar_madrugada.bat"

# Gatilho diario as 03:00 (usa so a hora; a data e irrelevante para -Daily).
$trigger = New-ScheduledTaskTrigger -Daily -At (Get-Date -Hour 3 -Minute 0 -Second 0)

# Roda na sessao logada do 'automacao' (casa com o tscon que mantem a sessao
# viva) e com privilegios mais altos -> main.py entra ja como admin, sem UAC.
$principal = New-ScheduledTaskPrincipal -UserId "GRUPOSTUDIO\automacao" `
    -LogonType Interactive -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8)

Register-ScheduledTask -TaskName "ReceitaBX_Madrugada" `
    -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description "Rodada noturna ReceitanetBX: orquestrar --executar --sistema todos --teto 180 (drenagem do backlog). 03:00 diario, elevado, na sessao logada." `
    -Force | Out-Null

Write-Host ""
Write-Host "==> Tarefa 'ReceitaBX_Madrugada' criada/atualizada com sucesso." -ForegroundColor Green
Get-ScheduledTask -TaskName "ReceitaBX_Madrugada" |
    Select-Object TaskName, State,
        @{n='Proxima';e={ (Get-ScheduledTaskInfo $_.TaskName).NextRunTime }} |
    Format-List

Read-Host "Pressione ENTER para sair"
