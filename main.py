"""
main.py — CLI único da automação ReceitanetBX.

Ponto de entrada de todo o fluxo. Substitui os antigos scripts soltos
(rota_a.py, rota_b.py, pesquisa_proc.py, processar_log.py) por subcomandos:

  pesquisar   → PesquisarArquivos (lista IDs; não gera pedido)
  solicitar   → SolicitarArquivos (gera pedido real): Rota A (período) ou B (IDs)
  processar   → lê logs, aplica regra de retificadora e move para a rede
  orquestrar  → lote multi-certificado guiado pela fila do banco (AUTOMATAX)

Exemplos:
  py main.py pesquisar --cnpj 07906793000151
  py main.py solicitar --cnpj 12132146000170
  py main.py solicitar --cnpj 07906793000151 --ids 17842999 15706187
  py main.py processar 20260708 12132146000170
  py main.py processar 20260708 12132146000170 --mover
  py main.py orquestrar              # simula o lote (não toca em nada)
  py main.py orquestrar --executar   # executa de verdade

O modo simulação é o padrão em 'processar' e 'orquestrar'; a ação real é
explícita (--mover / --executar), por segurança.
"""

import argparse
import ctypes
import os
import subprocess
import sys
from datetime import date, datetime

from receitanetbx import config, log_setup, operacoes, orquestrador, processador

LINHA = "=" * 78


