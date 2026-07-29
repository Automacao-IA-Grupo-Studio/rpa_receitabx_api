# bx_api — Automação de Download de SPED (ReceitanetBX Serviço)

Automação da baixa de escriturações da Receita Federal via o
**web service SOAP** do **ReceitanetBX Serviço**.

O contexto completo, o contrato da API e as regras de negócio estão em
[DOCUMENTACAO_RECEITANETBX.md](DOCUMENTACAO_RECEITANETBX.md). Este README foca na
**arquitetura do código** e em **como usar**.

---

## Fluxo em 3 etapas

```
┌─────────────┐     ┌──────────────────────┐     ┌────────────────────┐
│ 1. SOLICITAR│     │ 2. SERVIÇO BAIXA      │     │ 3. PROCESSAR       │
│ Python →    │ ──▶ │ ReceitanetBX Serviço  │ ──▶ │ Python lê logs,    │
│ API SOAP    │     │ baixa em background   │     │ aplica regra e     │
│ (solicitar) │     │ + grava logs JSON     │     │ move p/ a rede     │
└─────────────┘     └──────────────────────┘     └────────────────────┘
```

1. **Solicitar** — o Python registra o pedido na Receita (API SOAP local).
2. **Serviço baixa** — o serviço Windows, no próximo ciclo (até 10 min), baixa
   os arquivos e grava os logs em `bx_temp/logs`.
3. **Processar** — o Python lê os logs, aplica a **regra de retificadora** e
   copia os arquivos mantidos para a rede.

**Princípio central:** o serviço baixa *tudo* que está pendente para o
certificado ativo. O filtro (só ECF, cliente X, regra de retificadora) é
aplicado **depois**, na etapa de processamento — porque a pesquisa da API
retorna apenas IDs, sem os atributos de negócio.

---

## Estrutura do projeto

```
bx_api/
├── main.py                     ← CLI único (pesquisar / solicitar / processar)
├── requirements.txt            ← dependências (requests)
├── movidos.txt                 ← controle de duplicados (hashes já movidos)
├── README.md
├── DOCUMENTACAO_RECEITANETBX.md ← documentação técnica de contexto
├── receitanetbx/               ← pacote principal (lógica reutilizável)
│   ├── __init__.py
│   ├── config.py               ← configuração central (1 fonte de verdade)
│   ├── models.py               ← dataclasses de domínio
│   ├── soap_client.py          ← transporte SOAP genérico
│   ├── xml_builder.py          ← montagem do XML de negócio
│   ├── operacoes.py            ← pesquisar / solicitar (alto nível)
│   ├── log_parser.py           ← leitura robusta dos logs
│   ├── retificadora.py         ← regra de negócio (manter/descartar)
│   └── processador.py          ← cruza logs + aplica regra + move
└── tests/                      ← testes das partes delicadas (rodam sem pytest)
    ├── test_retificadora.py    ← regra + desempate por transmissão
    └── test_log_parser.py      ← Latin-1 + JSON multilinha + ignora -REC
```

### Padrão de arquitetura

O projeto segue uma separação em camadas típica de automação, para deixar a
lógica **testável** e a futura **integração com banco de dados** trivial:

- **Configuração isolada** (`config.py`) — hoje com valores fixos (fase de
  validação); quando o banco entrar, só esta fonte muda.
- **Domínio em dataclasses** (`models.py`) — nada de dicionários soltos
  circulando entre módulos.
- **Transporte separado da regra** — `soap_client` e `xml_builder` não sabem
  nada de negócio; `retificadora` não sabe nada de SOAP nem de arquivos.
- **CLI fino** (`main.py`) — só interpreta argumentos e imprime; toda a lógica
  vive no pacote e pode ser chamada por um futuro orquestrador de fila.

---

## Descrição de cada arquivo Python

### `main.py` — ponto de entrada (CLI)
Interpreta a linha de comando via `argparse` com três subcomandos
(`pesquisar`, `solicitar`, `processar`), chama as funções do pacote e formata a
saída para o terminal. **Não contém regra de negócio** — é apenas a camada de
apresentação. Substitui os antigos scripts soltos (`rota_a.py`, `rota_b.py`,
`pesquisa_proc.py`, `processar_log.py`).

### `receitanetbx/config.py` — configuração central
Fonte única de verdade: endpoint SOAP e portas, timeout HTTP, parâmetros
padrão de identificação (perfil, sistema, tipo de arquivo, tipo de pesquisa),
período padrão, filtros de processamento (`SISTEMA_ALVO`, `TIPO_ALVO`) e todos
os caminhos (logs, destino na rede, arquivo de controle). É o único arquivo que
precisará mudar quando os parâmetros vierem do banco de dados.

### `receitanetbx/models.py` — modelos de domínio
Dataclasses que trafegam entre as camadas:
- `Identificacao` — o bloco `<identificacao>`, com fábricas `procurador()` e
  `contribuinte()` (a diferença está em enviar ou não `nirepresentado`).
- `ArquivoSped` — um arquivo com atributos de negócio (hash, contribuinte,
  período, transmissão, retificadora) e o caminho físico após o cruzamento.
- `ResultadoPesquisa`, `ResultadoPedido`, `ResumoMovimentacao` — retornos
  estruturados das operações, com `.sucesso` para checagem rápida.

### `receitanetbx/soap_client.py` — cliente SOAP genérico
Isola todo o transporte SOAP. Sabe do padrão peculiar da API: cada operação
recebe um único parâmetro string `entrada` (com o XML de negócio escapado
dentro) e devolve `retorno` (int) + `saida` (string). A função `chamar(operacao,
xml)` monta o envelope, faz o POST com os headers corretos (`SOAPAction`,
`Content-Type`) e extrai `(retorno, saida, http_status)`. Nenhuma regra aqui.

