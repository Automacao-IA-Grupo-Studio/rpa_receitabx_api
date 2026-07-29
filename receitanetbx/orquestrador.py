"""
orquestrador.py — Lote multi-certificado guiado pela fila do AUTOMATAX.

No banco, ``id_tipo_arquivo = 5`` significa **ReceitaBX**, e ReceitaBX abrange os
4 documentos (ECF + ECD + PISCOFINS + ICMS). Uma linha tipo 5 = "baixa tudo do
ReceitaBX para este cliente". Por isso o modo padrão (``sistema="todos"``)
harvest os 4 documentos por certificado e agrega o desfecho no status da linha.

Fluxo (um certificado de cada vez, barreira entre eles):

  1. Busca a fila (ReceitaBX, tipo 5, status "Aguardando") e agrupa por certificado.
  2. Para cada certificado:
     a. reconfigura o serviço (cert + senha + pasta própria) e reinicia UMA vez;
     b. PESQUISA cada CNPJ em CADA documento (não gera pedido). Sem arquivos
        naquele documento → conta como "sem eventos". Havendo, solicita (Rota A);
     c. UMA espera/drenagem: o serviço baixa TODOS os documentos juntos (ele não
        filtra por sistema no download). A cada ciclo lê os logs de cada documento
        ativo, move os concluídos para RECEITABX/<subpasta> e marca o andamento;
     d. quando todos os documentos de um CNPJ têm desfecho, grava o status da linha:
          - 5  (sucesso)   todo documento que tinha arquivo baixou (mix de 5/8 = 5);
          - 8  (sem eventos) os 4 documentos vieram vazios;
          - 21 (parcial)   parte baixou, parte falhou (etapa_erro diz qual);
          - 6  (erro)      nada baixou (só erros / cert / serviço offline).
  Só passa para o próximo certificado quando TODO o grupo tem status final.

Modo por documento único (``sistema="piscofins"`` etc.): harvest só aquele
documento para os MESMOS CNPJs do ReceitaBX (roster), grava na pasta do sistema
e NÃO escreve status (a linha representa os 4; um documento só não a finaliza).

Segurança: ``orquestrar(dry_run=True)`` (padrão) não toca no serviço, no banco
nem na Receita — apenas mostra o plano. Use ``dry_run=False`` para executar.
"""

import time
import traceback
from collections import OrderedDict
from datetime import date, timedelta

from . import config, cripto_senha, operacoes, processador, servico
from database.db_handler import DBHandler


def _log(on_evento, nivel, msg):
    if on_evento:
        on_evento(nivel, msg)


# ── helpers ──────────────────────────────────────────────────────────────
def _agrupar_por_certificado(pendentes) -> "OrderedDict":
    """{id_certificado: [solicitacoes]} preservando a ordem da query."""
    grupos = OrderedDict()
    for s in pendentes:
        grupos.setdefault(s["id_certificado"], []).append(s)
    return grupos


def _dir_rede(cnpj, subpasta="ECF"):
    return config.DEST_REDE / cnpj / "RECEITABX" / subpasta


def _rede_receitabx(cnpj):
    """Pasta pai na rede (contém as subpastas ECF/PISCOFINS/ECD/ICMS)."""
    return config.DEST_REDE / cnpj / "RECEITABX"


def _rede_tem_arquivos(cnpj, subpasta="ECF") -> bool:
    d = _dir_rede(cnpj, subpasta)
    return d.exists() and any(p.is_file() for p in d.iterdir())


def _contar_downloads(profile_key) -> int:
    """Nº de arquivos baixados na pasta do procurador (ignora logs) — serve de
    'batimento cardíaco' para detectar que ainda está chegando arquivo novo."""
    base = config.base_procurador(profile_key)
    if not base.exists():
        return 0
    return sum(
        1 for p in base.rglob("*")
        if p.is_file() and "logs" not in p.parts
    )


def _validar_certificado(cert):
    """Retorna (ok, motivo). Não lança — o motivo vira etapa_erro no banco."""
    if not cert:
        return False, "certificado não encontrado no banco"
    if not cert.get("pfx_filename"):
        return False, "certificado sem pfx_filename"
    if not cert.get("pfx_password"):
        return False, "certificado sem senha cadastrada"
    pfx = config.CERT_DIR / cert["pfx_filename"]
    if not pfx.exists():
        return False, f"arquivo .pfx não encontrado: {pfx}"
    return True, None


def _sistemas_do_modo(sistema):
    """Lista [(chave, scfg)] a orquestrar. 'todos' = os 4 documentos ReceitaBX,
    na ordem do config.SISTEMAS; senão, só o documento pedido."""
    if sistema == "todos":
        return [(k, config.SISTEMAS[k]) for k in config.SISTEMAS]
    return [(sistema, config.sistema_cfg(sistema))]


