"""
log_setup.py — Log de execução da aplicação (orquestrador).

Grava um arquivo por dia em ``logs/orquestrar-AAAAMMDD.log`` (com timestamp em
cada linha), sem alterar o que aparece no console. Serve para acompanhar um
lote ao vivo (``Get-Content -Wait``) e revisar depois.

NÃO confundir com os logs do SERVIÇO ReceitanetBX (bx_temp/<profile>/logs),
que têm outro formato e outra finalidade.

O logger raiz da aplicação chama-se "bx_api"; loggers filhos (ex.: "bx_api.db"
no db_handler) propagam para cá, então erros de banco também caem no arquivo.
"""

import logging
from datetime import datetime

from . import config

# Mapeia os "níveis" textuais usados no on_evento do orquestrador para os
# níveis padrão do logging.
_NIVEIS = {"ERRO": logging.ERROR, "AVISO": logging.WARNING}


def nivel_para_logging(nivel: str) -> int:
    return _NIVEIS.get(nivel, logging.INFO)


def configurar(nome: str = "orquestrar"):
    """Configura o logger 'bx_api' com um FileHandler diário.

    Returns:
        (logger, caminho_do_arquivo)
    """
    config.LOG_APP_DIR.mkdir(parents=True, exist_ok=True)
    arquivo = config.LOG_APP_DIR / f"{nome}-{datetime.now():%Y%m%d}.log"

    logger = logging.getLogger("bx_api")
    logger.setLevel(logging.INFO)
    # idempotente: limpa handlers de chamadas anteriores no mesmo processo
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()

    fh = logging.FileHandler(arquivo, encoding="utf-8")
    fh.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                          "%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(fh)
    logger.propagate = False  # não duplica no root
    return logger, arquivo
