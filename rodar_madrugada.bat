@echo off
REM ============================================================================
REM  rodar_madrugada.bat - Rodada noturna do ReceitanetBX (drenagem do backlog)
REM ----------------------------------------------------------------------------
REM  Chamado pelo Agendador de Tarefas (tarefa "ReceitaBX_Madrugada"), 03:00
REM  todo dia. A tarefa roda com "privilegios mais altos", entao o main.py
REM  detecta que JA e admin (IsUserAnAdmin) e NAO dispara UAC.
REM
REM  --teto 180 = modo DRENAGEM: janela de ate 3h por certificado, sem corte
REM  por "sem novidade" - feito pra esvaziar o backlog de madrugada.
REM
REM  Log detalhado: logs\orquestrar-AAAAMMDD.log  (gravado pelo main.py)
REM  Log do wrapper (bootstrap/erros): logs\madrugada.log
REM ============================================================================
setlocal
cd /d "C:\Users\automacao\Desktop\bx_api"

set "PY=C:\Users\automacao\AppData\Local\Programs\Python\Python313\python.exe"

echo ============================================================>> "logs\madrugada.log"
echo [%date% %time%] INICIO rodada noturna>> "logs\madrugada.log"

"%PY%" main.py orquestrar --executar --sistema todos --teto 180 >> "logs\madrugada.log" 2>&1

echo [%date% %time%] FIM (exit=%ERRORLEVEL%)>> "logs\madrugada.log"
endlocal