def _status_final(oks, erros):
    """Status da linha ReceitaBX a partir dos desfechos por documento.

    oks  = documentos que baixaram tudo que tinham.
    erros = documentos que falharam (recusa/timeout).
    (documentos "sem eventos"/"sem procuração" são neutros — ficam anotados no
    etapa_erro, mas não impedem o sucesso.)

    Sem NENHUM erro → SUCESSO (5), inclusive quando TODOS os documentos estavam
    vazios (nada a baixar é sucesso; os docs vazios ficam registrados no
    etapa_erro).
    """
    if erros == 0:
        return config.ST_SUCESSO       # 5  — nada falhou (baixou tudo que tinha)
    if oks > 0:
        return config.ST_PARCIAL       # 21 — parte ok, parte erro
    return config.ST_ERRO              # 6  — nada baixou (só erros)


# ── processamento de um grupo (um certificado) ───────────────────────────
def _teto_do_lote(n) -> int:
    if n <= config.ORQ_LIMITE_LOTE_PEQUENO:
        return config.ORQ_TETO_PEQUENO_SEGUNDOS
    return config.ORQ_TETO_GRANDE_SEGUNDOS


def _e_transitorio(saida) -> bool:
    """True se a resposta da Receita indica falha TEMPORÁRIA (vale retentar):
    'Serviço indisponível temporariamente', 'tente mais tarde' ou corpo vazio.
    Erros permanentes (sem procuração, CNPJ inválido) retornam False."""
    s = (saida or "").lower()
    return ("indispon" in s) or ("tente mais tarde" in s) or (s.strip() == "")


def _e_sem_arquivos(saida) -> bool:
    """True se a Receita respondeu que NÃO HÁ arquivos para o critério — ou seja,
    'sem eventos', NÃO um erro. A pesquisa pode sinalizar isso de dois jeitos:
    ``retorno=1`` com zero ids (tratado à parte) OU ``retorno=0`` com a mensagem
    'Não encontrado nenhum arquivo correspondente a pesquisa (20)'. Este helper
    cobre o segundo caso — antes ele virava 'falha' e a linha caía em parcial."""
    s = (saida or "").lower()
    return "nenhum arquivo correspondente" in s or "não encontrado nenhum arquivo" in s


def _e_sem_procuracao(saida) -> bool:
    """True se a Receita respondeu que NÃO HÁ procuração eletrônica p/ o CNPJ
    naquele documento (ex.: 'Não existe procuração eletrônica para o detentor
    ...'). Sem procuração não há o que baixar — então o documento é tratado como
    'sem eventos' (neutro), NÃO como erro (não adianta retentar). Só é consultado
    em respostas de recusa (retorno != 1). Casa a palavra 'procuração' (com ou
    sem acento), sem casar 'procurador' (o papel)."""
    s = (saida or "").lower()
    return "procuração" in s or "procuracao" in s


def _pesquisar_esperado(cnpj, on_evento, scfg):
    """Quantos arquivos a Receita tem para o CNPJ no documento (sem gerar pedido).

    Retenta em erro transitório da Receita. Returns (esperado, ids, ok):
      - (n, [ids], True)   n>0 arquivos disponíveis;
      - (0, [], True)      NADA a baixar (sem eventos OU sem procuração) — neutro,
                           já logado aqui como PULADO;
      - (None, [], False)  a pesquisa falhou de fato → seguimos sem contagem.
    Os ``ids`` alimentam a Rota B (solicitação por lista de arquivos), usada por
    documentos com ``solicita_por_ids`` (ECD/ICMS). 'Sem procuração' e 'nenhum
    arquivo' NÃO são erro: não há o que baixar, então contam como sem eventos.
    """
    sub = scfg["subpasta"]
    for tentativa in range(1, config.ORQ_SOLICITAR_TENTATIVAS + 1):
        try:
            res = operacoes.pesquisar(nirepresentado=cnpj, scfg=scfg)
        except Exception as e:  # noqa: BLE001
            _log(on_evento, "AVISO",
                 f"{cnpj} [{sub}]: pesquisa falhou ({e}) — seguindo sem contagem")
            return None, [], False
        if res.sucesso:
            if not res.ids:
                _log(on_evento, "PULADO", f"{cnpj} [{sub}]: sem eventos (0)")
            return len(res.ids), res.ids, True
        # 'Não encontrado nenhum arquivo (20)' = sem eventos, não é falha.
        if _e_sem_arquivos(res.saida):
            _log(on_evento, "PULADO", f"{cnpj} [{sub}]: sem eventos (Receita: nenhum arquivo)")
            return 0, [], True
        # Sem procuração: nada a baixar → sem eventos (neutro), NÃO erro.
        if _e_sem_procuracao(res.saida):
            _log(on_evento, "PULADO", f"{cnpj} [{sub}]: sem procuração — nada a baixar")
            return 0, [], True
        # sem sucesso: retenta só se for transitório e ainda houver tentativa
        if _e_transitorio(res.saida) and tentativa < config.ORQ_SOLICITAR_TENTATIVAS:
            _log(on_evento, "AVISO",
                 f"{cnpj} [{sub}]: Receita indisponível na pesquisa "
                 f"(tentativa {tentativa}) — nova em {config.ORQ_SOLICITAR_BACKOFF_SEGUNDOS}s")
            time.sleep(config.ORQ_SOLICITAR_BACKOFF_SEGUNDOS)
            continue
        _log(on_evento, "AVISO",
             f"{cnpj} [{sub}]: pesquisa sem sucesso (HTTP {res.http_status}) "
             f"— seguindo sem contagem esperada")
        return None, [], False
    return None, [], False