### `receitanetbx/xml_builder.py` — montagem do XML de negócio
Constrói os três formatos de XML que vão dentro de `<entrada>`, sempre com os
valores escapados:
- `pesquisa(...)` — raiz `<pesquisa>` (para PesquisarArquivos).
- `pedido_por_periodo(...)` — **Rota A**: `<pedido>` com `<pesquisa>` aninhada.
- `pedido_por_ids(...)` — **Rota B**: `<pedido>` com lista `<arquivos>`.

### `receitanetbx/operacoes.py` — operações de alto nível
Combina `xml_builder` + `soap_client` e devolve resultados estruturados
(`models`), sem imprimir:
- `pesquisar(...)` — PesquisarArquivos (lista IDs; não gera pedido).
- `solicitar_por_periodo(...)` — Rota A (gera pedido real).
- `solicitar_por_ids(...)` — Rota B (gera pedido real).
Também extrai os IDs da pesquisa e o número do pedido da solicitação.

### `receitanetbx/log_parser.py` — leitura robusta dos logs
Lê os dois logs do serviço tratando os dois problemas conhecidos:
1. **Encoding Latin-1** (não UTF-8) — senão os acentos dos nomes de atributos
   corrompem e o matching quebra.
2. **JSON quebrado em várias linhas** (inclusive no meio de palavras) —
   reconstrói os objetos contando chaves `{ }` e descartando quebras cruas
   dentro de strings.
Produz `ArquivoSped` deduplicados por hash, ignora recibos (`-REC`) e trata os
dois formatos de retificadora (ECF `F/V` e EFD `Original/Retificadora`).

### `receitanetbx/retificadora.py` — a regra de negócio
Função pura `aplicar_regra(arquivos)`. Agrupa por
`(contribuinte, data_início, data_fim)` e, em cada grupo: se há retificadora,
mantém a mais recente (maior data de transmissão) e descarta o resto; senão,
mantém a original mais recente. **Todos os períodos são preservados** — só
desempata versões dentro do mesmo período. Retorna `(manter, descartar)`.

### `receitanetbx/processador.py` — orquestração da etapa 3
Amarra tudo: carrega os dois logs, **cruza por hash** (enriquece cada
`ArquivoSped` com o caminho físico), aplica a regra e — no modo `--mover` —
copia os mantidos para `DEST_REDE/{cnpj}/RECEITABX/ECF`, com controle de
duplicados por hash (`movidos.txt`). Usa o CNPJ do **contribuinte** (não o do
certificado) para separar os clientes.

---

## Instalação

```bash
pip install -r requirements.txt
```

Requer Python 3.9+ e o **ReceitanetBX Serviço** rodando com **Modo de gravação
dos logs = Arquivo** (ver documentação, seção 10).

---

## Uso

### 1. Pesquisar (consulta — não gera pedido)
```bash
py main.py pesquisar --cnpj 07906793000151
```

### 2. Solicitar (gera pedido real na Receita)
```bash
# Rota A — por período (uso padrão):
py main.py solicitar --cnpj 12132146000170

# Rota B — por IDs específicos:
py main.py solicitar --cnpj 07906793000151 --ids 17842999 15706187
```
Retorna o **número do pedido**. Acompanhe o download em
`http://127.0.0.1:2444/fila/`.

### 3. Processar (aplica a regra e move)
```bash
# Simulação (padrão — imprime MANTER/DESCARTAR, não move nada):
py main.py processar 20260708 12132146000170

# Mover de verdade:
py main.py processar 20260708 12132146000170 --mover
```
`data` e `cnpj` são opcionais (padrão: logs de hoje, todos os CNPJs). O modo
`--mover` é explícito, por segurança — o padrão é sempre simulação.

Veja `py main.py <comando> --help` para todas as opções.

---

## Testes

As partes que mais custaram para acertar têm testes dedicados — em especial o
desempate por data de **Transmissão** (caso "3 versões do mesmo ano") e o parser
(Latin-1 + JSON quebrado no meio de palavra). Rodam com ou sem pytest:

```bash
py -m pytest tests            # se tiver pytest instalado
py tests/test_retificadora.py # standalone, sem dependências extras
py tests/test_log_parser.py
```

---

## Diagnóstico rápido (PowerShell)

```powershell
# Serviço está rodando?
Get-Service | Where-Object { $_.DisplayName -like "*Receitanet*" }

# Portas em escuta (2443 SOAP, 2444 painel)
netstat -ano | findstr LISTENING | findstr "2443 2444"

# Endpoint SOAP responde?
curl "http://127.0.0.1:2443/services/ReceitanetBX?wsdl"
```

---

## Próximos passos (ver documentação, seção 12)

- **Integração com a fila de trabalho** — trocar os valores fixos de
  `config.py` por dados vindos do banco e orquestrar vários clientes em
  sequência (o CLI já expõe tudo por parâmetro; o pacote é chamável direto).
- **Multi-certificado** — o serviço usa um único certificado fixo; atender
  vários procuradores exige múltiplas instâncias ou troca de certificado.
- **Tratamento de erros** — mapear o `erros-*.log` (sem procuração / sem
  arquivos) para estados de negócio.
- **Limpeza da pasta temporária** — as versões descartadas ficam em `bx_temp`.
- **Outros sistemas** — ECD, EFD e Contribuições, ajustando `sistema`/
  `tipoarquivo` em `config.py` e o filtro no processamento.
