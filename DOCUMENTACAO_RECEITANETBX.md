# Automação de Download de SPED via ReceitanetBX Serviço

> Documentação técnica completa do projeto de migração da automação de baixa de
> arquivos SPED (foco em SPED ECF) — da automação por interface gráfica
> (PyAutoGUI + Tesseract) para integração via **web service SOAP** do
> **ReceitanetBX Serviço**.

---

## Sumário

1. [Contexto e motivação](#1-contexto-e-motivação)
2. [Glossário de termos](#2-glossário-de-termos)
3. [Visão geral da arquitetura](#3-visão-geral-da-arquitetura)
4. [O ReceitanetBX Serviço](#4-o-receitanetbx-serviço)
5. [A API SOAP (descoberta e contrato)](#5-a-api-soap-descoberta-e-contrato)
6. [Os logs do serviço](#6-os-logs-do-serviço)
7. [Fluxo completo de ponta a ponta](#7-fluxo-completo-de-ponta-a-ponta)
8. [A regra de negócio: retificadora](#8-a-regra-de-negócio-retificadora)
9. [Os scripts Python](#9-os-scripts-python)
10. [Configuração do serviço](#10-configuração-do-serviço)
11. [Perfis: Contribuinte vs Procurador](#11-perfis-contribuinte-vs-procurador)
12. [Questões em aberto e próximos passos](#12-questões-em-aberto-e-próximos-passos)
13. [Referência rápida de comandos](#13-referência-rápida-de-comandos)

---

## 1. Contexto e motivação

### 1.1 O problema original

A operação de contabilidade baixa escriturações fiscais e contábeis (SPED ECF,
SPED Contribuições, SPED Contábil/ECD, SPED Fiscal/EFD) da Receita Federal
através do programa **ReceitanetBX**. A automação anterior funcionava assim:

- Um robô em **Python** usando **PyAutoGUI** (automação de cliques/teclado) e
  **Tesseract** (OCR — leitura de texto em imagens) pilotava a **interface
  gráfica (GUI)** do ReceitanetBX.
- O robô abria a tela, selecionava sistema/tipo/período em combos, marcava
  checkboxes, clicava em "Solicitar", lia o número do pedido por OCR, e depois
  navegava a tela de Acompanhamento para baixar os arquivos.

### 1.2 Por que era frágil

Três pontos de fragilidade tornavam a automação instável:

- **Template matching** (localizar botões por imagem na tela): quebra com
  qualquer mudança de resolução, tema ou layout.
- **OCR (Tesseract)**: leitura de texto da tela sujeita a erro de
  reconhecimento; o número do pedido lido errado corrompe o controle.
- **Leitura de checkbox por pixel**: identificar se uma caixa estava marcada
  olhando cor de pixel — extremamente sensível.

Somado a isso, a GUI **travava** quando a base local acumulava muitos registros
(dezenas de milhares de arquivos pendentes), inviabilizando o uso.

### 1.3 A descoberta que mudou tudo

Investigando a lentidão, descobrimos que:

1. A base local do ReceitanetBX GUI é um **banco Apache Derby**, e ela é um
   **espelho re-sincronizado da nuvem da Receita** — apagar registros
   localmente não resolve, pois o programa repopula.
2. Existe uma versão **ReceitanetBX Serviço** (separada da GUI), feita para
   automação: roda como serviço do Windows, baixa em segundo plano e expõe um
   **web service SOAP local** para solicitar arquivos programaticamente.

A migração para o Serviço elimina **as três fragilidades de uma vez**: sem GUI
(nada de template matching), sem OCR (dados vêm em JSON estruturado), sem pixel
de checkbox (a regra roda sobre atributos dos logs).

---

## 2. Glossário de termos

| Termo | Significado |
|-------|-------------|
| **SPED** | Sistema Público de Escrituração Digital. Guarda-chuva das escriturações fiscais/contábeis digitais. |
| **ECF** | Escrituração Contábil Fiscal. Declaração **anual** (um arquivo por ano-calendário). É o foco principal desta automação. |
| **ECD** | Escrituração Contábil Digital (SPED Contábil). |
| **EFD** | Escrituração Fiscal Digital (SPED Fiscal — ICMS/IPI). Mensal. |
| **ReceitanetBX** | Programa da Receita Federal para baixar (BX = "baixa") arquivos SPED transmitidos. |
| **ReceitanetBX GUI** | A versão com interface gráfica (a que a automação antiga pilotava). |
| **ReceitanetBX Serviço** | A versão que roda como serviço Windows, com API SOAP. **É a que usamos agora.** |
| **SOAP** | Protocolo de web service baseado em XML (envelope com corpo). |
| **WSDL** | Web Services Description Language. Arquivo XML que descreve as operações, parâmetros e endereço de um web service SOAP. |
| **XSD** | XML Schema Definition. Define a estrutura/tipos válidos de um XML. |
| **Axis2** | Framework Java (Apache) que hospeda o web service SOAP dentro do Serviço. |
| **Apache Derby** | Banco de dados embarcado em Java, usado internamente pela GUI. |
| **Retificadora** | Versão corrigida de uma escrituração já transmitida. Substitui a "Original" daquele período. |
| **Original** | Primeira versão transmitida de uma escrituração para um período. |
| **Contribuinte** | O CNPJ dono da escrituração (o cliente cujos dados são baixados). |
| **Procurador** | Quem baixa em nome de terceiro, via procuração eletrônica (e-CAC). |
| **NI** | Número de Identificação (CNPJ ou CPF). |
| **Perfil** | Modo de acesso: "Contribuinte" (própria empresa) ou "Procurador" (representando terceiro). |

---

## 3. Visão geral da arquitetura

### 3.1 Fluxo em três etapas

```
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────────────┐
│   1. SOLICITAR  │     │   2. SERVIÇO BAIXA    │     │   3. PROCESSAR      │
│                 │     │                       │     │                     │
│  Python chama   │     │  ReceitanetBX Serviço │     │  Python lê os logs  │
│  a API SOAP     │───▶ │  baixa em background  │───▶ │  JSON, aplica regra │
│  (rota_a.py)    │     │  para bx_temp/        │     │  e move p/ a rede   │
│                 │     │  + grava logs JSON    │     │  (processar_log.py) │
└─────────────────┘     └──────────────────────┘     └─────────────────────┘
     retorna nº              arquivos .txt +               \\192.168.1.238\
     do pedido               logs de download              Dados\{cnpj}\
                             e pedidos                      RECEITABX\ECF\
```

### 3.2 Componentes

- **ReceitanetBX Serviço**: serviço Windows sempre ligado. A cada ciclo
  (configurável, mín. 10 min) varre a nuvem da Receita, baixa o que estiver
  pendente para o certificado configurado, salva arquivos em disco e grava logs
  em JSON.
- **API SOAP local** (`http://127.0.0.1:2443/services/ReceitanetBX`): permite
  **pesquisar** e **solicitar** arquivos programaticamente.
- **Página de status** (`http://127.0.0.1:2444/`): painel HTML de leitura
  (Acompanhamento, Fila de Download, Logs).
- **Scripts Python**: orquestram solicitar → processar → mover.

### 3.3 Princípio central

> O serviço **baixa tudo** que está pendente para o certificado ativo — de
> todos os sistemas (ECF, EFD, ECD, Contribuições) e de todos os clientes com
> pedido em aberto. O **filtro** (só ECF, só o cliente X, regra de retificadora)
> é aplicado **depois**, na etapa de processamento dos logs.

---

## 4. O ReceitanetBX Serviço

### 4.1 Localização da instalação

```
C:\Program Files (x86)\Programas RFB\Receitanet Bx Servico\
```

Componentes relevantes:

- `lib\receitanetbx-ws-1.9.26.jar` — o web service (contém WSDL e XSDs).
- `lib\receitanetbx-core-1.9.26.jar` — lógica de negócio.
- `lib\axis2-*.jar` — framework SOAP.
- `java\bin\` — o JRE embarcado (Java).

### 4.2 Modo de gravação de logs: "Arquivo"

Na configuração do serviço, o **Modo de gravação dos logs** deve estar em
**"Arquivo"** (não "Banco de dados"). No modo Arquivo:

- Os logs são gravados como **JSON** em disco, permitindo leitura incremental.
- As operações de API **Pesquisar Arquivos** e **Solicitar Arquivos**
  continuam funcionando.
- As operações **Situação dos Pedidos** e **Consultar Pedidos** da API ficam
  indisponíveis (substituídas pela leitura dos logs).

Este modo é o recomendado para grande volume de arquivos.

### 4.3 Serviço Windows

O serviço aparece no Windows (services.msc) e deve estar com status **RUNNING**
e inicialização **Automática**.

> **Atenção (PowerShell):** no PowerShell, `sc` é alias de `Set-Content`. Para o
> comando de serviços, use `sc.exe` ou os cmdlets nativos:
>
> ```powershell
> Get-Service | Where-Object { $_.DisplayName -like "*Receitanet*" }
> Start-Service -Name "NOME_DO_SERVICO"
> ```

---

## 5. A API SOAP (descoberta e contrato)

### 5.1 Como foi descoberta

A documentação da API não estava disponível publicamente. Ela foi obtida por
**engenharia reversa** dos artefatos instalados:

1. Identificamos que o Serviço usa **Axis2** (framework SOAP) pelas libs em
   `lib\` (`axis2-kernel`, `wsdl4j`, `axiom`, etc.).
2. Localizamos as portas TCP em escuta com `netstat -ano` → portas **2443**
   (SOAP) e **2444** (painel HTML), ambas do mesmo processo.
3. Extraímos o WSDL e os XSDs de dentro do jar `receitanetbx-ws-1.9.26.jar`
   (que é um ZIP), em `webservices/ReceitanetBX.wsdl` e
   `br/gov/serpro/receitanetbx/webservices/xml/xsd/*.xsd`.
4. Confirmamos o endpoint acessando
   `http://127.0.0.1:2443/services/ReceitanetBX?wsdl`.

### 5.2 Endpoint e operações

- **Endpoint:** `http://127.0.0.1:2443/services/ReceitanetBX`
- **Protocolo:** SOAP 1.1, document/literal, HTTP (não HTTPS).
- **Operações:**
  - `PesquisarArquivos` — lista os arquivos disponíveis (retorna **só IDs**).
  - `SolicitarArquivos` — cria um pedido de download (gera nº de pedido).
  - `VerificarSituacaoPedidos` — status de pedidos.
  - `ConsultarPedidos` — consulta de pedidos.

### 5.3 Contrato: o padrão "entrada" / "retorno" + "saida"

**Descoberta-chave:** todas as operações recebem **um único parâmetro string**
chamado `entrada`, e devolvem `retorno` (int) + `saida` (string). O XML de
negócio real vai **escapado dentro de `entrada`**, e o resultado vem em `saida`.

Ou seja, o SOAP é apenas o "envelope de transporte"; o conteúdo é um XML dentro
de uma string.

**Envelope SOAP de requisição (exemplo para PesquisarArquivos):**

```xml
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:ns="http://ws.apache.org/axis2">
  <soapenv:Header/>
  <soapenv:Body>
    <ns:PesquisarArquivos>
      <ns:entrada><!-- XML de negócio ESCAPADO aqui --></ns:entrada>
    </ns:PesquisarArquivos>
  </soapenv:Body>
</soapenv:Envelope>
```

- **Header HTTP obrigatório:** `SOAPAction: urn:PesquisarArquivos` (ou
  `urn:SolicitarArquivos`).
- **Content-Type:** `text/xml; charset=UTF-8`.

### 5.4 XML de negócio — PesquisarArquivos

Raiz `<pesquisa>`, contendo `<identificacao>` seguido de N `<campo>` **diretos**:

```xml
<pesquisa>
  <identificacao perfil="Contribuinte"
                 sistema="SPED ECF"
                 tipoarquivo="Escrituração"
                 tipopesquisa="Período da Escrituração"/>
  <campo nome="Data de início" valor="01/01/2014"/>
  <campo nome="Data de fim" valor="08/07/2026"/>
</pesquisa>
```

### 5.5 XML de negócio — SolicitarArquivos

Raiz `<pedido>`, com `<identificacao>` + uma **escolha** (`choice`) entre
`<pesquisa>` (por critério) ou `<arquivos>` (por IDs específicos):

**Rota A — por critério (período):**

```xml
<pedido>
  <identificacao perfil="Procurador"
                 sistema="SPED ECF"
                 tipoarquivo="Escrituração"
                 tipopesquisa="Período da Escrituração"
                 nirepresentado="12132146000170"
                 tiponirepresentado="cnpj"/>
  <pesquisa>
    <campo nome="Data de início" valor="01/01/2014"/>
    <campo nome="Data de fim" valor="08/07/2026"/>
  </pesquisa>
</pedido>
```

**Rota B — por IDs específicos:**

```xml
<pedido>
  <identificacao .../>
  <arquivos>
    <arquivo id="17842999"/>
    <arquivo id="15706187"/>
  </arquivos>
</pedido>
```

### 5.6 Atributos de `<identificacao>` (valores confirmados)

| Atributo | Valores válidos | Observação |
|----------|-----------------|------------|
| `perfil` | `Contribuinte` ou `Procurador` | Ver seção 11. |
| `sistema` | `SPED ECF` | (e outros sistemas para outros tipos) |
| `tipoarquivo` | `Escrituração` | |
| `tipopesquisa` | `Período da Escrituração` | **Valor exato** confirmado na GUI. |
| `nirepresentado` | CNPJ do cliente | **Só no perfil Procurador.** Omitido em Contribuinte. |
| `tiponirepresentado` | `cnpj` ou `cpf` | Só no perfil Procurador. |

Nomes dos campos de período (confirmados na GUI): **`Data de início`** e
**`Data de fim`** (com "de", minúsculo, acento no "í").

### 5.7 Formato das respostas

**PesquisarArquivos** — `retorno=1` (sucesso), `saida` com lista de IDs:

```xml
<retornopesquisa>
  <arquivos>
    <arquivo id="17842999"/>
    <arquivo id="15706187"/>
  </arquivos>
  <mensagem>A pesquisa foi processada com sucesso.</mensagem>
</retornopesquisa>
```

> **Limitação importante:** a pesquisa retorna **apenas os IDs**, sem atributos
> (sem Contribuinte, Data, Retificadora). Os atributos só aparecem nos **logs**
> após o serviço processar o pedido. Por isso a regra de retificadora roda na
> etapa de processamento, não na pesquisa.

**SolicitarArquivos** — `retorno=1`, `saida` com o número do pedido no atributo
`id`:

```xml
<retornopedido id="76158203">
  <mensagem>O pedido número 76158203 foi registrado com sucesso.</mensagem>
</retornopedido>
```

> O número do pedido (`id="76158203"`) substitui a leitura por OCR da automação
> antiga.

### 5.8 Erros comuns (mensagens do validador)

O serviço valida contra o XSD e devolve a mensagem em `saida`. Exemplos reais
encontrados durante a calibração:

- `Não foi possível identificar o tipo de pesquisa X` → valor de `tipopesquisa`
  inválido. Correto: `Período da Escrituração`.
- `Data fim deve ser igual ou menor que a data atual` → não usar data futura em
  `Data de fim`. Usar `date.today()`.
- `Foi detectado um conteúdo inválido começando com o elemento 'campo'` →
  estrutura XML errada (campos em posição incorreta).

---

## 6. Os logs do serviço

### 6.1 Localização

```
C:\Users\<usuario>\Documents\bx_temp\logs\
├── download\download-AAAAMMDD.log    ← o que foi baixado + caminho físico
├── pedidos\pedidos-AAAAMMDD.log      ← atributos completos (retificadora, datas)
└── erros\erros-AAAAMMDD.log          ← erros (ex.: sem procuração, sem arquivos)
```

Os arquivos físicos baixados ficam em:

```
C:\Users\<usuario>\Documents\bx_temp\{CNPJ_DO_CERTIFICADO}\
```

> **Atenção:** a subpasta é o CNPJ do **certificado/procurador** (ex.:
> `44189727000134` = Studio Varejo), **não** do cliente. Todos os clientes
> baixados com o mesmo certificado caem juntos nessa pasta. A separação por
> cliente acontece na etapa de mover (usando o `Contribuinte` dos atributos).

### 6.2 Formato do `download-*.log`

Uma linha JSON por arquivo baixado. Contém o **caminho físico** mas **não** os
atributos de negócio:

```json
{
  "timestamp": 1783451043646,
  "idpedido": 76075765,
  "idarquivo": "11778069",
  "nome": "SPEDECF-07906793000151-20220701-20221231-20230307103406.txt",
  "tamanho": 680708,
  "caminhodownload": "C:\\Users\\...\\bx_temp\\44189727000134\\SPEDECF-...txt",
  "hash": "864CB8FB7F60ECE0F54160BE8FD2E336",
  "tipohash": "MD5"
}
```

### 6.3 Formato do `pedidos-*.log`

Uma linha JSON por pedido, com **todos os atributos** de cada arquivo. É a fonte
da regra de retificadora:

```json
{
  "idpedido": 76158203,
  "sistema": "SPED ECF",
  "tipoarquivo": "Escrituração",
  "arquivos": [
    {
      "id": "1085602",
      "hash": "51715BEACF80CF4744C143B9ADBA9F9C",
      "tipohash": "MD5",
      "tamanho": 3438348,
      "atributos": [
        {"nome": "Contribuinte", "valor": "12132146000170", "tipo": "cnpj"},
        {"nome": "Data Início", "valor": "2014-01-01T00:00:00", "tipo": "data"},
        {"nome": "Data Fim", "valor": "2014-12-31T00:00:00", "tipo": "data"},
        {"nome": "Transmissão", "valor": "2015-09-30T14:30:09", "tipo": "data"},
        {"nome": "Retificadora", "valor": "F", "tipo": "booleano"},
        {"nome": "Recibo", "valor": "26DB1938...", "tipo": "texto"}
      ]
    }
  ]
}
```

### 6.4 Chave de cruzamento entre os dois logs: HASH

O `download-*.log` tem `hash` + `caminhodownload`.
O `pedidos-*.log` tem `hash` + atributos.

O **hash (MD5)** é a chave que liga os dois: pelo hash, cruzamos "onde o arquivo
está" (download) com "quais os atributos dele" (pedidos).

### 6.5 Detalhes técnicos críticos dos logs

Dois pontos que causaram bugs e precisam de atenção em qualquer parser:

1. **Encoding: os logs são Latin-1 (ISO-8859-1), NÃO UTF-8.** Ler como UTF-8
   corrompe os acentos ("Data Início" → "Data In�cio", "Transmissão" →
   "Transmiss�o"), quebrando o matching de nomes de atributos. **Sempre ler com
   `encoding="latin-1"`.**

2. **O JSON pode vir quebrado em várias linhas**, inclusive no meio de palavras
   ("Transm\nissão"). O parser não pode ler linha-a-linha ingenuamente; precisa
   reconstruir os objetos JSON contando chaves `{ }` e descartando quebras
   cruas dentro de strings.

3. **Diferença de campo entre sistemas:**
   - **ECF** usa o atributo `Retificadora` com valor `F` (original) ou `V`
     (retificadora).
   - **EFD/PISCOFINS** usa o atributo `Situacão` com valor `Original` ou
     `Retificadora`.
   - O parser trata os dois formatos.

4. **Recibos:** cada arquivo pode ter um par com id terminando em `-REC` (tipo
   "Recibo", ~181 bytes). O parser ignora esses (filtra `Tipo = Escrituração`).

5. **Duplicação:** o mesmo arquivo aparece em vários pedidos (por
   re-solicitações). O parser deduplica por hash.

---

## 7. Fluxo completo de ponta a ponta

### Passo 1 — Solicitar (Python → API SOAP)

```
py rota_a.py
```

O script monta o XML de negócio, envelopa em SOAP, faz POST no endpoint e recebe
o número do pedido. Isso **registra o pedido na Receita**; o download ainda não
aconteceu.

### Passo 2 — Serviço baixa (automático, background)

O serviço, no próximo ciclo (até 10 min), vê o pedido pendente, baixa os
arquivos físicos para `bx_temp\{cnpj_certificado}\` e grava as entradas nos logs
`download-*.log` e `pedidos-*.log`.

Acompanhamento: `http://127.0.0.1:2444/fila/` (fila esvazia conforme baixa).

### Passo 3 — Processar e mover (Python → logs → rede)

```
# Simulação (confere sem mover):
py processar_log.py 20260708 12132146000170

# Mover de verdade:
py processar_log.py 20260708 12132146000170 --mover
```

O script lê os dois logs, cruza por hash, aplica a regra de retificadora e copia
os arquivos mantidos para `\\192.168.1.238\Dados\{cnpj_cliente}\RECEITABX\ECF\`,
com controle de duplicados por hash.

---

## 8. A regra de negócio: retificadora

### 8.1 Enunciado

> Para cada **contribuinte + período** (mesmo par Data Início + Data Fim, ou
> seja, mesmo ano-calendário na ECF):
> - Se existe **retificadora**, mantém a **retificadora mais recente** (maior
>   data de Transmissão) e **descarta as originais** do mesmo período.
> - Se só existe **original**, mantém a original.
>
> **Todos os anos/períodos são preservados** — a regra só desempata versões
> **dentro do mesmo período**. Nunca se descarta um ano inteiro.

### 8.2 Exemplo real (cliente 12132146000170)

O cliente tinha 19 arquivos ECF (2014–2024). Após a regra:

| Ano | Situação | Ação |
|-----|----------|------|
| 2014 | só original | mantém original |
| 2015 | só original | mantém original |
| 2016 | só original | mantém original |
| 2017 | original + retificadora | mantém **retificadora** |
| 2018 | só original | mantém original |
| 2019 | original + retificadora | mantém **retificadora** |
| 2020 | original + retificadora | mantém **retificadora** |
| 2021 | original + 2 retificadoras | mantém **retif. mais recente** |
| 2022 | original + 2 retificadoras | mantém **retif. mais recente (2026-02-06)** |
| 2023 | original + retificadora | mantém **retificadora** |
| 2024 | só original | mantém original |

Resultado: **11 mantidos** (um por ano), **8 descartados** (versões duplicadas).

### 8.3 Onde a regra roda

A regra roda **após o download**, sobre o `pedidos-*.log` — porque a pesquisa
via API **não retorna os atributos** (só os IDs). Só depois de o serviço baixar
é que os atributos (Retificadora, Data, Transmissão) ficam disponíveis nos logs.

Consequência prática: o serviço **baixa todas as versões** (inclusive as que
serão descartadas), e o filtro é aplicado ao mover. As versões descartadas ficam
em `bx_temp` (pasta temporária) e podem ser limpas periodicamente.

---

## 9. Os scripts Python

### 9.1 Inventário

| Script | Função | Gera pedido? |
|--------|--------|--------------|
| `pesquisa.py` | Pesquisa (perfil Contribuinte) — teste/consulta | Não |
| `pesquisa_proc.py` | Pesquisa (perfil Procurador) | Não |
| `rota_a.py` | Solicita por critério/período (perfil Procurador) | **Sim** |
| `solicitar_rota_b.py` | Solicita por IDs específicos | **Sim** |
| `processar_log.py` | Aplica regra de retificadora + move para a rede | Não |

### 9.2 Rota A vs Rota B — qual usar

**Use a Rota A (por critério).** Justificativa:

- A regra de retificadora roda **depois** do download (no `processar_log.py`),
  não na hora de solicitar.
- A pesquisa não retorna os atributos, então não há como saber quais IDs são
  retificadora **antes** de baixar. Passar IDs na Rota B não traria vantagem —
  você teria que passar todos mesmo.
- A Rota B fica reservada para o caso de baixar um arquivo avulso específico.

### 9.3 `processar_log.py` — lógica interna

```
1. carregar_pedidos(pedidos-log, cnpj_filtro)
   - lê em Latin-1, reconstrói JSON multilinha
   - filtra sistema="SPED ECF" e Tipo="Escrituração" (ignora -REC)
   - extrai por arquivo: hash, contribuinte, data_ini, data_fim,
     transmissao, retificadora (F/V ou Original/Retificadora)
   - deduplica por hash
   → dict {hash: info}

2. carregar_downloads(download-log)
   - lê em Latin-1
   → dict {hash: {caminho, nome}}

3. aplicar_regra(pedidos)
   - agrupa por (contribuinte, data_ini, data_fim)
   - se há retificadora no grupo: mantém a de maior transmissao,
     descarta o resto
   - senão: mantém a original de maior transmissao
   → (lista_manter, lista_descartar)

4. [modo --mover] mover_arquivos(manter, caminhos)
   - para cada mantido, destino = DEST_REDE\{contribuinte}\RECEITABX\ECF
   - copia (shutil.copy2), cria pastas se preciso
   - registra hash em movidos.txt (controle de duplicados)
```

### 9.4 Configurações no topo do `processar_log.py`

```python
LOG_BASE  = r"C:\Users\rodrigo.fechner\Documents\bx_temp\logs"
DEST_REDE = r"\\192.168.1.238\Dados"   # destino: DEST_REDE\{cnpj}\RECEITABX\ECF
CONTROLE  = r"C:\Users\rodrigo.fechner\Desktop\bx_api\movidos.txt"
SISTEMA_ALVO = "SPED ECF"
TIPO_ALVO    = "Escrituração"
```

### 9.5 Uso do `processar_log.py`

```bash
# Simulação (padrão — imprime MANTER/DESCARTAR, não move):
py processar_log.py                          # logs de hoje
py processar_log.py 20260708                 # data específica
py processar_log.py 20260708 12132146000170  # data + CNPJ

# Mover de verdade:
py processar_log.py 20260708 12132146000170 --mover
```

O modo `--mover` é explícito e opcional; o padrão é sempre simulação, por
segurança.

---

## 10. Configuração do serviço

Feita no **Configurador do ReceitanetBX Serviço** (interface separada):

| Campo | Valor usado | Observação |
|-------|-------------|------------|
| Certificado — armazenamento | Pelo caminho | Recomendado pela Receita. |
| Caminho do certificado | `.pfx`/`.p12` do procurador | Um certificado por instância. |
| Senha do certificado | (senha do pfx) | |
| Intervalo de atualização | 10 min | Mínimo permitido. |
| Local de gravação | pasta sem acento/espaço | Ex.: onde caem os `bx_temp`. |
| Downloads simultâneos | 1 (teste) | Aumentar depois. |
| Subdiretórios (agrupamento) | `NI` | Agrupa por CNPJ do **solicitante/certificado**. |
| Porta de publicação | 2444 | Painel HTML de status. |
| Cache do banco | 40 MB | Mínimo exigido. |
| Salvar log para depuração | **Desmarcado** | Gera log gigante (stderr), não usar. |
| **Modo de gravação dos logs** | **Arquivo** | **Essencial** para os logs JSON. |

> A porta **2443** (SOAP) é aberta automaticamente pelo serviço, em par com a
> 2444.

---

## 11. Perfis: Contribuinte vs Procurador

### 11.1 Contribuinte

A empresa baixa a **própria** escrituração. O CNPJ do certificado é o mesmo do
contribuinte. No XML, **não** se usa `nirepresentado`:

```xml
<identificacao perfil="Contribuinte" sistema="SPED ECF"
               tipoarquivo="Escrituração"
               tipopesquisa="Período da Escrituração"/>
```

### 11.2 Procurador

Baixa em nome de terceiro (cliente), via **procuração eletrônica** cadastrada no
e-CAC. O certificado é do procurador; o cliente vai em `nirepresentado`:

```xml
<identificacao perfil="Procurador" sistema="SPED ECF"
               tipoarquivo="Escrituração"
               tipopesquisa="Período da Escrituração"
               nirepresentado="12132146000170"
               tiponirepresentado="cnpj"/>
```

### 11.3 Ponto crítico do serviço

O **serviço usa um único certificado fixo** (o configurado no Configurador).
Diferente da GUI, não dá para escolher o certificado por chamada. Portanto:

- Para clientes representados por um mesmo procurador → basta variar
  `nirepresentado`.
- Para clientes de **procuradores diferentes** → é preciso trocar o certificado
  do serviço, ou rodar **múltiplas instâncias** do serviço (uma por
  certificado). Ver seção 12.

---

## 12. Questões em aberto e próximos passos

### 12.1 Multi-certificado (arquitetura)

O maior ponto em aberto. Como o serviço tem **um certificado fixo**, para
atender vários procuradores (Studio Varejo, Studio Agronegócios, SPACEW, etc.) é
preciso decidir entre:

- **Opção A:** rodar **N instâncias** do serviço, uma por certificado, cada uma
  em sua porta/pasta.
- **Opção B:** trocar o certificado do serviço programaticamente conforme o lote
  de clientes de cada procurador (reconfigurar + reiniciar o serviço entre
  lotes).

Recomendação preliminar: **Opção A** (instâncias dedicadas) é mais estável para
operação contínua, ao custo de mais recursos.

### 12.2 Integração com a fila de trabalho

Os scripts hoje são executados manualmente para um CNPJ. O próximo passo é
orquestrá-los a partir da fila de solicitações (tabela
`pjdocs_sol_baixa_arquivos` ou equivalente), processando vários clientes em
sequência:

```
para cada solicitação na fila:
    rota_a.solicitar(cnpj_cliente, certificado_do_procurador)
    (aguardar ciclo do serviço)
    processar_log.processar(cnpj_cliente, mover=True)
    marcar solicitação como concluída
```

### 12.3 Tratamento de casos de erro

Mapear as mensagens do `erros-*.log` para os estados de negócio:

- **Sem procuração** → cliente sem procuração eletrônica válida.
- **Sem arquivos** → pesquisa retorna vazio (nada transmitido no período).

Esses casos apareciam como popups na automação antiga; agora vêm no log de
erros e precisam ser lidos/tratados.

### 12.4 Limpeza da pasta temporária

Como o serviço baixa **todas** as versões (inclusive as descartadas pela regra),
a pasta `bx_temp` acumula. Definir uma rotina de limpeza periódica dos arquivos
já processados/movidos.

### 12.5 Suporte a outros sistemas

Hoje o foco é **SPED ECF**. O mesmo mecanismo serve para ECD, EFD e
Contribuições, ajustando `sistema` e `tipoarquivo` na identificação e o filtro
no `processar_log.py`. As regras de negócio (retificadora) podem diferir por
sistema.

---

## 13. Referência rápida de comandos

### 13.1 Solicitar (gera pedido real)

```bash
py rota_a.py
# saída: NUMERO DO PEDIDO: 76158203
```

### 13.2 Acompanhar o download

```
Navegador: http://127.0.0.1:2444/fila/     (fila em andamento)
Navegador: http://127.0.0.1:2444/           (status geral)
```

### 13.3 Processar (simulação e mover)

```bash
py processar_log.py 20260708 12132146000170            # simula
py processar_log.py 20260708 12132146000170 --mover    # move p/ rede
```

### 13.4 Diagnóstico

```powershell
# Serviço está rodando?
Get-Service | Where-Object { $_.DisplayName -like "*Receitanet*" }

# Portas do serviço em escuta (2443 SOAP, 2444 HTTP)
netstat -ano | findstr LISTENING | findstr "2443 2444"

# Confirmar endpoint SOAP
curl "http://127.0.0.1:2443/services/ReceitanetBX?wsdl"

# Ver logs de hoje (lembrar: Latin-1)
Get-Content "C:\Users\rodrigo.fechner\Documents\bx_temp\logs\pedidos\pedidos-20260708.log" -TotalCount 2
```

### 13.5 Teste da API (via curl, exemplo de pesquisa)

O corpo real é o XML de negócio escapado dentro de `<entrada>`. Na prática, é
mais simples usar os scripts Python, que montam o envelope automaticamente.

---

## Apêndice A — Histórico da descoberta

Resumo cronológico de como a solução foi construída (útil para entender decisões):

1. **Problema inicial:** GUI travando por acúmulo na base local (Apache Derby).
2. **Tentativa 1 (descartada):** limpar a base Derby via `ij`. Descobriu-se que
   a base é espelho da nuvem e repopula — limpar não resolve.
3. **Virada:** identificação do ReceitanetBX Serviço como caminho correto.
4. **Configuração** do serviço em modo Arquivo, com certificado do procurador.
5. **Validação do download automático:** o serviço baixou 719+ arquivos
   pendentes sozinho, confirmando o mecanismo.
6. **Engenharia reversa da API:** Axis2 → portas 2443/2444 → WSDL/XSD extraídos
   do jar → endpoint `/services/ReceitanetBX`.
7. **Calibração dos parâmetros** por tentativa guiada pelas mensagens de erro do
   validador (tipopesquisa, nomes dos campos, data não-futura).
8. **Pesquisa e Solicitação validadas** (Contribuinte e Procurador), com número
   de pedido retornado estruturado.
9. **Regra de retificadora:** descoberto que a pesquisa não traz atributos →
   regra movida para pós-download, lendo o `pedidos-*.log`.
10. **Bugs de parsing resolvidos:** encoding Latin-1 e JSON multilinha.
11. **Modo `--mover`** com controle de duplicados por hash. Fluxo completo.

---

## Apêndice B — Estrutura de pastas de referência

```
C:\Program Files (x86)\Programas RFB\
├── Receitanet BX\                    (GUI — legado)
└── Receitanet Bx Servico\            (Serviço — atual)
    ├── lib\receitanetbx-ws-1.9.26.jar
    ├── lib\receitanetbx-core-1.9.26.jar
    └── java\bin\                      (JRE embarcado)

C:\Users\<usuario>\Documents\bx_temp\
├── {CNPJ_CERTIFICADO}\               (arquivos físicos baixados — misturados)
│   └── SPEDECF-....txt
└── logs\
    ├── download\download-AAAAMMDD.log
    ├── pedidos\pedidos-AAAAMMDD.log
    └── erros\erros-AAAAMMDD.log

C:\Users\<usuario>\Desktop\bx_api\    (scripts Python)
├── rota_a.py
├── solicitar_rota_b.py
├── pesquisa_proc.py
├── processar_log.py
└── movidos.txt                       (controle de duplicados)

\\192.168.1.238\Dados\                 (destino final na rede)
└── {CNPJ_CLIENTE}\RECEITABX\ECF\
    └── SPEDECF-....txt                (só os mantidos pela regra)
```