def _solicitar_com_retry(cnpj, on_evento, scfg, ids=None):
    """Solicita retentando em erro TEMPORÁRIO da Receita. Erro permanente (sem
    procuração etc.) sai na 1ª. Returns o ResultadoPedido.

    Documentos com ``solicita_por_ids`` (ECD/ICMS) usam a Rota B (lista de
    arquivos ``ids`` vinda da pesquisa); os demais, a Rota A (por período)."""
    por_ids = scfg.get("solicita_por_ids", False)
    res = None
    for tentativa in range(1, config.ORQ_SOLICITAR_TENTATIVAS + 1):
        if por_ids:
            res = operacoes.solicitar_por_ids(cnpj, ids or [], scfg=scfg)
        else:
            res = operacoes.solicitar_por_periodo(cnpj, scfg=scfg)
        if res.sucesso or not _e_transitorio(res.saida):
            return res
        if tentativa < config.ORQ_SOLICITAR_TENTATIVAS:
            _log(on_evento, "AVISO",
                 f"{cnpj} [{scfg['subpasta']}]: Receita indisponível ao solicitar "
                 f"(tentativa {tentativa}) — nova em {config.ORQ_SOLICITAR_BACKOFF_SEGUNDOS}s")
            time.sleep(config.ORQ_SOLICITAR_BACKOFF_SEGUNDOS)
    return res


def _janela_datas(data_inicio):
    """Datas AAAAMMDD de data_inicio até hoje — normalmente só hoje; 2+ se a
    espera cruzou a meia-noite (pedido num dia, download no outro)."""
    dias = (date.today() - data_inicio).days
    return [(data_inicio + timedelta(days=i)).strftime("%Y%m%d")
            for i in range(max(dias, 0) + 1)]


