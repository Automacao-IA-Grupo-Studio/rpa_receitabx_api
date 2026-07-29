"""
config.py — Configuração central do projeto.

Ponto único de verdade para endpoints, caminhos, parâmetros padrão e filtros.
Enquanto a integração com banco de dados não existe, os valores ficam aqui
(fase de validação). Quando o banco entrar, basta trocar a origem destes
valores — o resto do código consome sempre estas constantes/funções.
"""

from datetime import date
from pathlib import Path

# ── API SOAP (ReceitanetBX Serviço) ──────────────────────────────────────
ENDPOINT = "http://127.0.0.1:2443/services/ReceitanetBX"
STATUS_URL = "http://127.0.0.1:2444/"          # painel HTML de acompanhamento
NS_AXIS2 = "http://ws.apache.org/axis2"
SOAP_ENV = "http://schemas.xmlsoap.org/soap/envelope/"
HTTP_TIMEOUT = 60                              # segundos

# ── Identificação padrão (SPED ECF via Procurador) ───────────────────────
PERFIL_PADRAO = "Procurador"                   # "Contribuinte" ou "Procurador"
SISTEMA = "SPED ECF"
TIPOARQUIVO = "Escrituração"
TIPOPESQUISA = "Período da Escrituração"       # valor exato exigido pela API
TIPO_NI_PADRAO = "cnpj"                         # "cnpj" ou "cpf"

# ── Período padrão da pesquisa/solicitação ───────────────────────────────
DATA_INI_PADRAO = "01/01/2014"                 # início histórico


def data_fim_hoje() -> str:
    """Data de fim = hoje (a API rejeita data futura)."""
    return date.today().strftime("%d/%m/%Y")


# ── Filtros aplicados no processamento dos logs ──────────────────────────
SISTEMA_ALVO = "SPED ECF"                      # padrão (retrocompatível)
TIPO_ALVO = "Escrituração"                     # ignora Recibo / -REC

