# ============================================================================
#  register_task_fulltime.ps1 - Cria a tarefa "ReceitaBX_FullTime" (loop
#  continuo, inicia no logon) e DESLIGA as tarefas de horario fixo, que NAO
#  devem rodar junto com o loop (duas orquestracoes ao mesmo tempo brigariam
#  pelo mesmo servico).
#
#  RODE UMA VEZ, ELEVADO (Administrador):
#    Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile',`
#      '-ExecutionPolicy','Bypass','-File',`
#      'C:\Users\automacao\Desktop\bx_api\register_task_fulltime.ps1'
# ============================================================================

$ErrorActionPreference = 'Stop'

$action = New-ScheduledTaskAction -Execute "C:\Users\automacao\Desktop\bx_api\rodar_fulltime.bat"

# Inicia no logon do usuario (a sessao e mantida viva via tscon). Assim o loop
# volta sozinho depois de reboot/login.
$trigger = New-ScheduledTaskTrigger -AtLogOn

$principal = New-ScheduledTaskPrincipal -UserId "GRUPOSTUDIO\automacao" `
    -LogonType Interactive -RunLevel Highest

# ExecutionTimeLimit 0 = SEM limite (o loop roda o tempo todo, nunca e cortado).
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName "ReceitaBX_FullTime" `
    -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description "Loop continuo do ReceitaBX (VM dedicada): baixa pendentes, completa ECD parciais e limpa o disco, repetindo. Inicia no logon, elevado, sem limite de tempo." `
    -Force | Out-Null

Write-Host ""
Write-Host "==> Tarefa 'ReceitaBX_FullTime' criada (inicia no logon)." -ForegroundColor Green

# Desliga as tarefas de horario fixo (nao apaga; so desativa).
foreach ($t in "ReceitaBX_Madrugada", "ReceitaBX_ECD_Catchup") {
    try {
        Disable-ScheduledTask -TaskName $t -ErrorAction Stop | Out-Null
        Write-Host "==> Desativada (nao roda mais): $t" -ForegroundColor Yellow
    } catch {
        Write-Host "    ($t nao encontrada - ok)"
    }
}

Write-Host ""
Write-Host "Pronto. Para iniciar AGORA sem esperar o proximo logon:" -ForegroundColor Cyan
Write-Host "    Start-ScheduledTask -TaskName 'ReceitaBX_FullTime'"
Write-Host "…ou rode direto (janela elevada):  .\rodar_fulltime.bat"
Read-Host "Pressione ENTER para sair"
