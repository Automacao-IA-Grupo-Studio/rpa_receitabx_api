"""
receitanetbx — Automação de download de SPED via ReceitanetBX Serviço.

Pacote que orquestra o fluxo em três etapas:
  1. SOLICITAR  (Python → API SOAP)          → operacoes.solicitar_*
  2. SERVIÇO BAIXA (automático, background)   → logs em bx_temp/logs
  3. PROCESSAR  (logs → regra → rede)         → processador.*

O ponto de entrada de linha de comando é o main.py na raiz do projeto.
"""

__version__ = "2.0.0"