# ── Sistemas ReceitanetBX suportados ─────────────────────────────────────
# Cada sistema: nome EXATO como aparece no log (campo "sistema"), a pasta de
# destino na rede (DEST_REDE/{cnpj}/RECEITABX/<subpasta>), a data inicial da
# solicitação e o id na fila do AUTOMATAX (None = ainda não mapeado).
# A regra de retificadora é a MESMA para todos (por período): mantém a
# retificadora mais recente do período e descarta as demais versões. Como o
# período do PISCOFINS/ECD/ICMS é MENSAL, o desempate é por mês automaticamente.
# Parâmetros CONFIRMADOS na Receita (pesquisa read-only, ret=1) — 2026-07-10.
# Cada sistema tem seu tipoarquivo, tipopesquisa e NOMES de campo próprios (a
# grafia varia: ECF usa "Data de início"; ICMS usa "Data Inicio" sem acento).
#   campos_extra: campos fixos adicionais (checkboxes booleano = "V"/"F").
#   attr_contribuinte: atributo do log que traz o CNPJ (ECF/PISCOFINS="Contribuinte";
#                      ECD/ICMS="CNPJ", pois trazem estabelecimentos/filiais).
#   regra_retif: aplica a regra de retificadora (True, ECF/PISCOFINS) ou não.
#   regra_situacao: ECD — mantém TODAS as escriturações, exceto a SUBSTITUÍDA
#                  (o ECD pode ter vários SPEDs válidos do mesmo período; só a
#                  versão substituída fica de fora). ICMS não tem situação →
#                  mantém tudo (nem retif, nem situação).
#   attr_situacao: atributo do log que traz a Situação SPED (só o ECD tem).
#   pasta_cliente: grava na pasta do CLIENTE (não do estabelecimento) — ICMS,
#                  que traz filiais com CNPJ próprio, mas vai tudo na pasta do cliente.
#   solicita_por_ids: SolicitarArquivos exige a LISTA de arquivos (Rota B) em vez
#                  do período (Rota A). ECD e ICMS recusam o período ("A solicitação
#                  não pode ser realizada através de um critério de pesquisa. Por
#                  favor, informe a lista de arquivos.") → pesquisa-se e solicita-se
#                  pelos IDs retornados. ECF/PISCOFINS aceitam por período (Rota A).
SISTEMAS = {
    "ecf": {
        "sistema": "SPED ECF", "subpasta": "ECF", "data_ini": "01/01/2014",
        "tipoarquivo": "Escrituração", "tipopesquisa": "Período da Escrituração",
        "campo_data_ini": "Data de início", "campo_data_fim": "Data de fim",
        "campos_extra": [], "attr_contribuinte": "Contribuinte",
        "attr_situacao": None, "regra_situacao": False,
        "regra_retif": True, "pasta_cliente": False, "tipo_automatax": 5,
        "solicita_por_ids": False,
    },
    "piscofins": {
        "sistema": "SPED Contribuições", "subpasta": "PISCOFINS", "data_ini": "01/01/2012",
        "tipoarquivo": "Escrituração", "tipopesquisa": "Período da Escrituração",
        "campo_data_ini": "Data de início", "campo_data_fim": "Data de fim",
        "campos_extra": [], "attr_contribuinte": "Contribuinte",
        "attr_situacao": None, "regra_situacao": False,
        "regra_retif": True, "pasta_cliente": False, "tipo_automatax": None,
        "solicita_por_ids": False,
    },
    "ecd": {
        "sistema": "SPED Contábil", "subpasta": "ECD", "data_ini": "01/01/2008",
        "tipoarquivo": "Escrituração Contábil Digital",
        "tipopesquisa": "Por Período da Escrituração",
        "campo_data_ini": "Data de início", "campo_data_fim": "Data de fim",
        "campos_extra": [], "attr_contribuinte": "CNPJ",
        # ECD: mantém tudo, exceto a SUBSTITUÍDA (regra_situacao).
        "attr_situacao": "Situação SPED", "regra_situacao": True,
        "regra_retif": False, "pasta_cliente": False, "tipo_automatax": None,
        "solicita_por_ids": True,   # ECD só solicita pela lista de arquivos (Rota B)
    },
    "icms": {
        "sistema": "SPED Fiscal - EFD ICMS IPI", "subpasta": "ICMS", "data_ini": "01/01/2012",
        "tipoarquivo": "Escrituração Fiscal Digital",
        "tipopesquisa": "Por Período da Escrituracao",  # SEM ç/ã em "Escrituracao"
        "campo_data_ini": "Data Inicio", "campo_data_fim": "Data Fim",  # sem "de", sem acento
        "campos_extra": [
            ("Buscar Arquivos de Todos os Estabelecimentos", "V"),
            ("Último arquivo transmitido", "V"),
        ],
        "attr_contribuinte": "CNPJ",
        "attr_situacao": None, "regra_situacao": False,
        "regra_retif": False, "pasta_cliente": True, "tipo_automatax": None,
        "solicita_por_ids": True,   # ICMS só solicita pela lista de arquivos (Rota B)
    },
}


def sistema_cfg(chave: str) -> dict:
    """Retorna a config de um sistema (ecf/piscofins/ecd/icms)."""
    try:
        return SISTEMAS[chave]
    except KeyError:
        raise ValueError(f"sistema desconhecido: {chave!r} (use {list(SISTEMAS)})")

# ── Caminhos de arquivos ─────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Base onde o serviço grava downloads e logs. No modo multi-certificado, cada
# procurador ganha uma subpasta própria (BX_TEMP_BASE / <profile_key>), o que
# isola tanto os arquivos baixados quanto os logs daquele certificado.
BX_TEMP_BASE = Path(r"C:\Users\automacao\Documents\download_bx")

# Base padrão (uso avulso, um certificado só) — retrocompatível.
LOG_BASE = BX_TEMP_BASE / "logs"

