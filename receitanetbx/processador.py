"""
processador.py — Orquestra a etapa 3: ler logs → aplicar regra → mover.

Fluxo:
  1. carregar_pedidos  (atributos, por hash)
  2. carregar_downloads (caminho físico, por hash)
  3. cruzar por hash (preenche o caminho em cada ArquivoSped)
  4. aplicar_regra (retificadora) → manter / descartar
  5. [opcional] mover os MANTIDOS para a rede, com controle de duplicados

As funções devolvem dados (models); a exibição fica na camada CLI (main.py).
"""

import re
import shutil
from datetime import date
from pathlib import Path

from . import config, log_parser, retificadora
from .models import ResumoMovimentacao


def datas_disponiveis(log_base=None) -> list:
    """Datas AAAAMMDD que têm pedidos/download-log no log_base (ordenadas).

    Útil para processar TODO o histórico baixado (o backlog cai em vários dias).
    """
    import re
    if log_base is None:
        dirs = [config.LOG_PEDIDOS, config.LOG_DOWNLOAD]
    else:
        dirs = [Path(log_base) / "pedidos", Path(log_base) / "download"]
    datas = set()
    for d in dirs:
        if Path(d).exists():
            for f in Path(d).glob("*.log"):
                m = re.search(r"(\d{8})", f.name)
                if m:
                    datas.add(m.group(1))
    return sorted(datas)


def caminhos_dos_logs(data: str = None, log_base=None) -> tuple:
    """Devolve (pedidos_log, download_log) para a data AAAAMMDD (padrão hoje).

    ``log_base`` permite apontar para a pasta de logs de um procurador
    específico (BX_TEMP_BASE/<profile_key>/logs); se omitido, usa a base padrão.
    """
    data = data or date.today().strftime("%Y%m%d")
    if log_base is None:
        ped_dir, dl_dir = config.LOG_PEDIDOS, config.LOG_DOWNLOAD
    else:
        ped_dir, dl_dir = Path(log_base) / "pedidos", Path(log_base) / "download"
    ped = ped_dir / f"pedidos-{data}.log"
    dl = dl_dir / f"download-{data}.log"
    return ped, dl


def carregar(data: str = None, cnpj: str = None, log_base=None,
             sistema: str = None, attr_contribuinte: str = "Contribuinte",
             aplicar_regra_retif: bool = True, attr_situacao: str = None,
             regra_situacao: bool = False) -> tuple:
    """Carrega os logs de UMA data, cruza por hash e aplica a regra.

    ``sistema``: nome exato do sistema a filtrar (padrão SISTEMA_ALVO = ECF).

    Returns:
        (manter, descartar, total_pedidos, total_downloads)
        As listas contêm ArquivoSped já com o caminho físico preenchido.
    """
    data = data or date.today().strftime("%Y%m%d")
    return carregar_janela([data], cnpj, log_base, sistema,
                           attr_contribuinte=attr_contribuinte,
                           aplicar_regra_retif=aplicar_regra_retif,
                           attr_situacao=attr_situacao,
                           regra_situacao=regra_situacao)


