# ============================================================================
#  register_task_ecd.ps1 - Cria a tarefa "ReceitaBX_ECD_Catchup" (09:00 diario).
#  RODE UMA VEZ, ELEVADO (Administrador). Segunda passada que recolhe os ECD que
#  a Receita so disponibiliza horas apos a madrugada.
#
#  Como rodar:
#    - PowerShell como Administrador:
#        & 'C:\Users\automacao\Desktop\bx_api\register_task_ecd.ps1'
#    - OU auto-elevando (abre UAC):
#        Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','C:\Users\automacao\Desktop\bx_api\register_task_ecd.ps1'
# ============================================================================

$ErrorActionPreference = 'Stop'

$action  = New-ScheduledTaskAction -Execute "C:\Users\automacao\Desktop\bx_api\rodar_ecd_catchup.bat"

# Gatilho diario as 09:00 (a madrugada roda 03:00; 6h dao tempo p/ a Receita
# preparar os ECD).
$trigger = New-ScheduledTaskTrigger -Daily -At (Get-Date -Hour 9 -Minute 0 -Second 0)

$principal = New-ScheduledTaskPrincipal -UserId "GRUPOSTUDIO\automacao" `
    -LogonType Interactive -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8)

Register-ScheduledTask -TaskName "ReceitaBX_ECD_Catchup" `
    -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description "Catch-up de ECD: reprocessa so o ECD das linhas parciais (status 21) e fecha em 5 quando completar. 09:00 diario, elevado, na sessao logada." `
    -Force | Out-Null

Write-Host ""
Write-Host "==> Tarefa 'ReceitaBX_ECD_Catchup' criada/atualizada com sucesso." -ForegroundColor Green
Get-ScheduledTask -TaskName "ReceitaBX_ECD_Catchup" |
    Select-Object TaskName, State,
        @{n='Proxima';e={ (Get-ScheduledTaskInfo $_.TaskName).NextRunTime }} |
    Format-List

Read-Host "Pressione ENTER para sair"