# ── Elevação para administrador (necessária p/ reconfigurar o serviço) ────
def _e_admin() -> bool:
    """True se o processo atual tem privilégios de administrador."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:  # noqa: BLE001 — fora do Windows / sem shell32
        return False


def _reexecutar_como_admin() -> int:
    """Relança o MESMO comando elevado (dispara o UAC). Retorna o código do
    ShellExecuteW (>32 = sucesso)."""
    script = os.path.abspath(sys.argv[0])
    # marca --elevado para não entrar em loop caso a elevação não "pegue"
    params = subprocess.list2cmdline([script] + sys.argv[1:] + ["--elevado"])
    return int(ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, os.getcwd(), 1))


def _garantir_admin(args) -> bool:
    """Garante privilégio de admin para ações que mexem no serviço.

    Returns True se pode prosseguir; False se o processo atual deve encerrar
    (porque delegou o trabalho a uma janela elevada, ou a elevação falhou).
    """
    if _e_admin():
        return True
    if getattr(args, "elevado", False):
        # já viemos de uma tentativa de elevação e ainda não somos admin
        print("ERRO: elevação não concedeu privilégios de administrador.")
        return False
    print("Este comando reconfigura o serviço e precisa de administrador.")
    print("Solicitando elevação (UAC)... o lote roda na janela elevada.")
    rc = _reexecutar_como_admin()
    if rc <= 32:
        print(f"ERRO: não foi possível elevar (UAC recusado? código {rc}).")
        print("Alternativa: abra o terminal como administrador e rode de novo.")
        return False
    print("Janela elevada iniciada. Acompanhe pelo log:")
    print(rf"  Get-Content .\logs\orquestrar-{date.today():%Y%m%d}.log -Wait -Tail 20")
    return False


# ── subcomando: pesquisar ────────────────────────────────────────────────
def cmd_pesquisar(args) -> int:
    print(LINHA)
    print(f"PESQUISAR — perfil {args.perfil}"
          + (f" | representado {args.cnpj}" if args.cnpj else ""))
    print(LINHA)
    res = operacoes.pesquisar(
        nirepresentado=args.cnpj, perfil=args.perfil,
        data_ini=args.data_ini, data_fim=args.data_fim,
    )
    print(f"HTTP status : {res.http_status}")
    print(f"retorno     : {res.retorno}")
    print(f"IDs ({len(res.ids)}) : {', '.join(res.ids) if res.ids else '(nenhum)'}")
    if not res.sucesso:
        print("\nsaida:")
        print(res.saida)
    return 0 if res.sucesso else 1


# ── subcomando: solicitar (Rota A período / Rota B ids) ──────────────────
def cmd_solicitar(args) -> int:
    rota = "B — por IDs" if args.ids else "A — por período"
    print(LINHA)
    print(f"SOLICITAR (Rota {rota}) — GERA PEDIDO REAL | representado {args.cnpj}")
    print(LINHA)

    if args.ids:
        res = operacoes.solicitar_por_ids(
            nirepresentado=args.cnpj, ids=args.ids, perfil=args.perfil,
        )
    else:
        res = operacoes.solicitar_por_periodo(
            nirepresentado=args.cnpj, perfil=args.perfil,
            data_ini=args.data_ini, data_fim=args.data_fim,
        )

    print(f"HTTP status : {res.http_status}")
    print(f"retorno     : {res.retorno}")
    if res.sucesso:
        print(f"\nNÚMERO DO PEDIDO: {res.numero_pedido}")
        print("O serviço fará o download no próximo ciclo (até 10 min).")
        print(f"Acompanhe em: {config.STATUS_URL}fila/")
    else:
        print("\nsaida:")
        print(res.saida)
    return 0 if res.sucesso else 1


# ── subcomando: processar (logs → regra → mover) ─────────────────────────
def _print_arquivos(titulo, itens):
    print(f"\n>>> {titulo} ({len(itens)}):")
    for i in sorted(itens, key=lambda x: (x.contribuinte, x.data_fim)):
        tag = "RETIF" if i.retificadora else "orig "
        print(f"  [{tag}] {i.contribuinte} {i.data_ini}..{i.data_fim} "
              f"transm={i.transmissao[:10]}")
        if i.caminho:
            print(f"         -> {i.caminho}")


def cmd_processar(args) -> int:
    cfg = config.sistema_cfg(args.sistema)
    log_base = config.log_base_procurador(args.profile) if args.profile else None

    if args.todas_datas:
        datas = processador.datas_disponiveis(log_base)
    else:
        datas = [args.data or date.today().strftime("%Y%m%d")]

    print(LINHA)
    print(f"PROCESSAR - sistema {args.sistema.upper()} ({cfg['sistema']}) -> pasta {cfg['subpasta']}")
    print(f"  perfil: {args.profile or '(base padrão)'} | datas: "
          + (f"{datas[0]}..{datas[-1]} ({len(datas)})" if datas else "(nenhuma)")
          + (f" | CNPJ {args.cnpj}" if args.cnpj else ""))
    print(LINHA)
    if not datas:
        print("Nenhum log encontrado para processar.")
        return 0

    manter, descartar, n_ped, n_dl = processador.carregar_janela(
        datas, cnpj=args.cnpj, log_base=log_base, sistema=cfg["sistema"],
        apenas_baixados=True, attr_contribuinte=cfg["attr_contribuinte"],
        aplicar_regra_retif=cfg["regra_retif"],
        attr_situacao=cfg.get("attr_situacao"),
        regra_situacao=cfg.get("regra_situacao", False))
    print(f"Arquivos únicos no pedidos-log ({cfg['sistema']}) : {n_ped}")
    print(f"Arquivos no download-log                          : {n_dl}")
    print("-" * 78)

    _print_arquivos("MANTER", manter)
    if not args.mover:
        _print_arquivos("DESCARTAR", descartar)

    print("\n" + LINHA)
    if args.mover:
        print(f"MOVENDO {len(manter)} arquivos MANTIDOS para RECEITABX/{cfg['subpasta']}...")
        print(LINHA)
        ev = lambda nivel, msg: print(f"  [{nivel}] {msg}")
        if cfg.get("pasta_cliente"):
            # ICMS: cada arquivo traz o CNPJ do estabelecimento (filial). Agrupa
            # pela base (8 díg.) e grava na pasta do CLIENTE (CNPJ do roster do
            # banco com a mesma base; sem correspondência, usa o próprio CNPJ).
            from database.db_handler import DBHandler
            base_para_cliente = {}
            for r in DBHandler().buscar_roster_ecf():
                base_para_cliente.setdefault(r["cnpj"][:8], r["cnpj"])
            grupos = {}
            for a in manter:
                cliente = base_para_cliente.get(a.contribuinte[:8], a.contribuinte)
                grupos.setdefault(cliente, []).append(a)
            copiados = pulados = erros = 0
            for cliente, arqs in grupos.items():
                r = processador.mover_arquivos(arqs, on_evento=ev,
                                               subpasta=cfg["subpasta"],
                                               cnpj_override=cliente)
                copiados += r.copiados; pulados += r.pulados; erros += r.erros
            print(f"\nResumo: {copiados} copiados, {pulados} pulados (já movidos), "
                  f"{erros} erros. ({len(grupos)} cliente(s))")
        else:
            resumo = processador.mover_arquivos(
                manter, on_evento=ev, subpasta=cfg["subpasta"])
            print(f"\nResumo: {resumo.copiados} copiados, "
                  f"{resumo.pulados} pulados (já movidos), {resumo.erros} erros.")
    else:
        print("SIMULAÇÃO — nada foi movido. Rode com --mover para copiar de verdade.")
        print(LINHA)
    return 0


# ── subcomando: orquestrar (lote multi-certificado via banco) ────────────
def cmd_orquestrar(args) -> int:
    # Execução real mexe no serviço (Program Files) → exige admin.
    if args.executar and not _garantir_admin(args):
        return 1

    logger, arquivo = log_setup.configurar("orquestrar")
    modo = "EXECUÇÃO REAL" if args.executar else "SIMULAÇÃO (dry-run)"
    alvo = "catch-up ECD (parciais)" if args.catchup else args.sistema
    print(LINHA)
    print(f"ORQUESTRAR — ReceitaBX ({alvo}) por certificado | {modo}")
    print(LINHA)
    if not args.executar:
        print("Nada será tocado (serviço, banco, Receita). Use --executar para valer.\n")
    logger.info("=" * 60)
    logger.info(f"INÍCIO — ORQUESTRAR ({modo})")

    def evento(nivel, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"  {ts} [{nivel}] {msg}", flush=True)
        logger.log(log_setup.nivel_para_logging(nivel), f"[{nivel}] {msg}")

    resumo = orquestrador.orquestrar(dry_run=not args.executar, on_evento=evento,
                                     somente_certs=args.cert, teto_min=args.teto,
                                     sistema=args.sistema, catchup=args.catchup,
                                     limpar=args.limpar)

    print("\n" + LINHA)
    print("RESUMO POR CERTIFICADO")
    print(LINHA)
    logger.info("RESUMO POR CERTIFICADO")
    if not resumo:
        print("(nenhuma solicitação pendente)")
        logger.info("(nenhuma solicitação pendente)")
    for id_cert, r in resumo.items():
        linha = f"id {id_cert:>3} | {r['company']:<24} | {r['n']:>3} sol. | {r['status']}"
        print(f"  {linha}")
        logger.info(linha)

    logger.info("FIM — ORQUESTRAR")
    print(f"\nLog salvo em: {arquivo}")
    return 0


# ── subcomando: limpar (libera espaço no download_bx) ────────────────────
def _fmt_tam(n: int) -> str:
    mb = n / (1024 * 1024)
    return f"{mb/1024:.2f} GB" if mb >= 1024 else f"{mb:.1f} MB"


def _rodar_limpeza(dry_run, somente_certs, evento) -> dict:
    tot = orquestrador.limpar_concluidos(
        dry_run=dry_run, somente_certs=somente_certs, on_evento=evento)
    acao = "liberável(is)" if dry_run else "apagado(s)"
    print(f"\nRESUMO: {tot['arquivos']} arquivo(s) {acao} ({_fmt_tam(tot['bytes'])}) "
          f"em {tot['profiles']} profile(s)"
          + (f" | {tot['erros']} erro(s)" if tot["erros"] else ""))
    return tot


def cmd_limpar(args) -> int:
    logger, arquivo = log_setup.configurar("limpar")
    modo = "APAGAR DE VERDADE" if args.executar else "SIMULAÇÃO (dry-run)"
    print(LINHA)
    print(f"LIMPAR download_bx — arquivos de linhas CONCLUÍDAS | {modo}")
    print(LINHA)
    if not args.executar:
        print("Nada será apagado. Use --executar para apagar de verdade.\n")
    logger.info(f"INÍCIO — LIMPAR ({modo})")

    def evento(nivel, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"  {ts} [{nivel}] {msg}", flush=True)
        logger.log(log_setup.nivel_para_logging(nivel), f"[{nivel}] {msg}")

    _rodar_limpeza(dry_run=not args.executar, somente_certs=args.cert, evento=evento)
    print(LINHA)
    logger.info("FIM — LIMPAR")
    print(f"\nLog salvo em: {arquivo}")
    return 0


# ── parser ───────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Automação de download de SPED (ReceitanetBX Serviço).",
    )
    sub = p.add_subparsers(dest="comando", required=True)

    # pesquisar
    sp = sub.add_parser("pesquisar", help="Lista IDs disponíveis (não gera pedido).")
    sp.add_argument("--cnpj", help="CNPJ do cliente representado (perfil Procurador).")
    sp.add_argument("--perfil", default=config.PERFIL_PADRAO,
                    choices=["Procurador", "Contribuinte"])
    sp.add_argument("--data-ini", default=config.DATA_INI_PADRAO)
    sp.add_argument("--data-fim", default=None, help="Padrão: hoje.")
    sp.set_defaults(func=cmd_pesquisar)

    # solicitar
    ss = sub.add_parser("solicitar", help="Gera pedido real (Rota A período ou B IDs).")
    ss.add_argument("--cnpj", required=True, help="CNPJ do cliente representado.")
    ss.add_argument("--ids", nargs="+", help="IDs específicos (ativa a Rota B).")
    ss.add_argument("--perfil", default=config.PERFIL_PADRAO,
                    choices=["Procurador", "Contribuinte"])
    ss.add_argument("--data-ini", default=config.DATA_INI_PADRAO)
    ss.add_argument("--data-fim", default=None, help="Padrão: hoje.")
    ss.set_defaults(func=cmd_solicitar)

    # processar
    pp = sub.add_parser("processar", help="Aplica a regra de retificadora e move.")
    pp.add_argument("data", nargs="?", default=None, help="Data AAAAMMDD (padrão hoje).")
    pp.add_argument("cnpj", nargs="?", default=None, help="Filtra um CNPJ (opcional).")
    pp.add_argument("--sistema", default="ecf", choices=list(config.SISTEMAS),
                    help="Sistema a processar (padrão ecf).")
    pp.add_argument("--profile", default=None,
                    help="Perfil/procurador (lê logs de bx_temp/<profile>/logs).")
    pp.add_argument("--todas-datas", dest="todas_datas", action="store_true",
                    help="Processa TODAS as datas de log do perfil (todo o histórico baixado).")
    pp.add_argument("--mover", action="store_true",
                    help="Copia de verdade (sem isto, apenas simula).")
    pp.set_defaults(func=cmd_processar)

    # orquestrar
    po = sub.add_parser("orquestrar",
                        help="Lote multi-certificado a partir da fila do banco.")
    po.add_argument("--executar", action="store_true",
                    help="Executa de verdade (reinicia serviço, solicita, "
                         "grava status). Sem isto, apenas simula o plano. "
                         "Pede elevação (UAC) automaticamente se necessário.")
    po.add_argument("--sistema", default="todos",
                    choices=["todos"] + list(config.SISTEMAS),
                    help="Documento(s) a orquestrar. Padrão 'todos' = ReceitaBX "
                         "completo (os 4 documentos) com status agregado na linha "
                         "tipo 5 (5/8/21/6). ecf/piscofins/ecd/icms = só aquele "
                         "documento p/ os mesmos CNPJs, sem escrever status.")
    po.add_argument("--cert", type=int, nargs="+", metavar="ID",
                    help="Processa SOMENTE estes certificados (id). Ex.: --cert 4")
    po.add_argument("--teto", type=int, metavar="MIN",
                    help="MODO DRENAGEM: janela longa (MIN minutos) por certificado, "
                         "sem corte por 'sem novidade'. P/ rodar de madrugada e "
                         "drenar o backlog. Ex.: --teto 120")
    po.add_argument("--catchup", action="store_true",
                    help="CATCH-UP DE ECD: reprocessa SÓ o ECD das linhas que ficaram "
                         "PARCIAIS (status 21), sem re-baixar os outros documentos, e "
                         "fecha em sucesso quando o ECD completar. O ECD demora horas "
                         "p/ a Receita disponibilizar — rode algumas horas após a "
                         "madrugada. Use com --executar --teto. Ignora --sistema.")
    po.add_argument("--limpar", action="store_true",
                    help="Ao TERMINAR cada certificado, confere na rede os arquivos "
                         "dos CNPJs concluídos e apaga as cópias LOCAIS deles antes "
                         "do próximo certificado (libera disco; não segura mais que "
                         "um certificado por vez). Só com --executar.")
    po.add_argument("--elevado", action="store_true", help=argparse.SUPPRESS)
    po.set_defaults(func=cmd_orquestrar)

    # limpar
    pl = sub.add_parser(
        "limpar",
        help="Apaga do download_bx os arquivos de linhas concluídas (libera espaço).")
    pl.add_argument("--executar", action="store_true",
                    help="Apaga de verdade. Sem isto, só mostra o que apagaria (dry-run).")
    pl.add_argument("--cert", type=int, nargs="+", metavar="ID",
                    help="Limpa SOMENTE os CNPJs destes certificados (id).")
    pl.set_defaults(func=cmd_limpar)

    return p


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