def carregar_janela(datas, cnpj: str = None, log_base=None,
                    sistema: str = None, apenas_baixados: bool = False,
                    attr_contribuinte: str = "Contribuinte",
                    aplicar_regra_retif: bool = True, attr_situacao: str = None,
                    regra_situacao: bool = False) -> tuple:
    """Como ``carregar``, mas cruza pedidos+downloads de VÁRIAS datas.

    Necessário quando o serviço baixa num dia diferente do da solicitação
    (ex.: espera cruzando a meia-noite): o pedido fica no log de um dia e o
    download no de outro. Junta tudo por hash antes de aplicar a regra.

    Args:
        datas: iterável de strings no formato AAAAMMDD.
        sistema: nome exato do sistema a filtrar (padrão SISTEMA_ALVO = ECF).
        apenas_baixados: se True, aplica a regra SÓ entre os arquivos que
            realmente baixaram (têm caminho). Evita "manter" uma versão ainda
            não baixada e descartar uma já baixada — essencial ao processar um
            backlog parcialmente baixado.
        attr_contribuinte: atributo do log que traz o CNPJ (ECF/PISCOFINS=
            "Contribuinte"; ECD/ICMS="CNPJ").
        aplicar_regra_retif: ECF/PISCOFINS — regra de retificadora (por período).
        regra_situacao: ECD — mantém tudo, exceto a SUBSTITUÍDA (Situação SPED).
        (nenhum dos dois: ICMS — mantém TODOS os arquivos baixados.)
        Seja qual for a regra, o que NÃO é mantido vai para ``descartar`` e
        continua contando como baixado (o pedido fecha), só não vai pra rede.
    """
    pedidos, caminhos = {}, {}
    for data in datas:
        ped_path, dl_path = caminhos_dos_logs(data, log_base)
        pedidos.update(log_parser.carregar_pedidos(
            ped_path, cnpj, sistema, attr_contribuinte,
            attr_situacao=attr_situacao))
        caminhos.update(log_parser.carregar_downloads(dl_path))

    # cruzamento por hash: enriquece cada arquivo com caminho/nome físicos
    for h, arq in pedidos.items():
        info = caminhos.get(h)
        if info:
            arq.caminho = info["caminho"]
            arq.nome = info["nome"]

    total_pedidos = len(pedidos)
    if apenas_baixados:
        pedidos = {h: a for h, a in pedidos.items() if a.caminho}

    if aplicar_regra_retif:
        manter, descartar = retificadora.aplicar_regra(pedidos)          # ECF/PIS
    elif regra_situacao:
        manter, descartar = retificadora.aplicar_regra_situacao(pedidos)  # ECD
    else:
        # ICMS: mantém tudo (sem disputa de versão)
        manter = list(pedidos.values() if isinstance(pedidos, dict) else pedidos)
        descartar = []
    return manter, descartar, total_pedidos, len(caminhos)


# ── controle de duplicados (hashes já movidos) ───────────────────────────
def _carregar_controle() -> set:
    if not config.CONTROLE.exists():
        return set()
    with open(config.CONTROLE, encoding="utf-8") as f:
        return {ln.strip() for ln in f if ln.strip()}


def _registrar_movido(h: str) -> None:
    with open(config.CONTROLE, "a", encoding="utf-8") as f:
        f.write(h + "\n")


def _destino_dir(arq, subpasta: str, cnpj_override: str = None) -> Path:
    """Pasta de destino na rede: DEST_REDE/{cnpj}/RECEITABX/<subpasta>.

    Arquivos de SCP (uma "CNPJ dentro da CNPJ") vão numa subpasta ``SCP``
    separada, dentro da pasta do documento — ex.: .../RECEITABX/ECF/SCP/. Os do
    contribuinte principal (sem SCP) ficam direto em .../RECEITABX/ECF/."""
    cnpj = cnpj_override or arq.contribuinte
    base = config.DEST_REDE / cnpj / "RECEITABX" / subpasta
    return base / "SCP" if getattr(arq, "scp", "") else base


def mover_arquivos(manter, on_evento=None, subpasta: str = "ECF",
                   cnpj_override: str = None) -> ResumoMovimentacao:
    """Copia os arquivos MANTIDOS para DEST_REDE/{cnpj}/RECEITABX/<subpasta>.

    Usa o CNPJ do Contribuinte (não o do certificado). Deduplica por hash.
    Arquivos de SCP vão para a subpasta ``SCP`` do documento (ver _destino_dir).

    Args:
        manter: lista de ArquivoSped (com caminho físico preenchido).
        on_evento: callback opcional (nivel, mensagem) para log da CLI.
        subpasta: subpasta do sistema na rede (ECF/PISCOFINS/ECD/ICMS).
        cnpj_override: se informado, grava TODOS os arquivos na pasta deste CNPJ
            (usado pelo ICMS: filiais vão todas na pasta do cliente, não na do
            estabelecimento). Sem override, usa o CNPJ de cada arquivo.
    """
    def log(nivel, msg):
        if on_evento:
            on_evento(nivel, msg)

    ja_movidos = _carregar_controle()
    resumo = ResumoMovimentacao()

    for arq in sorted(manter, key=lambda x: (x.contribuinte, x.data_fim)):
        origem = arq.caminho
        if not origem:
            log("ERRO", f"sem caminho no download-log: {arq.data_fim}")
            resumo.erros += 1
            continue

        nome = Path(origem).name
        if arq.hash in ja_movidos:
            log("PULADO", f"já movido antes: {nome}")
            resumo.pulados += 1
            continue

        if not Path(origem).exists():
            log("ERRO", f"arquivo não existe no disco: {origem}")
            resumo.erros += 1
            continue

        destino_dir = _destino_dir(arq, subpasta, cnpj_override)
        try:
            destino_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origem, destino_dir / nome)
            _registrar_movido(arq.hash)
            log("OK", f"{nome} -> {destino_dir}")
            resumo.copiados += 1
        except Exception as e:  # noqa: BLE001 — reporta e segue para os demais
            log("ERRO", f"falha ao copiar {nome}: {e}")
            resumo.erros += 1

    return resumo


