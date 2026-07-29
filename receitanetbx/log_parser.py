"""
log_parser.py — Leitura robusta dos logs JSON do serviço.

Dois cuidados que causaram bugs no passado e são tratados aqui:

  1. Encoding Latin-1 (ISO-8859-1), NÃO UTF-8. Ler como UTF-8 corrompe acentos
     ("Data Início" → "Data In�cio") e quebra o matching de atributos.
  2. O JSON pode vir quebrado em várias linhas, inclusive no meio de palavras.
     Não dá para ler linha-a-linha: reconstruímos os objetos contando chaves
     { } e descartando quebras cruas dentro de strings.

Produz objetos ArquivoSped já deduplicados por hash. A chave hash (MD5) é o
elo entre o pedidos-log (atributos) e o download-log (caminho físico).
"""

import json
import unicodedata
from pathlib import Path

from . import config
from .models import ArquivoSped


def _norm(txt: str) -> str:
    """Minúsculo e sem acento — casa nomes de atributos de forma robusta."""
    txt = (txt or "").strip().lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", txt)
        if unicodedata.category(c) != "Mn"
    )


def _iter_json_objs(texto: str):
    """Gera os objetos JSON de nível superior de um texto, mesmo quebrados em
    várias linhas (quebras cruas dentro de strings são descartadas)."""
    depth = 0
    buf = []
    in_str = False
    esc = False
    for ch in texto:
        if in_str:
            if esc:
                buf.append(ch); esc = False
            elif ch == "\\":
                buf.append(ch); esc = True
            elif ch == '"':
                buf.append(ch); in_str = False
            elif ch in "\n\r":
                pass  # quebra crua dentro de string → artificial, descarta
            else:
                buf.append(ch)
            continue
        if ch == '"':
            in_str = True; buf.append(ch); continue
        if ch == "{":
            if depth == 0:
                buf = []
            depth += 1
            buf.append(ch)
        elif ch == "}":
            depth -= 1
            buf.append(ch)
            if depth == 0:
                try:
                    yield json.loads("".join(buf))
                except json.JSONDecodeError:
                    pass
                buf = []
        elif depth > 0:
            buf.append(ch)


def _attrs_to_dict(arquivo: dict) -> dict:
    """Lista de atributos → dict {nome_normalizado: valor}."""
    return {
        _norm(at.get("nome", "")): (at.get("valor") or "").strip()
        for at in arquivo.get("atributos", [])
    }


def _eh_retificadora(attrs: dict) -> bool:
    """Trata os dois formatos de campo entre sistemas:
      - ECF:           'retificadora' = F (original) / V (retificadora)
      - EFD/PISCOFINS: 'situacao'     = Original / Retificadora
    """
    r = attrs.get("retificadora")
    if r is not None:
        return r.strip().upper() == "V"
    s = attrs.get("situacao")
    if s is not None:
        return s.strip().lower().startswith("retific")
    return False


def _ler_texto(caminho: Path) -> str:
    """Lê o arquivo em Latin-1 (obrigatório para os logs do serviço)."""
    return caminho.read_text(encoding="latin-1")


def carregar_pedidos(caminho: Path, cnpj_filtro: str = None,
                     sistema: str = None, attr_contribuinte: str = "Contribuinte",
                     attr_situacao: str = None) -> dict:
    """Lê o pedidos-log → dict {hash: ArquivoSped} (deduplicado por hash).

    Filtra por ``sistema`` (padrão SISTEMA_ALVO) e Tipo=TIPO_ALVO (ignora
    Recibo e ids -REC). Se cnpj_filtro for informado, mantém só o contribuinte.

    ``attr_contribuinte``: nome do atributo que traz o CNPJ do arquivo. ECF e
    PISCOFINS usam "Contribuinte"; ECD e ICMS usam "CNPJ" (trazem filiais/
    estabelecimentos, cada um com seu CNPJ).

    ``attr_situacao``: nome do atributo com a Situação SPED (só o ECD tem). Seu
    valor é apenas GUARDADO em ArquivoSped.situacao — a escolha da versão a
    manter por período é da regra (retificadora.aplicar_regra_situacao), não
    daqui, para que todas as versões sejam contadas como baixadas."""
    caminho = Path(caminho)
    sistema = sistema or config.SISTEMA_ALVO
    chave_contrib = _norm(attr_contribuinte)
    chave_sit = _norm(attr_situacao) if attr_situacao else None
    registros = {}
    if not caminho.exists():
        print(f"[aviso] pedidos-log não encontrado: {caminho}")
        return registros

    for ped in _iter_json_objs(_ler_texto(caminho)):
        if ped.get("sistema") != sistema:
            continue
        for arq in ped.get("arquivos", []):
            if str(arq.get("id", "")).endswith("-REC"):
                continue  # recibo
            attrs = _attrs_to_dict(arq)
            tipo = attrs.get("tipo", config.TIPO_ALVO)  # ECF não tem "Tipo"
            if tipo and tipo != config.TIPO_ALVO:
                continue
            # Situação SPED (só ECD): guardamos o valor BRUTO. Todas as versões
            # ficam no parse (contam como baixadas p/ o pedido fechar); a regra
            # decide depois qual manter por período.
            situacao = attrs.get(chave_sit, "") if chave_sit else ""
            contrib = attrs.get(chave_contrib, "")
            if cnpj_filtro and contrib != cnpj_filtro:
                continue
            h = (arq.get("hash") or "").strip()
            if not h:
                continue
            registros[h] = ArquivoSped(
                hash=h,
                id=arq.get("id"),
                contribuinte=contrib,
                data_ini=attrs.get("data inicio", "")[:10],
                data_fim=attrs.get("data fim", "")[:10],
                transmissao=attrs.get("transmissao", ""),
                retificadora=_eh_retificadora(attrs),
                scp=attrs.get("scp", ""),  # CNPJ da SCP (vazio = sem SCP)
                situacao=situacao,
                tamanho=arq.get("tamanho"),
            )
    return registros


def carregar_downloads(caminho: Path) -> dict:
    """Lê o download-log → dict {hash: {'caminho', 'nome'}} (deduplicado)."""
    caminho = Path(caminho)
    caminhos = {}
    if not caminho.exists():
        print(f"[aviso] download-log não encontrado: {caminho}")
        return caminhos

    for d in _iter_json_objs(_ler_texto(caminho)):
        h = (d.get("hash") or "").strip()
        cam = d.get("caminhodownload")
        if h and cam:
            caminhos[h] = {"caminho": cam, "nome": d.get("nome", "")}
    return caminhos
