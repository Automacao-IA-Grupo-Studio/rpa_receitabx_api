@echo off
REM ============================================================================
REM  rodar_ecd_catchup.bat - Catch-up de ECD (2a passada, ~09:00)
REM ----------------------------------------------------------------------------
REM  O ECD demora horas para a Receita disponibilizar. A madrugada (03:00) cria
REM  os pedidos; algumas horas depois os arquivos ficam prontos. Esta tarefa
REM  reprocessa SO o ECD das linhas que ficaram PARCIAIS (status 21), sem
REM  re-baixar os outros 3 documentos, e fecha a linha em 5 quando o ECD completa.
REM
REM  Chamado pela tarefa "ReceitaBX_ECD_Catchup" (09:00 diario, elevado).
REM  Log detalhado: logs\orquestrar-AAAAMMDD.log | wrapper: logs\ecd_catchup.log
REM ============================================================================
setlocal
cd /d "C:\Users\automacao\Desktop\bx_api"

set "PY=C:\Users\automacao\AppData\Local\Programs\Python\Python313\python.exe"

echo ============================================================>> "logs\ecd_catchup.log"
echo [%date% %time%] INICIO catch-up ECD>> "logs\ecd_catchup.log"

"%PY%" main.py orquestrar --executar --catchup --teto 120 >> "logs\ecd_catchup.log" 2>&1

echo [%date% %time%] FIM (exit=%ERRORLEVEL%)>> "logs\ecd_catchup.log"
endlocal