def _processar_grupo(db, cert, grupo, on_evento, sistemas, teto_override=None,
                     drenar=False, rastrear=True, catchup=False):
    """Harvest de um certificado: solicita os documentos de ``sistemas`` para
    todos os CNPJs do grupo, drena UMA vez e grava o status ReceitaBX por linha.

    ``sistemas``: lista [(chave, scfg)] (1 documento ou os 4).
    ``rastrear``: grava status no banco (só faz sentido no modo 'todos').
    ``catchup``: modo catch-up de ECD (sistemas=[ecd], linhas já parciais/21). A
        finalização fecha em SUCESSO (5) se o ECD resolveu, ou MANTÉM parcial (21)
        se ainda não — nunca rebaixa para erro (6), pois os outros 3 docs já vieram.
    """
    # Escrita de status no banco só no modo ReceitaBX completo ('todos').
    # No modo documento-único, estas viram no-op (a linha representa os 4).
    def _mark(sid, *a, **k):
        if rastrear:
            db.marcar_status(sid, *a, **k)

    def _erro(sid, *a, **k):
        if rastrear:
            db.registrar_erro(sid, *a, **k)

    def _sucesso(sid, *a, **k):
        if rastrear:
            db.registrar_sucesso(sid, *a, **k)

    def _parcial(sid, etapa, file_url=None):
        if rastrear:
            db.registrar_parcial(sid, etapa, file_url=file_url)

    profile = (cert.get("profile_key") or f"cert{cert['id']}").strip()
    log_base = config.log_base_procurador(profile)
    pfx = config.CERT_DIR / cert["pfx_filename"]
    docs = "+".join(s["subpasta"] for _, s in sistemas)

    # a) reconfigura e reinicia o serviço para este certificado (UMA vez)
    _log(on_evento, "INFO", f"[{docs}] Configurando serviço p/ "
                            f"{cert['company_name']} (CNPJ cert {cert['cnpj']}, pasta {profile})")
    servico.aplicar_certificado(pfx, cert["pfx_password"],
                               config.base_procurador(profile))
    servico.reiniciar()
    if not servico.aguardar_online():
        for s in grupo:
            _erro(s["id"], "servico_offline", "serviço não respondeu após reiniciar")
        _log(on_evento, "ERRO", "Serviço não ficou online — grupo marcado com erro")
        return

    # b) pesquisa + solicita cada (CNPJ, documento).
    # desfecho[sid]: estado por documento ("pendente"/"ok"/"erro"/"semev").
    # ativos[(sid, chave)]: pedidos aguardando download.
    desfecho = OrderedDict()
    ativos = OrderedDict()
    fase4 = set()          # sids já marcados "Efetuando downloads" (status 4)
    finalizados = set()

    def _finalizar(sid):
        """Fecha uma linha: agrega os desfechos por documento no status ReceitaBX."""
        if sid in finalizados:
            return
        finalizados.add(sid)
        info = desfecho[sid]
        cnpj = info["s"]["cnpj"]
        estados = list(info["sist"].values())
        oks = estados.count("ok")
        erros = estados.count("erro")
        semevs = estados.count("semev")
        rede = str(_rede_receitabx(cnpj))
        falhas = "; ".join(info["motivo"].get(c, c)
                           for c, e in info["sist"].items() if e == "erro")
        # Documentos SEM nada para baixar (sem eventos / sem procuração): a linha
        # continua SUCESSO, mas anotamos no etapa_erro quais foram — informativo.
        vazios = [config.SISTEMAS[c]["subpasta"] for c, e in info["sist"].items()
                  if e == "semev" and c in config.SISTEMAS]
        nota_vazios = "; ".join(f"{d}: sem documentos para baixar" for d in vazios)
        if catchup:
            # A linha já estava parcial (os outros 3 docs vieram). Se o ECD agora
            # resolveu (baixou ou sem eventos), fecha em SUCESSO (5). Se ainda não,
            # MANTÉM parcial (21) — nunca rebaixa para erro (6).
            if erros == 0:
                _sucesso(sid, rede, nota_vazios)
                _log(on_evento, "OK", f"{cnpj}: ECD recuperado — linha concluída (5)"
                     + (f" [{nota_vazios}]" if nota_vazios else ""))
            else:
                _parcial(sid, falhas, file_url=rede)
                _log(on_evento, "AVISO",
                     f"{cnpj}: ECD ainda pendente — mantém parcial (21): {falhas}")
            return
        st = _status_final(oks, erros)
        if st == config.ST_SUCESSO:
            _sucesso(sid, rede, nota_vazios)
            _log(on_evento, "OK",
                 f"{cnpj}: ReceitaBX OK (5) — {oks} doc(s) baixado(s), {semevs} sem eventos"
                 + (f" [{nota_vazios}]" if nota_vazios else ""))
        elif st == config.ST_PARCIAL:
            _parcial(sid, falhas, file_url=rede)
            _log(on_evento, "AVISO",
                 f"{cnpj}: parcialmente completo (21) — {oks} ok / falhou: {falhas}")
        else:  # ST_ERRO
            _erro(sid, falhas or "Nenhum arquivo baixado.")
            _log(on_evento, "ERRO", f"{cnpj}: erro (6) — {falhas or 'nada baixado'}")

    def _sid_terminou(sid):
        """True se nenhum documento do sid está mais 'pendente'."""
        return "pendente" not in desfecho[sid]["sist"].values()

    for s in grupo:
        sid, cnpj = s["id"], s["cnpj"]
        desfecho[sid] = {"s": s, "sist": OrderedDict(), "motivo": {}}
        _mark(sid, config.ST_CRIANDO)  # 2
        for chave, scfg in sistemas:
            esperado, ids, ok = _pesquisar_esperado(cnpj, on_evento, scfg)
            if ok and esperado == 0:
                # sem eventos / sem procuração — já logado em _pesquisar_esperado.
                desfecho[sid]["sist"][chave] = "semev"
                continue
            if scfg.get("solicita_por_ids") and not ids:
                # ECD/ICMS só solicitam pela lista de arquivos; sem a pesquisa não dá.
                desfecho[sid]["sist"][chave] = "erro"
                desfecho[sid]["motivo"][chave] = \
                    f"{scfg['subpasta']}: não foi possível listar os arquivos na Receita."
                _log(on_evento, "ERRO",
                     f"{cnpj} [{scfg['subpasta']}]: pesquisa não retornou arquivos para solicitar")
                continue
            try:
                res = _solicitar_com_retry(cnpj, on_evento, scfg, ids)
            except Exception as e:  # noqa: BLE001
                desfecho[sid]["sist"][chave] = "erro"
                desfecho[sid]["motivo"][chave] = f"{scfg['subpasta']}: erro ao solicitar na Receita."
                _log(on_evento, "ERRO", f"{cnpj} [{scfg['subpasta']}]: falha ao solicitar ({e})")
                continue
            if res.sucesso:
                desfecho[sid]["sist"][chave] = "pendente"
                ativos[(sid, chave)] = {"cnpj": cnpj, "esperado": esperado,
                                        "baixados": 0, "scfg": scfg}
                alvo = f"{esperado} arquivo(s)" if esperado is not None else "qtd. desconhecida"
                _log(on_evento, "OK",
                     f"{cnpj} [{scfg['subpasta']}]: pedido {res.numero_pedido} (esperado: {alvo})")
            elif _e_sem_procuracao(res.saida) or _e_sem_arquivos(res.saida):
                # Recusa por sem procuração / sem arquivo: nada a fazer → sem
                # eventos (neutro), NÃO erro (não derruba a linha p/ parcial).
                desfecho[sid]["sist"][chave] = "semev"
                _log(on_evento, "PULADO",
                     f"{cnpj} [{scfg['subpasta']}]: sem procuração/arquivo — nada a baixar")
            else:
                motivo = operacoes.mensagem_amigavel(res.saida)
                desfecho[sid]["sist"][chave] = "erro"
                desfecho[sid]["motivo"][chave] = f"{scfg['subpasta']}: {motivo}"
                _log(on_evento, "ERRO",
                     f"{cnpj} [{scfg['subpasta']}]: solicitação recusada — {motivo}")
        _mark(sid, config.ST_AGUARDANDO_DL)  # 3
        if _sid_terminou(sid):  # nada a baixar (tudo semev/erro) → fecha já
            _finalizar(sid)

    if not ativos:
        _log(on_evento, "INFO", "Nenhum download pendente no grupo (tudo resolvido na solicitação)")
        return

    # c) UMA espera/drenagem: o serviço baixa todos os documentos juntos.
    teto = teto_override * 60 if teto_override else _teto_do_lote(len(ativos))
    intervalo_min = servico.ler_intervalo_minutos()
    inicio = time.monotonic()
    data_inicio = date.today()
    ultima_novidade = inicio
    proximo_hb = inicio + config.ORQ_HEARTBEAT_SEGUNDOS
    cont_inicial = _contar_downloads(profile)
    ultima_cont = cont_inicial
    total_ativos = len(ativos)
    modo = "MODO DRENAGEM (usa o teto inteiro)" if drenar else "espera paciente até o 1º arquivo"
    docs_lbl = ", ".join(sorted({s["subpasta"] for _, s in sistemas}))
    _log(on_evento, "INFO",
         f"Aguardando downloads ({total_ativos} pedidos | docs: {docs_lbl} | serviço "
         f"verifica a cada {intervalo_min} min | {modo} | teto {teto // 60} min)")

    while ativos:
        time.sleep(config.ORQ_POLL_SEGUNDOS)

        # Lê os logs UMA vez por documento ainda ativo neste ciclo.
        # ICMS (pasta_cliente): agrupa filiais pela base de 8 dígitos do CNPJ,
        # pois a pesquisa/contagem é por cliente e o destino é a pasta do cliente.
        chaves_ativas = {chave for (_sid, chave) in ativos}
        manter_por, baix_por = {}, {}
        for chave in chaves_ativas:
            scfg = config.SISTEMAS[chave]
            por_cliente = scfg.get("pasta_cliente", False)
            try:
                manter_all, desc_all, _np, _nd = processador.carregar_janela(
                    _janela_datas(data_inicio), log_base=log_base,
                    sistema=scfg["sistema"], apenas_baixados=True,
                    attr_contribuinte=scfg["attr_contribuinte"],
                    aplicar_regra_retif=scfg["regra_retif"],
                    attr_situacao=scfg.get("attr_situacao"),
                    regra_situacao=scfg.get("regra_situacao", False))
            except Exception:  # noqa: BLE001 — segue tentando nos próximos ciclos
                _log(on_evento, "AVISO",
                     f"erro ao ler logs [{scfg['subpasta']}] (retenta)\n{traceback.format_exc()}")
                continue
            mp, bp = {}, {}
            for a in manter_all:
                k = a.contribuinte[:8] if por_cliente else a.contribuinte
                mp.setdefault(k, []).append(a)
                if a.caminho:
                    bp[k] = bp.get(k, 0) + 1
            for a in desc_all:
                if a.caminho:
                    k = a.contribuinte[:8] if por_cliente else a.contribuinte
                    bp[k] = bp.get(k, 0) + 1
            manter_por[chave] = mp
            baix_por[chave] = bp

        for (sid, chave), info in list(ativos.items()):
            cnpj = info["cnpj"]
            esperado = info["esperado"]
            scfg = info["scfg"]
            por_cliente = scfg.get("pasta_cliente", False)
            k_cli = cnpj[:8] if por_cliente else cnpj
            baixados = baix_por.get(chave, {}).get(k_cli, 0)
            info["baixados"] = baixados
            if baixados and sid not in fase4:
                fase4.add(sid)
                _mark(sid, config.ST_EFETUANDO_DL)  # 4
                _log(on_evento, "INFO", f"{cnpj}: começou a baixar")
            if not baixados:
                continue
            # Concluído: baixou TUDO que a pesquisa apontou (ou, sem contagem,
            # basta ter chegado arquivo — fallback do modo sem-pesquisa).
            completo = (baixados >= esperado) if esperado is not None else True
            if not completo:
                continue

            movidos = manter_por.get(chave, {}).get(k_cli, [])
            resumo = processador.mover_arquivos(
                movidos,
                subpasta=scfg["subpasta"],
                cnpj_override=cnpj if por_cliente else None)
            desfecho[sid]["sist"][chave] = "ok"
            del ativos[(sid, chave)]
            # baixados conta TUDO que chegou; nem tudo vai pra rede (ECF/PIS:
            # retificadora mais recente do período; ECD: tudo menos a substituída;
            # ICMS: tudo). Deixamos explícito no log p/ não parecer que "faltou
            # enviar" (baixados > enviados é normal).
            n_desc = max(baixados - len(movidos), 0)
            alvo = f"{baixados}/{esperado}" if esperado is not None else str(baixados)
            extra = f", {n_desc} descartada(s) (substituída/versão anterior)" if n_desc else ""
            _log(on_evento, "OK",
                 f"{cnpj} [{scfg['subpasta']}]: concluído ({alvo}) — rede: "
                 f"{resumo.copiados} enviada(s), {resumo.pulados} já existia(m){extra}")
            if _sid_terminou(sid):
                _finalizar(sid)

        if not ativos:
            break

        atual = _contar_downloads(profile)
        agora = time.monotonic()
        if atual > ultima_cont:
            ultima_cont = atual
            ultima_novidade = agora
        viu_download = atual > cont_inicial

        if agora >= proximo_hb:
            proximo_hb = agora + config.ORQ_HEARTBEAT_SEGUNDOS
            dec = int(agora - inicio)
            if not viu_download:
                fase = "aguardando serviço iniciar"
            else:
                sem_nov = int(agora - ultima_novidade)
                fase = ("baixando" if sem_nov <= config.ORQ_POLL_SEGUNDOS * 2
                        else f"aguardando arquivos restantes ({sem_nov // 60} min sem novidade)")
            concluidos = total_ativos - len(ativos)
            _log(on_evento, "PROG",
                 f"decorrido {dec // 60:02d}:{dec % 60:02d} | "
                 f"pedidos concluídos {concluidos}/{total_ativos} | "
                 f"linhas fechadas {len(finalizados)}/{len(desfecho)} | {fase}")

        if agora - inicio > teto:
            _log(on_evento, "INFO", "Teto de tempo do grupo atingido — encerrando espera")
            break
        if drenar:
            continue  # modo drenagem: usa o teto inteiro (serviço varre o backlog)
        if not viu_download:
            continue  # espera PACIENTE: não desiste antes de o 1º arquivo chegar
        if agora - ultima_novidade > config.ORQ_SEM_NOVIDADE_SEGUNDOS:
            _log(on_evento, "INFO",
                 f"Sem arquivo novo há {config.ORQ_SEM_NOVIDADE_SEGUNDOS // 60} min "
                 f"— encerrando espera do grupo")
            break

    # d) o que sobrou ativo (não baixou no tempo) → erro naquele documento.
    for (sid, chave), info in ativos.items():
        scfg = info["scfg"]
        det = (f"baixou {info['baixados']}/{info['esperado']}"
               if info["esperado"] is not None else f"baixou {info['baixados']}")
        desfecho[sid]["sist"][chave] = "erro"
        desfecho[sid]["motivo"][chave] = \
            f"{scfg['subpasta']}: não terminou de baixar no tempo ({det})."
    ativos.clear()
    for sid in list(desfecho):
        _finalizar(sid)  # fecha qualquer linha ainda aberta (idempotente)


