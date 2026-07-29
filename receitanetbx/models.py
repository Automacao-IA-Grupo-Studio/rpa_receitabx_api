"""
models.py — Estruturas de dados de domínio (dataclasses).

Centraliza os "objetos" que circulam entre as camadas, evitando dicionários
soltos. Facilita a leitura e a futura serialização para o banco de dados.
"""

from dataclasses import dataclass, field
from typing import Optional

from . import config


@dataclass
class Identificacao:
    """Bloco <identificacao> comum a pesquisa e pedido.

    No perfil Contribuinte, ``nirepresentado`` fica None (não é enviado).
    No perfil Procurador, ``nirepresentado`` recebe o CNPJ do cliente.
    """
    perfil: str = config.PERFIL_PADRAO
    sistema: str = config.SISTEMA
    tipoarquivo: str = config.TIPOARQUIVO
    tipopesquisa: str = config.TIPOPESQUISA
    nirepresentado: Optional[str] = None
    tiponirepresentado: Optional[str] = None

    @classmethod
    def procurador(cls, nirepresentado: str, tipo_ni: str = config.TIPO_NI_PADRAO,
                   scfg: dict = None):
        """Fábrica para o perfil Procurador (representando um cliente).

        ``scfg``: config do sistema (define sistema/tipoarquivo/tipopesquisa).
        Sem scfg, usa os padrões globais (ECF, retrocompatível)."""
        scfg = scfg or {}
        return cls(
            perfil="Procurador",
            sistema=scfg.get("sistema", config.SISTEMA),
            tipoarquivo=scfg.get("tipoarquivo", config.TIPOARQUIVO),
            tipopesquisa=scfg.get("tipopesquisa", config.TIPOPESQUISA),
            nirepresentado=nirepresentado,
            tiponirepresentado=tipo_ni,
        )

    @classmethod
    def contribuinte(cls, scfg: dict = None):
        """Fábrica para o perfil Contribuinte (própria empresa)."""
        scfg = scfg or {}
        return cls(
            perfil="Contribuinte",
            sistema=scfg.get("sistema", config.SISTEMA),
            tipoarquivo=scfg.get("tipoarquivo", config.TIPOARQUIVO),
            tipopesquisa=scfg.get("tipopesquisa", config.TIPOPESQUISA),
        )


@dataclass
class ArquivoSped:
    """Um arquivo SPED com seus atributos de negócio (vindos do pedidos-log)
    e, após o cruzamento por hash, o caminho físico (vindo do download-log)."""
    hash: str
    id: str
    contribuinte: str
    data_ini: str
    data_fim: str
    transmissao: str
    retificadora: bool
    scp: str = ""                      # SCP (CNPJ dentro do CNPJ); "" = sem SCP
    situacao: str = ""                 # ECD "Situação SPED" (AUTENTICADA,
                                       # SUBSTITUÍDA, RECEBIDA, SOB EXIGÊNCIA,
                                       # INDEFERIDA...). A regra_situacao usa isto
                                       # p/ escolher a versão do período. "" nos
                                       # demais (ECF/PISCOFINS/ICMS não têm).
    tamanho: Optional[int] = None
    caminho: Optional[str] = None      # preenchido no cruzamento com download-log
    nome: Optional[str] = None

    @property
    def periodo(self) -> tuple:
        """Chave de agrupamento da regra de retificadora: contribuinte + SCP +
        período. O SCP é uma 'CNPJ dentro da CNPJ' (ex.: sócia em SCP) — quando
        presente, cada SCP tem sua PRÓPRIA disputa retificadora × original,
        separada do contribuinte principal (SCP vazio) e dos demais SCPs. Sem
        SCP, o campo é "" e o agrupamento fica igual ao de antes."""
        return (self.contribuinte, self.scp, self.data_ini, self.data_fim)


@dataclass
class ResultadoPesquisa:
    """Retorno de PesquisarArquivos (a API devolve apenas os IDs)."""
    retorno: Optional[str]
    saida: Optional[str]
    http_status: int
    ids: list = field(default_factory=list)

    @property
    def sucesso(self) -> bool:
        return self.retorno == "1"


@dataclass
class ResultadoPedido:
    """Retorno de SolicitarArquivos (gera o número do pedido)."""
    retorno: Optional[str]
    saida: Optional[str]
    http_status: int
    numero_pedido: Optional[str] = None

    @property
    def sucesso(self) -> bool:
        return self.retorno == "1" and self.numero_pedido is not None


@dataclass
class ResumoMovimentacao:
    """Contadores da etapa de mover arquivos para a rede."""
    copiados: int = 0
    pulados: int = 0
    erros: int = 0