# ── limpeza do download_bx (libera espaço após concluir) ──────────────────
def _fmt_tam(n: int) -> str:
    """Formata bytes como MB/GB legível."""
    mb = n / (1024 * 1024)
    return f"{mb/1024:.2f} GB" if mb >= 1024 else f"{mb:.1f} MB"


# CNPJ (14 díg.) no nome do arquivo baixado. Vem SEMPRE antes do hash, em todos
# os formatos do serviço: "<cnpj>-...-SPED-ECD/EFD", "SPEDECF-<cnpj>-...",
# "PISCOFINS_<dt>_<dt>_<cnpj>_...". A borda (?<!\d)/(?!\d) evita casar datas (8
# díg.), o CPF do contador (11) ou trechos numéricos do hash.
_RX_CNPJ = re.compile(r"(?<!\d)\d{14}(?!\d)")


def _cnpj_do_nome(nome: str):
    m = _RX_CNPJ.search(nome or "")
    return m.group(0) if m else None


def limpar_arquivos_baixados(profile_key, cnpjs_liberados, dry_run: bool = True,
                             on_evento=None) -> dict:
    """Apaga do ``download_bx/<profile>`` os arquivos SPED já baixados cujos
    CNPJs (base de 8 díg.) constam em ``cnpjs_liberados`` — linhas concluídas.

    Como ``mover_arquivos`` COPIA para a rede (não move), os originais ficam
    acumulando aqui; esta função os remove com segurança:
      - só apaga o que o download-log referencia (nunca "adivinha" arquivos);
      - só dentro da pasta do profile (jamais fora do download_bx);
      - NUNCA toca em logs nem em arquivos de CNPJ ainda pendente/parcial;
      - deleta TODAS as versões baixadas do CNPJ liberado (a vigente já está na
        rede; as descartadas/substituídas não serão arquivadas — são lixo local).

    O dono de cada arquivo é lido do PRÓPRIO NOME (o CNPJ vem no nome), então
    não depende do pedidos-log continuar em disco (que rotaciona).

    ``cnpjs_liberados``: set de CNPJ[:8] liberados. ``dry_run``: só relata.
    Returns dict {arquivos, bytes, apagados, erros}.
    """
    def log(nivel, msg):
        if on_evento:
            on_evento(nivel, msg)

    base = config.base_procurador(profile_key)
    lb = base / "logs"
    vazio = {"arquivos": 0, "bytes": 0, "apagados": 0, "erros": 0}
    if not lb.exists():
        return vazio
    try:
        base_res = base.resolve()
    except OSError:
        return vazio

    caminhos = set()
    for dl in (lb / "download").glob("download-*.log"):
        for v in log_parser.carregar_downloads(dl).values():
            if v.get("caminho"):
                caminhos.add(v["caminho"])

    alvos, sem_cnpj = {}, 0
    for cam in caminhos:
        p = Path(cam)
        cnpj = _cnpj_do_nome(p.name)
        if not cnpj:
            sem_cnpj += 1
            continue
        if cnpj[:8] not in cnpjs_liberados:
            continue
        try:
            pres = p.resolve()
        except OSError:
            continue
        # blindagem: só arquivo real, dentro do profile, nunca em logs
        if base_res not in pres.parents or "logs" in pres.parts:
            continue
        if p.is_file():
            alvos[str(pres)] = p.stat().st_size

    total = sum(alvos.values())
    apagados = erros = 0
    if dry_run:
        log("INFO", f"[{profile_key}] {len(alvos)} arquivo(s) liberável(is) "
                    f"({_fmt_tam(total)}) — SIMULAÇÃO, nada apagado")
    else:
        for caminho in alvos:
            try:
                Path(caminho).unlink()
                apagados += 1
            except OSError as e:
                erros += 1
                log("ERRO", f"falha ao apagar {caminho}: {e}")
        log("INFO", f"[{profile_key}] apagados {apagados}/{len(alvos)} "
                    f"({_fmt_tam(total)} liberados), {erros} erro(s)")
    if sem_cnpj:
        log("AVISO", f"[{profile_key}] {sem_cnpj} arquivo(s) sem CNPJ no nome — mantidos")
    return {"arquivos": len(alvos), "bytes": total, "apagados": apagados, "erros": erros}
