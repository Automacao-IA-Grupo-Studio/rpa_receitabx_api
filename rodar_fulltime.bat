@echo off
REM ============================================================================
REM  rodar_fulltime.bat - Loop CONTINUO do ReceitaBX (VM dedicada).
REM ----------------------------------------------------------------------------
REM  Roda ELEVADO (Administrador). Substitui as tarefas de horario fixo
REM  (madrugada / catch-up). Cada CICLO faz, em ordem:
REM    1) orquestrar --sistema todos --limpar : baixa tudo que esta pendente e,
REM       ao fim, apaga do download_bx o que ja foi arquivado (libera disco);
REM    2) orquestrar --catchup                : completa os ECD que ficaram
REM       parciais (a Receita disponibiliza o ECD horas depois);
REM    3) dorme SLEEP segundos e repete.
REM
REM  Log resumido: logs\fulltime.log   |   detalhes: logs\orquestrar-AAAAMMDD.log
REM  Parar: feche esta janela (ou pare a tarefa ReceitaBX_FullTime).
REM ============================================================================
setlocal
cd /d "C:\Users\automacao\Desktop\bx_api"
set "PY=C:\Users\automacao\AppData\Local\Programs\Python\Python313\python.exe"
set "SLEEP=900"

REM Precisa de admin (reconfigura o servico a cada certificado). Sem elevacao,
REM cada execucao tentaria abrir UAC repetidamente.
net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo [ERRO] Este loop precisa rodar como ADMINISTRADOR.
  echo Abra o PowerShell/cmd elevado e rode novamente, ou use a tarefa
  echo agendada "ReceitaBX_FullTime" ^(register_task_fulltime.ps1^).
  echo.
  pause
  exit /b 1
)

echo [%date% %time%] LOOP FULL-TIME iniciado (dedicado a ReceitaBX).>> "logs\fulltime.log"

:loop
echo(>> "logs\fulltime.log"
echo [%date% %time%] ===== CICLO INICIO =====>> "logs\fulltime.log"

"%PY%" main.py orquestrar --executar --sistema todos --limpar   >> "logs\fulltime.log" 2>&1
"%PY%" main.py orquestrar --executar --catchup --teto 120 --limpar >> "logs\fulltime.log" 2>&1

echo [%date% %time%] ===== CICLO FIM (dormindo %SLEEP%s) =====>> "logs\fulltime.log"
powershell -NoProfile -Command "Start-Sleep -Seconds %SLEEP%"
goto loop