# ── entrada principal ────────────────────────────────────────────────────
def _validar_e_limpar_cert(db, cert, grupo, on_evento):
    """Após um certificado terminar: confere na REDE os arquivos que deveriam ter
    sido arquivados de cada CNPJ CONCLUÍDO e, se estiver tudo lá, apaga as cópias
    LOCAIS daquele CNPJ — liberando disco ANTES de partir para o próximo
    certificado. CNPJ com QUALQUER arquivo faltando na rede NÃO é apagado (as
    cópias locais ficam para reprocessar). Nunca toca em pendentes/parciais.
    """
    nome = cert.get("company_name", f"id {cert['id']}")
    profile = (cert.get("profile_key") or f"cert{cert['id']}").strip()
    log_base = config.log_base_procurador(profile)
    try:
        datas = processador.datas_disponiveis(log_base)[-30:]  # janela recente
    except Exception:  # noqa: BLE001
        datas = []
    if not datas:
        return

    # CNPJs deste certificado que já concluíram (nada mais a baixar).
    status = {l.get("cnpj"): l.get("id_status") for l in db.listar_status_receitabx()}
    concluidos = [s["cnpj"] for s in grupo
                  if status.get(s["cnpj"]) in config.ST_CONCLUIDOS]
    if not concluidos:
        _log(on_evento, "INFO",
             f"[{nome}] nenhum CNPJ concluído ainda — nada a limpar localmente.")
        return

    # O que DEVERIA estar na rede, por documento (lê os logs 1x por sistema).
    manter_por_sistema = {}
    for chave, scfg in config.SISTEMAS.items():
        try:
            manter, _d, _np, _nd = processador.carregar_janela(
                datas, log_base=log_base, sistema=scfg["sistema"], apenas_baixados=True,
                attr_contribuinte=scfg["attr_contribuinte"],
                aplicar_regra_retif=scfg["regra_retif"],
                attr_situacao=scfg.get("attr_situacao"),
                regra_situacao=scfg.get("regra_situacao", False))
        except Exception:  # noqa: BLE001
            manter = []
        manter_por_sistema[chave] = (scfg, manter)

    # Verifica CNPJ a CNPJ: cada arquivo "manter" tem gêmeo na pasta de rede?
    liberados, pendentes, total_conf = set(), [], 0
    for cnpj in concluidos:
        faltando = total = 0
        for _chave, (scfg, manter) in manter_por_sistema.items():
            por_cliente = scfg.get("pasta_cliente", False)
            if por_cliente:
                arqs = [a for a in manter if a.contribuinte[:8] == cnpj[:8]]
            else:
                arqs = [a for a in manter if a.contribuinte == cnpj]
            for a in arqs:
                total += 1
                destino = processador._destino_dir(
                    a, scfg["subpasta"], cnpj_override=cnpj if por_cliente else None)
                if not (a.nome and (destino / a.nome).exists()):
                    faltando += 1
        if faltando == 0:
            liberados.add(cnpj[:8])
            total_conf += total
        else:
            pendentes.append((cnpj, faltando, total))

    if liberados:
        r = processador.limpar_arquivos_baixados(
            profile, liberados, dry_run=False, on_evento=on_evento)
        _log(on_evento, "OK",
             f"[{nome}] rede conferida: {len(liberados)} CNPJ(s) OK, "
             f"{total_conf} arquivo(s) confirmado(s) — apagados {r['apagados']} "
             f"local(is) ({processador._fmt_tam(r['bytes'])} liberados).")
    for cnpj, faltando, total in pendentes:
        _log(on_evento, "AVISO",
             f"[{nome}] {cnpj}: {faltando}/{total} arquivo(s) NÃO encontrados na "
             f"rede — cópias locais MANTIDAS (serão reprocessadas).")


