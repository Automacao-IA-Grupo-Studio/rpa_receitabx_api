"""
retificadora.py — A regra de negócio central (isolada e testável).

Regra:
  Para cada (contribuinte + SCP + período), sendo período = (Data Início, Data Fim):
    - Se existe retificadora no grupo, mantém a retificadora mais recente
      (maior data de Transmissão) e descarta as demais versões do período.
    - Se só existem originais, mantém a de maior Transmissão.

O SCP (uma "CNPJ dentro da CNPJ") entra na chave: cada SCP é comparado à parte
do contribuinte principal (SCP vazio) e dos demais SCPs — a mesma disputa
retificadora × original, mas partindo do próprio SCP. Sem SCP, o comportamento
é o de antes.

Todos os períodos/anos são preservados — a regra só desempata versões DENTRO
de um mesmo período. Nunca descarta um ano inteiro.

Para o ECD existe uma variante — ``aplicar_regra_situacao`` — que NÃO disputa
versão por período: mantém todas as escriturações, exceto a SUBSTITUÍDA (o ECD
pode ter vários SPEDs válidos do mesmo período; ver docstring abaixo).
"""

import unicodedata

from .models import ArquivoSped


def _norm(txt: str) -> str:
    """Minúsculo e sem acento (igual ao log_parser._norm)."""
    txt = (txt or "").strip().lower()
    return "".join(c for c in unicodedata.normalize("NFD", txt)
                   if unicodedata.category(c) != "Mn")


def _e_substituida(arq) -> bool:
    """True se a Situação SPED for SUBSTITUÍDA (foi trocada por outra versão)."""
    return "substitu" in _norm(getattr(arq, "situacao", ""))


def aplicar_regra_situacao(arquivos) -> tuple:
    """ECD: mantém TODAS as escriturações, exceto a SUBSTITUÍDA.

    Diferente do ECF/PISCOFINS, o ECD pode ter VÁRIOS SPEDs válidos de um mesmo
    período (raro, mas acontece) — e todos precisam ser baixados/arquivados. Não
    há disputa por período nem escolha de "melhor versão": a única exclusão é a
    Situação SPED "SUBSTITUÍDA", pois essa versão foi trocada por outra e não
    deve ir para a rede.

    As substituídas vão para ``descartar`` e continuam contando como baixadas (o
    pedido fecha), só não são arquivadas.

    Returns (manter, descartar): duas listas de ArquivoSped.
    """
    if isinstance(arquivos, dict):
        arquivos = arquivos.values()

    manter, descartar = [], []
    for arq in arquivos:
        (descartar if _e_substituida(arq) else manter).append(arq)

    return manter, descartar


def aplicar_regra(arquivos) -> tuple:
    """Decide manter/descartar por (contribuinte, data_ini, data_fim).

    Args:
        arquivos: iterável de ArquivoSped (ou o dict {hash: ArquivoSped}).

    Returns:
        (manter, descartar): duas listas de ArquivoSped.
    """
    if isinstance(arquivos, dict):
        arquivos = arquivos.values()

    grupos = {}
    for arq in arquivos:
        grupos.setdefault(arq.periodo, []).append(arq)

    manter, descartar = [], []
    for itens in grupos.values():
        retifs = [i for i in itens if i.retificadora]
        candidatos = retifs if retifs else itens
        # mais recente por data de Transmissão (string ISO ordena corretamente)
        escolhido = max(candidatos, key=lambda i: i.transmissao)
        for i in itens:
            (manter if i is escolhido else descartar).append(i)

    return manter, descartar