# Destino final na rede: DEST_REDE / {cnpj_cliente} / RECEITABX / ECF
DEST_REDE = Path(r"\\192.168.1.238\Dados")

# Controle de duplicados: 1 hash MD5 por linha (arquivos já movidos)
CONTROLE = PROJECT_ROOT / "movidos.txt"

# Logs da APLICAÇÃO (execução do orquestrador) — NÃO confundir com os logs do
# SERVIÇO (bx_temp/<profile>/logs). Um arquivo por dia, fácil de acompanhar.
LOG_APP_DIR = PROJECT_ROOT / "logs"

# Subpastas dos logs por operação (base padrão)
LOG_PEDIDOS = LOG_BASE / "pedidos"             # pedidos-AAAAMMDD.log
LOG_DOWNLOAD = LOG_BASE / "download"           # download-AAAAMMDD.log
LOG_ERROS = LOG_BASE / "erros"                 # erros-AAAAMMDD.log


def base_procurador(profile_key: str) -> Path:
    """Pasta de gravação (downloads + logs) de um procurador. É o valor que vai
    em ``caminhoGravacaoArquivos`` no .properties do serviço."""
    return BX_TEMP_BASE / (profile_key or "").strip()


def log_base_procurador(profile_key: str) -> Path:
    """Pasta de logs de um procurador (o serviço grava em <gravação>/logs)."""
    return base_procurador(profile_key) / "logs"


# ── Serviço Windows ReceitanetBX (modo multi-certificado) ────────────────
SERVICO_NOME = "ReceitanetBX"                  # nome do serviço no Windows
SERVICO_DIR = Path(
    r"C:\Program Files (x86)\Programas RFB\Receitanet Bx Servico"
)
PROPERTIES_PATH = SERVICO_DIR / "recnetbx-service.properties"
WSDL_URL = "http://127.0.0.1:2443/services/ReceitanetBX?wsdl"

# Pasta local com os arquivos .pfx (o nome do arquivo vem do banco: pfx_filename)
CERT_DIR = Path(r"C:\Users\automacao\Desktop\certificados")

# Certificados a ignorar no orquestrador (ex.: vencidos). Vazio = processa todos.
# Studio Agro (id 5): reativado em 2026-07-16 — certificado renovado (válido até
# 2027-07-10) e senha atualizada no AUTOMATAX; abre o .pfx (validado).
CERT_IDS_IGNORADOS = set()

# Tempo máximo (s) para o serviço subir os endpoints após reiniciar.
SERVICO_TIMEOUT_ONLINE = 300

# ── Oráculo da criptografia da senha (ver cripto_senha.py) ───────────────
# Par conhecido texto/cifra usado para descobrir a chave (MAC) desta máquina.
# A cifra abaixo foi gravada pelo próprio serviço; o texto é a senha do
# certificado de referência (Studio Varejo), lida do banco — sem senha em texto
# no repositório. Se a tabela de certificados for reorganizada e o id abaixo
# mudar, o orquestrador cai para as senhas de TODOS os certs (self-healing), mas
# mantenha este id apontando para o Studio Varejo para o caminho rápido.
CRIPTO_ORACULO_CIFRA = "nWLiR2gT1xhwpqhI5N+fcI9T+zOTzAGV"
CRIPTO_ORACULO_CERT_ID = 8  # Studio Varejo (CNPJ 44189727000134)

