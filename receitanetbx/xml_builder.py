"""
xml_builder.py — Construtores do XML de negócio (o que vai dentro de <entrada>).

Concentra a montagem dos XML exigidos pela API, evitando string repetida em
cada operação. Três formatos:

  - pesquisa               → raiz <pesquisa>, identificacao + campos diretos
  - pedido por período     → raiz <pedido>, identificacao + <pesquisa> (Rota A)
  - pedido por IDs         → raiz <pedido>, identificacao + <arquivos>  (Rota B)

Todos os valores são escapados (aspas incluídas) para não quebrar o XML.
"""

from html import escape

from .models import Identificacao

_PROLOGO = '<?xml version="1.0" encoding="UTF-8"?>'


def _attr(nome: str, valor) -> str:
    """Renderiza um atributo XML escapado: nome="valor"."""
    return f'{nome}="{escape(str(valor), quote=True)}"'


def _identificacao(ident: Identificacao) -> str:
    """Monta o elemento <identificacao/> a partir do dataclass."""
    partes = [
        _attr("perfil", ident.perfil),
        _attr("sistema", ident.sistema),
        _attr("tipoarquivo", ident.tipoarquivo),
        _attr("tipopesquisa", ident.tipopesquisa),
    ]
    # nirepresentado/tiponirepresentado só existem no perfil Procurador
    if ident.nirepresentado:
        partes.append(_attr("nirepresentado", ident.nirepresentado))
        partes.append(_attr("tiponirepresentado", ident.tiponirepresentado or "cnpj"))
    return f'<identificacao {" ".join(partes)}/>'


def _campos_periodo(data_ini: str, data_fim: str, scfg: dict = None) -> str:
    """Campos de período + campos_extra do sistema (nomes exatos por sistema).

    Os NOMES de campo variam entre sistemas (confirmado na Receita): ECF/ECD/
    PISCOFINS usam "Data de início"/"Data de fim"; ICMS usa "Data Inicio"/"Data
    Fim" (sem "de", sem acento) + os checkboxes booleano ("V")."""
    scfg = scfg or {}
    nome_ini = scfg.get("campo_data_ini", "Data de início")
    nome_fim = scfg.get("campo_data_fim", "Data de fim")
    partes = [
        f'<campo nome="{escape(nome_ini, quote=True)}" valor="{escape(data_ini, quote=True)}"/>',
        f'<campo nome="{escape(nome_fim, quote=True)}" valor="{escape(data_fim, quote=True)}"/>',
    ]
    for nome, valor in scfg.get("campos_extra", []):
        partes.append(
            f'<campo nome="{escape(nome, quote=True)}" valor="{escape(str(valor), quote=True)}"/>'
        )
    return "".join(partes)


def pesquisa(ident: Identificacao, data_ini: str, data_fim: str, scfg: dict = None) -> str:
    """XML de PesquisarArquivos: <pesquisa> com identificacao + campos diretos."""
    return (
        f"{_PROLOGO}<pesquisa>"
        f"{_identificacao(ident)}{_campos_periodo(data_ini, data_fim, scfg)}"
        f"</pesquisa>"
    )


def pedido_por_periodo(ident: Identificacao, data_ini: str, data_fim: str,
                       scfg: dict = None) -> str:
    """XML da Rota A: <pedido> com <pesquisa> aninhada (solicita por critério)."""
    return (
        f"{_PROLOGO}<pedido>"
        f"{_identificacao(ident)}"
        f"<pesquisa>{_campos_periodo(data_ini, data_fim, scfg)}</pesquisa>"
        f"</pedido>"
    )


def pedido_por_ids(ident: Identificacao, ids) -> str:
    """XML da Rota B: <pedido> com <arquivos> (solicita IDs específicos)."""
    arquivos = "".join(f'<arquivo {_attr("id", i)}/>' for i in ids)
    return (
        f"{_PROLOGO}<pedido>"
        f"{_identificacao(ident)}<arquivos>{arquivos}</arquivos>"
        f"</pedido>"
    )