def orquestrar(dry_run: bool = True, on_evento=None, somente_certs=None,
               teto_min=None, sistema="todos", catchup=False, limpar=False) -> dict:
    """Executa o lote completo. dry_run=True apenas mostra o plano.

    ``somente_certs``: lista de id_certificado a processar (os demais são
    pulados). Útil para testar/isolar um certificado. None = todos.

    ``teto_min``: se informado (minutos), ativa o MODO DRENAGEM — cada
    certificado recebe uma janela longa (teto_min) e a espera usa o teto
    inteiro (sem corte por "sem novidade"). Ideal para rodar de madrugada.

    ``sistema``: 'todos' (padrão) = ReceitaBX completo (os 4 documentos), usa a
    fila do AUTOMATAX (tipo 5, status 1) e grava o status agregado por linha.
    'ecf'/'piscofins'/'ecd'/'icms' = só aquele documento para os MESMOS CNPJs do
    ReceitaBX (roster), grava na pasta do sistema e NÃO escreve status.

    ``catchup``: CATCH-UP DE ECD. Reprocessa SÓ o ECD das linhas que ficaram
    PARCIAIS (status 21) — o ECD costuma demorar horas para a Receita
    disponibilizar, então uma passada posterior recolhe o que ficou pronto sem
    re-baixar os outros 3 documentos. Fecha a linha em 5 quando o ECD completar.

    Returns um resumo {id_certificado: {"company": ..., "n": ..., "status": ...}}.
    """
    db = DBHandler()
    if catchup:
        sistemas = [("ecd", config.SISTEMAS["ecd"])]
        rastrear = True
        docs_lbl = "ECD"
        linhas = db.buscar_pendentes_ecf(status=config.ST_PARCIAL)   # parciais (21)
        _log(on_evento, "INFO",
             f"Catch-up de ECD: {len(linhas)} linha(s) parcial(is) (status 21) — "
             f"completa só o ECD e fecha em 5 quando concluir; não toca nos outros docs.")
    else:
        sistemas = _sistemas_do_modo(sistema)
        rastrear = (sistema == "todos")
        docs_lbl = ", ".join(s["subpasta"] for _, s in sistemas)
        if rastrear:
            linhas = db.buscar_pendentes_ecf()      # fila ReceitaBX (tipo 5, status 1)
            _log(on_evento, "INFO",
                 f"Modo ReceitaBX completo: {len(linhas)} linha(s) pendente(s) "
                 f"(tipo 5, status 1) — documentos: {docs_lbl}. Status agregado por linha.")
        else:
            linhas = db.buscar_roster_ecf()         # mesmos CNPJs do ReceitaBX
            _log(on_evento, "INFO",
                 f"Modo documento único ({docs_lbl}): usando os mesmos {len(linhas)} CNPJs "
                 f"do ReceitaBX (sem status no banco; grava em RECEITABX/{docs_lbl}).")
    grupos = _agrupar_por_certificado(linhas)
    resumo = OrderedDict()

    if somente_certs:
        grupos = OrderedDict((k, v) for k, v in grupos.items() if k in somente_certs)
        _log(on_evento, "INFO",
             f"Filtro --cert ativo: processando somente certificado(s) {sorted(somente_certs)}")

    if not grupos:
        _log(on_evento, "INFO", f"Nada a processar para {docs_lbl}"
                                + (" (certificado(s) filtrado(s))." if somente_certs else "."))
        return resumo

    # Antes de tocar em qualquer coisa (modo real), descobre e VALIDA a chave de
    # criptografia da senha contra o oráculo. Se falhar, aborta — não grava
    # senha inválida (que derrubaria o serviço).
    if not dry_run:
        # Testa o cert de REFERÊNCIA primeiro e, se ele sumiu/mudou de id (a
        # tabela de certificados é reorganizada de tempos em tempos), cai para as
        # senhas de TODOS os certificados — a chave é a mesma desta máquina.
        certs_all = db.listar_certificados()
        pref = next((c for c in certs_all
                     if c["id"] == config.CRIPTO_ORACULO_CERT_ID), None)
        candidatos = ([pref["pfx_password"]] if pref else []) + \
                     [c["pfx_password"] for c in certs_all]
        try:
            cripto_senha.descobrir_chave_entre(candidatos, config.CRIPTO_ORACULO_CIFRA)
            _log(on_evento, "INFO", "Chave de criptografia do serviço confirmada (oráculo OK).")
        except Exception as e:  # noqa: BLE001
            _log(on_evento, "ERRO", f"Criptografia: {e}")
            _log(on_evento, "ERRO", "Abortando sem tocar no serviço/banco.")
            return resumo

    for id_cert, grupo in grupos.items():
        cert = db.buscar_certificado(id_cert)
        nome = (cert or {}).get("company_name", f"id {id_cert}")

        if id_cert in config.CERT_IDS_IGNORADOS:
            _log(on_evento, "PULADO", f"Certificado {nome} (id {id_cert}) ignorado "
                                      f"— {len(grupo)} solicitações não tocadas")
            resumo[id_cert] = {"company": nome, "n": len(grupo), "status": "ignorado"}
            continue

        ok, motivo = _validar_certificado(cert)
        if not ok:
            _log(on_evento, "ERRO", f"Certificado {nome}: {motivo} "
                                    f"— {len(grupo)} solicitações com erro")
            if not dry_run and rastrear:
                for s in grupo:
                    db.registrar_erro(s["id"], "certificado", motivo)
            resumo[id_cert] = {"company": nome, "n": len(grupo), "status": motivo}
            continue

        cnpjs = ", ".join(s["cnpj"] for s in grupo)
        _log(on_evento, "GRUPO",
             f"[{nome}] {len(grupo)} CNPJ(s) x [{docs_lbl}]: {cnpjs}")

        if dry_run:
            resumo[id_cert] = {"company": nome, "n": len(grupo), "status": "simulado"}
            continue

        try:
            _processar_grupo(db, cert, grupo, on_evento, sistemas,
                             teto_override=teto_min, drenar=bool(teto_min),
                             rastrear=rastrear, catchup=catchup)
            resumo[id_cert] = {"company": nome, "n": len(grupo), "status": "processado"}
            # Regra de disco: terminado o certificado, confere na rede e apaga as
            # cópias locais dos CNPJs concluídos ANTES do próximo certificado.
            if limpar and rastrear:
                try:
                    _validar_e_limpar_cert(db, cert, grupo, on_evento)
                except Exception:  # noqa: BLE001 — limpeza nunca derruba o lote
                    _log(on_evento, "AVISO",
                         f"[{nome}] falha ao validar/limpar local (segue):\n{traceback.format_exc()}")
        except Exception as e:  # noqa: BLE001 — um certificado com problema não derruba o lote
            _log(on_evento, "ERRO", f"[{nome}] falhou: {e}\n{traceback.format_exc()}")
            if rastrear:
                for s in grupo:
                    db.registrar_erro(s["id"], "grupo", traceback.format_exc())
            resumo[id_cert] = {"company": nome, "n": len(grupo), "status": f"erro: {e}"}

    return resumo