# ── Parâmetros da espera adaptativa (orquestrador) ───────────────────────
ORQ_TIPO_ARQUIVO = 5                            # SPED ECF na fila do AUTOMATAX
ORQ_STATUS_PENDENTE = 1                         # "Aguardando"
ORQ_POLL_SEGUNDOS = 30                          # intervalo do polling
# Só encerra a espera depois de MUITO tempo sem NENHUM arquivo novo. Os últimos
# arquivos de um documento (ex.: ECD) podem demorar a ser preparados pela Receita
# e o serviço só verifica a cada ~3 min — 12 min cortava cedo demais e deixava
# pedidos em 1/2, 10/12. Enquanto estiver chegando arquivo, NUNCA corta; só
# desiste após este tempo TODO parado. Se ainda cortar cedo, aumente aqui.
ORQ_SEM_NOVIDADE_SEGUNDOS = 25 * 60            # encerra só após 25 min sem arquivo novo
ORQ_LIMITE_LOTE_PEQUENO = 5                     # até 5 solicitações = lote pequeno
ORQ_TETO_PEQUENO_SEGUNDOS = 60 * 60            # teto p/ lote pequeno (backstop absoluto)
ORQ_TETO_GRANDE_SEGUNDOS = 90 * 60             # teto p/ lote grande (backstop absoluto)

# O serviço só verifica/baixa pedidos a cada `intervaloAtualizacaoPedidos` (min)
# e a Receita leva alguns minutos para preparar os arquivos. Por isso a espera é
# PACIENTE: não desiste enquanto NENHUM arquivo chegou (só o teto limita); depois
# que começam a chegar, encerra após ORQ_SEM_NOVIDADE_SEGUNDOS sem novidade.
SERVICO_INTERVALO_MIN = 10                      # fallback se não ler do .properties
ORQ_HEARTBEAT_SEGUNDOS = 90                     # cadência do progresso no console

# Valores gravados no .properties a cada troca de certificado (o orquestrador
# roda elevado, então consegue escrever em Program Files). Reduzir o intervalo
# faz o serviço verificar/baixar com mais frequência → lote muito mais rápido.
# OBS: o GUI trava o mínimo em 10; aqui forçamos direto. Se o serviço recusar
# um valor < 10 e não subir, o 1º grupo cai em "serviço offline" — aí é só
# voltar SERVICO_INTERVALO_ALVO para 10.
SERVICO_INTERVALO_ALVO = 3                      # min entre ciclos (era 10)
SERVICO_DOWNLOADS_SIMULTANEOS = 6              # downloads paralelos por certificado
# 4 sistemas (ECF/PISCOFINS/ECD/ICMS) = ~4x o volume por certificado, e os
# mensais (PISCOFINS/ECD/ICMS) têm ~12x períodos do ECF. Dobrar os downloads
# paralelos (3→6) drena a fila de "disponível para baixar" mais rápido. Se a
# madrugada mostrar que o gargalo é o download (e não a Receita liberar), pode
# subir para 8. O GUI trava o mínimo, mas gravamos direto no .properties.

# A Receita às vezes responde "Serviço indisponível temporariamente. Tente mais
# tarde." de forma intermitente. Nesses casos, retenta a pesquisa/solicitação
# em vez de marcar erro. Erros permanentes (sem procuração, CNPJ inválido) NÃO
# são retentados.
ORQ_SOLICITAR_TENTATIVAS = 3
ORQ_SOLICITAR_BACKOFF_SEGUNDOS = 8

# Status da fila (pjdocs_sol_baixa_arquivos_status) usados pelo orquestrador
ST_CRIANDO = 2                                  # Criando solicitações
ST_AGUARDANDO_DL = 3                            # Aguardando downloads
ST_EFETUANDO_DL = 4                             # Efetuando downloads
ST_SUCESSO = 5                                  # Finalizado com sucesso
ST_ERRO = 6                                     # Finalizado com erro
ST_SEM_EVENTOS = 8                              # Sem eventos no período
ST_SEM_PROCURACAO = 12                          # Sem procuração p/ o CNPJ
ST_PARCIAL = 21                                 # Parcialmente Completo (ReceitaBX)

# Status "concluídos" — nada mais a baixar. A limpeza do download_bx só apaga os
# arquivos de um CNPJ quando TODAS as suas linhas estão aqui (e ao menos uma é
# sucesso); qualquer outro status (1/2/3/4/6/21…) segura os arquivos p/ retry.
ST_CONCLUIDOS = frozenset({ST_SUCESSO, ST_SEM_EVENTOS, ST_SEM_PROCURACAO})
