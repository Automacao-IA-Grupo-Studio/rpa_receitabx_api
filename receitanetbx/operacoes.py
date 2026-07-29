"""
operacoes.py — Operações de alto nível da API (pesquisar / solicitar).

Combina xml_builder + soap_client e devolve resultados estruturados (models),
sem imprimir nada. A camada de apresentação (main.py) decide como exibir.

  - pesquisar             → PesquisarArquivos (não gera pedido)
  - solicitar_por_periodo → SolicitarArquivos, Rota A (gera pedido real)
  - solicitar_por_ids     → SolicitarArquivos, Rota B (gera pedido real)
"""

import xml.etree.ElementTree as ET

from . import config, soap_client, xml_builder
from .models import Identificacao, ResultadoPedido, ResultadoPesquisa


def _extrair_ids(saida: str) -> list:
    """Extrai os IDs de <arquivo id="..."/> do XML de saída da pesquisa."""
    if not saida:
        return []
    try:
        root = ET.fromstring(saida)
    except ET.ParseError:
        return []
    return [
        el.get("id")
        for el in root.iter()
        if el.tag.split("}")[-1] == "arquivo" and el.get("id")
    ]


def _extrair_numero_pedido(saida: str):
    """Extrai o id de <retornopedido id="..."> do XML de saída da solicitação."""
    if not saida:
        return None
    try:
        root = ET.fromstring(saida)
    except ET.ParseError:
        return None
    if root.tag.split("}")[-1] == "retornopedido":
        return root.get("id")
    return None


def extrair_mensagem(saida: str):
    """Texto de <mensagem> do XML de retorno da Receita (ou None se não houver)."""
    if not saida:
        return None
    try:
        root = ET.fromstring(saida)
    except ET.ParseError:
        return None
    for el in root.iter():
        if el.tag.split("}")[-1] == "mensagem" and (el.text or "").strip():
            return el.text.strip()
    return None


# Mensagens técnicas da Receita → texto curto e claro para o usuário final
# (coluna etapa_erro do AUTOMATAX). A 1ª chave que casar (substring, minúsculas)
# vence, então as mais específicas vêm primeiro.
_MENSAGENS_AMIGAVEIS = (
    ("procuração eletrônica", "Sem procuração eletrônica para este CNPJ na Receita."),
    ("critério de pesquisa", "Documento precisa ser solicitado pela lista de arquivos."),
    ("indisponível", "Serviço da Receita indisponível no momento. Tente mais tarde."),
    ("tente mais tarde", "Serviço da Receita indisponível no momento. Tente mais tarde."),
    ("cnpj inválido", "CNPJ inválido."),
    ("procuração", "Sem procuração para o CNPJ solicitado."),
)


def mensagem_amigavel(saida: str,
                      padrao: str = "Solicitação recusada pela Receita.") -> str:
    """Converte o retorno XML da Receita numa frase curta para o usuário final.

    Sem <mensagem> reconhecível, cai no ``padrao``. Havendo mensagem mas sem
    mapeamento, usa a própria mensagem da Receita (já em português), aparada."""
    msg = extrair_mensagem(saida)
    if not msg:
        return padrao
    low = msg.lower()
    for chave, amigavel in _MENSAGENS_AMIGAVEIS:
        if chave in low:
            return amigavel
    return msg if len(msg) <= 180 else msg[:177] + "..."


def _identificacao(nirepresentado, perfil, tipo_ni, scfg=None) -> Identificacao:
    """Monta a identificação conforme o perfil desejado (e a config do sistema)."""
    if perfil == "Contribuinte":
        return Identificacao.contribuinte(scfg=scfg)
    return Identificacao.procurador(nirepresentado, tipo_ni, scfg=scfg)


def pesquisar(
    nirepresentado: str = None,
    perfil: str = config.PERFIL_PADRAO,
    tipo_ni: str = config.TIPO_NI_PADRAO,
    data_ini: str = None,
    data_fim: str = None,
    scfg: dict = None,
) -> ResultadoPesquisa:
    """Pesquisa arquivos disponíveis (retorna apenas os IDs). Não gera pedido.

    ``scfg``: config do sistema (define sistema/tipoarquivo/tipopesquisa/campos e
    a data inicial). Sem scfg, usa os padrões globais (ECF, retrocompatível)."""
    scfg = scfg or config.sistema_cfg("ecf")
    data_ini = data_ini or scfg.get("data_ini", config.DATA_INI_PADRAO)
    data_fim = data_fim or config.data_fim_hoje()
    ident = _identificacao(nirepresentado, perfil, tipo_ni, scfg)
    xml = xml_builder.pesquisa(ident, data_ini, data_fim, scfg)
    retorno, saida, status = soap_client.chamar("PesquisarArquivos", xml)
    return ResultadoPesquisa(retorno, saida, status, _extrair_ids(saida))


def solicitar_por_periodo(
    nirepresentado: str,
    perfil: str = config.PERFIL_PADRAO,
    tipo_ni: str = config.TIPO_NI_PADRAO,
    data_ini: str = None,
    data_fim: str = None,
    scfg: dict = None,
) -> ResultadoPedido:
    """Rota A — solicita por critério/período. GERA PEDIDO REAL na Receita."""
    scfg = scfg or config.sistema_cfg("ecf")
    data_ini = data_ini or scfg.get("data_ini", config.DATA_INI_PADRAO)
    data_fim = data_fim or config.data_fim_hoje()
    ident = _identificacao(nirepresentado, perfil, tipo_ni, scfg)
    xml = xml_builder.pedido_por_periodo(ident, data_ini, data_fim, scfg)
    retorno, saida, status = soap_client.chamar("SolicitarArquivos", xml)
    return ResultadoPedido(retorno, saida, status, _extrair_numero_pedido(saida))


def solicitar_por_ids(
    nirepresentado: str,
    ids,
    perfil: str = config.PERFIL_PADRAO,
    tipo_ni: str = config.TIPO_NI_PADRAO,
    scfg: dict = None,
) -> ResultadoPedido:
    """Rota B — solicita IDs específicos. GERA PEDIDO REAL na Receita.

    ``scfg``: config do sistema (define sistema/tipoarquivo/tipopesquisa na
    identificação). Necessário p/ ECD/ICMS, que só solicitam por lista de
    arquivos. Sem scfg, usa os padrões globais (ECF, retrocompatível)."""
    ident = _identificacao(nirepresentado, perfil, tipo_ni, scfg)
    xml = xml_builder.pedido_por_ids(ident, ids)
    retorno, saida, status = soap_client.chamar("SolicitarArquivos", xml)
    return ResultadoPedido(retorno, saida, status, _extrair_numero_pedido(saida))