# ── limpeza do download_bx (libera espaço) ────────────────────────────────
def limpar_concluidos(dry_run: bool = True, somente_certs=None, on_evento=None) -> dict:
    """Apaga do ``download_bx`` os arquivos das linhas ReceitaBX já CONCLUÍDAS,
    liberando espaço (o mover_arquivos COPIA p/ a rede, então os locais sobram).

    Um CNPJ (base de 8 díg.) só é liberado quando TODAS as suas linhas estão em
    ``config.ST_CONCLUIDOS`` (sucesso/sem eventos/sem procuração) E ao menos uma
    é sucesso — assim nada pendente/parcial/erro perde seus arquivos. Varre só os
    profiles que têm CNPJ liberado. Dry-run por padrão (só relata o que apagaria).

    ``somente_certs``: restringe aos certificados informados (por id).
    Returns dict {profiles, arquivos, bytes, apagados, erros}.
    """
    db = DBHandler()
    linhas = db.listar_status_receitabx()

    st_por_base, prof_por_base, cert_por_base = {}, {}, {}
    for l in linhas:
        cnpj = l.get("cnpj") or ""
        if len(cnpj) < 8:
            continue
        b8 = cnpj[:8]
        st_por_base.setdefault(b8, set()).add(l.get("id_status"))
        if l.get("profile_key"):
            prof_por_base.setdefault(b8, set()).add(l["profile_key"])
        if l.get("id_certificado"):
            cert_por_base.setdefault(b8, set()).add(l["id_certificado"])

    liberados = {
        b8 for b8, sts in st_por_base.items()
        if config.ST_SUCESSO in sts and sts <= set(config.ST_CONCLUIDOS)
    }
    if somente_certs:
        alvo = set(somente_certs)
        liberados = {b8 for b8 in liberados if cert_por_base.get(b8, set()) & alvo}

    profiles = sorted({p for b8 in liberados for p in prof_por_base.get(b8, ())})
    _log(on_evento, "INFO",
         f"Limpeza: {len(liberados)} CNPJ(s) concluído(s) em {len(profiles)} "
         f"profile(s){' — SIMULAÇÃO' if dry_run else ''}")

    tot = {"profiles": len(profiles), "arquivos": 0, "bytes": 0,
           "apagados": 0, "erros": 0}
    for prof in profiles:
        r = processador.limpar_arquivos_baixados(prof, liberados, dry_run, on_evento)
        tot["arquivos"] += r["arquivos"]
        tot["bytes"] += r["bytes"]
        tot["apagados"] += r["apagados"]
        tot["erros"] += r["erros"]
    return tot
