"""
db_handler.py — Acesso ao banco AUTOMATAX (PostgreSQL/Supabase).

Fila de trabalho: ``pjdocs_sol_baixa_arquivos`` (uma solicitação por CNPJ).
Certificados:     ``certificados`` (pfx_filename, pfx_password, cnpj, ...).
Status:           ``pjdocs_sol_baixa_arquivos_status``.

Todos os métodos abrem e fecham a própria conexão (curta duração), para não
segurar conexão do pool do Supabase durante as longas esperas de download.
"""

import logging
import os

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Carrega variáveis do arquivo .env
load_dotenv()

logger = logging.getLogger("bx_api.db")


class DBHandler:
    def __init__(self):
        self.config = {
            "user": os.getenv("DB_USER"),
            "password": os.getenv("DB_PASSWORD"),
            "host": os.getenv("DB_HOST"),
            "database": os.getenv("DB_NAME"),
            "port": int(os.getenv("DB_PORT", 5432)),
        }

    def _get_connection(self):
        try:
            return psycopg2.connect(**self.config)
        except psycopg2.Error as err:
            logger.error(f"Erro ao conectar no Banco de Dados: {err}")
            return None

    def connect(self):
        conn = self._get_connection()
        if conn:
            conn.close()
            return True
        return False

    # ── Leitura da fila ──────────────────────────────────────────────────
    def buscar_pendentes_ecf(self, status=None):
        """Solicitações ReceitaBX (id_tipo_arquivo=5) num dado status.

        ``status``: id_status a buscar (padrão ORQ_STATUS_PENDENTE=1, "Aguardando").
        O catch-up de ECD passa ST_PARCIAL (21) para reprocessar as parciais.

        Ordena por certificado (agrupa o lote), depois prioridade e id — assim o
        orquestrador percorre um certificado de cada vez.
        """
        from receitanetbx import config

        if status is None:
            status = config.ORQ_STATUS_PENDENTE
        query = (
            "SELECT id, cnpj, razao_social, id_certificado, profile_key, prioridade "
            "FROM pjdocs_sol_baixa_arquivos "
            "WHERE id_tipo_arquivo = %s AND id_status = %s "
            "ORDER BY id_certificado, prioridade DESC, id"
        )
        conn = self._get_connection()
        if not conn:
            return []
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(query, (config.ORQ_TIPO_ARQUIVO, status))
            linhas = [dict(r) for r in cur.fetchall()]
            cur.close()
            return linhas
        except psycopg2.Error as err:
            logger.error(f"Erro ao buscar pendentes ECF: {err}")
            return []
        finally:
            conn.close()

    def listar_certificados(self):
        """Todos os certificados (id, company_name, pfx_password). Usado pelo
        oráculo de criptografia para achar a chave mesmo se a tabela mudar de id."""
        conn = self._get_connection()
        if not conn:
            return []
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT id, company_name, pfx_password FROM certificados ORDER BY id")
            linhas = [dict(r) for r in cur.fetchall()]
            cur.close()
            return linhas
        except psycopg2.Error as err:
            logger.error(f"Erro ao listar certificados: {err}")
            return []
        finally:
            conn.close()

    def listar_status_receitabx(self):
        """Todas as linhas ReceitaBX (tipo 5) com (cnpj, id_status, profile_key,
        id_certificado). Usado pela limpeza do download_bx para saber quais
        CNPJs já concluíram (e podem ter os arquivos locais apagados)."""
        from receitanetbx import config

        query = (
            "SELECT cnpj, id_status, profile_key, id_certificado "
            "FROM pjdocs_sol_baixa_arquivos WHERE id_tipo_arquivo = %s"
        )
        conn = self._get_connection()
        if not conn:
            return []
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(query, (config.ORQ_TIPO_ARQUIVO,))
            linhas = [dict(r) for r in cur.fetchall()]
            cur.close()
            return linhas
        except psycopg2.Error as err:
            logger.error(f"Erro ao listar status ReceitaBX: {err}")
            return []
        finally:
            conn.close()

    def buscar_roster_ecf(self):
        """CNPJs distintos que atendemos para ECF (tipo 5), com seu certificado.

        Usado para solicitar OUTROS sistemas (PISCOFINS/ECD/ICMS) para os MESMOS
        clientes, sem depender de uma fila própria. Não há status para eles — o
        campo ``id`` é sintético (o próprio CNPJ), só para chavear internamente.
        """
        from receitanetbx import config

        query = (
            "SELECT cnpj, id_certificado, MAX(profile_key) AS profile_key, "
            "MAX(prioridade) AS prioridade "
            "FROM pjdocs_sol_baixa_arquivos "
            "WHERE id_tipo_arquivo = %s AND id_certificado IS NOT NULL "
            "GROUP BY cnpj, id_certificado "
            "ORDER BY id_certificado, cnpj"
        )
        conn = self._get_connection()
        if not conn:
            return []
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(query, (config.ORQ_TIPO_ARQUIVO,))
            linhas = []
            for r in cur.fetchall():
                d = dict(r)
                d["id"] = d["cnpj"]  # id sintético (sem linha na fila)
                linhas.append(d)
            cur.close()
            return linhas
        except psycopg2.Error as err:
            logger.error(f"Erro ao buscar roster ECF: {err}")
            return []
        finally:
            conn.close()

    def buscar_certificado(self, id_certificado):
        """Dados do certificado: company_name, cnpj, pfx_filename, pfx_password,
        profile_key, expires_at. Retorna None se não achar."""
        if not id_certificado:
            return None
        query = (
            "SELECT id, company_name, cnpj, pfx_filename, pfx_password, "
            "profile_key, expires_at FROM certificados WHERE id = %s"
        )
        conn = self._get_connection()
        if not conn:
            return None
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(query, (id_certificado,))
            row = cur.fetchone()
            cur.close()
            return dict(row) if row else None
        except psycopg2.Error as err:
            logger.error(f"Erro ao buscar certificado {id_certificado}: {err}")
            return None
        finally:
            conn.close()

    # ── Escrita de status ────────────────────────────────────────────────
    def marcar_status(self, id_solicitacao, novo_status, etapa_erro=None,
                      traceback=None, file_url=None):
        """Atualiza id_status (+ campos opcionais) de uma solicitação."""
        campos = ["id_status = %s", "updated_at = NOW()"]
        params = [novo_status]
        if etapa_erro is not None:
            campos.append("etapa_erro = %s")
            params.append(str(etapa_erro)[:255])
        if traceback is not None:
            campos.append("traceback = %s")
            params.append(str(traceback))
        if file_url is not None:
            campos.append("file_url = %s")
            params.append(str(file_url))
        query = (
            f"UPDATE pjdocs_sol_baixa_arquivos SET {', '.join(campos)} WHERE id = %s"
        )
        params.append(id_solicitacao)

        conn = self._get_connection()
        if not conn:
            return False
        try:
            cur = conn.cursor()
            cur.execute(query, tuple(params))
            conn.commit()
            cur.close()
            return True
        except psycopg2.Error as err:
            logger.error(f"Erro ao atualizar status de {id_solicitacao}: {err}")
            conn.rollback()
            return False
        finally:
            conn.close()

    def registrar_sucesso(self, id_solicitacao, file_url, etapa=None):
        """Status 5 (Finalizado com sucesso) + caminho na rede.

        ``etapa`` (opcional) vai para a coluna ``etapa_erro`` como NOTA
        informativa — ex.: documentos que não tinham nada para baixar. Sem nota,
        limpa a coluna (a linha foi um sucesso limpo)."""
        return self.marcar_status(id_solicitacao, 5, file_url=file_url,
                                  etapa_erro=(etapa if etapa is not None else ""))

    def registrar_erro(self, id_solicitacao, etapa, traceback=None):
        """Status 6 (Finalizado com erro) + etapa/traceback."""
        return self.marcar_status(id_solicitacao, 6, etapa_erro=etapa,
                                  traceback=traceback)

    def registrar_sem_eventos(self, id_solicitacao):
        """Status 8 (Sem eventos no período) — a pesquisa não achou arquivos."""
        return self.marcar_status(id_solicitacao, 8,
                                  etapa_erro="sem eventos no período")

    def registrar_parcial(self, id_solicitacao, etapa, file_url=None):
        """Status 21 (Parcialmente Completo) — parte dos documentos ReceitaBX
        baixou e parte falhou. ``etapa`` diz quais documentos falharam."""
        return self.marcar_status(id_solicitacao, 21, etapa_erro=etapa,
                                  file_url=file_url)
