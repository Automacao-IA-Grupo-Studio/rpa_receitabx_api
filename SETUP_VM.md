# SETUP_VM — Runbook de migração do bx_api para a VM

> **Para o Claude da VM:** este documento é um runbook. Execute os passos **na ordem**,
> validando cada um antes de seguir. **NÃO rode solicitações reais** (que criam pedidos
> na Receita) sem o usuário autorizar — só os comandos de **validação read-only**
> marcados abaixo. O projeto já está com a lógica pronta e validada na máquina de
> origem; aqui é só **adaptar o que é específico da máquina** e conferir.

## Contexto (resumo de 30s)

`bx_api` automatiza o download de documentos SPED (ECF, PISCOFINS, ECD, ICMS) via o
**serviço Windows ReceitanetBX** (SOAP em `localhost:2443`), orquestrado por uma fila
no banco **AUTOMATAX** (PostgreSQL). Fluxo: solicita (cria pedido) → o serviço baixa
em background → o Python lê os logs, aplica a regra por documento e move pra rede.

- `id_tipo_arquivo = 5` no AUTOMATAX = **ReceitaBX** = os 4 documentos.
- Modo principal: `py main.py orquestrar --executar --sistema todos`.
- A senha do certificado é gravada **criptografada** no `.properties`, com **chave
  derivada do MAC da máquina** → por isso a migração exige o passo 3 abaixo.

Dados desta VM (dos prints do usuário):
- Usuário Windows: **`automacao`**
- Pasta de download configurada no serviço: `C:\Users\automacao\Documents\download_bx`
- Certificado de referência: `C:\Users\automacao\Desktop\Certificado - PJ Studio Varejo.pfx` (Studio Varejo = id 4)
- Diretório do serviço: `C:\Program Files (x86)\Programas RFB\Receitanet Bx Servico`

---

## Passo 1 — Dependências Python

```powershell
cd <pasta do projeto na VM>
py -m pip install -r requirements.txt
```
Confere que instalou: `requests`, `psycopg2-binary`, `python-dotenv`, `pycryptodome`.
Teste rápido de import:
```powershell
py -c "import requests, psycopg2, dotenv; from Crypto.Cipher import DES3; print('deps OK')"
```

---

## Passo 2 — Ajustar caminhos no `receitanetbx/config.py`

Troque os caminhos da máquina antiga (`rodrigo.fechner`) pelos da VM (`automacao`).

**2a. Pasta de download local (`BX_TEMP_BASE`)** — a atual não existe na VM:
- Procure: `BX_TEMP_BASE = Path(r"C:\Users\rodrigo.fechner\Documents\bx_temp")`
- Troque para: `BX_TEMP_BASE = Path(r"C:\Users\automacao\Documents\download_bx")`

**2b. Pasta dos certificados (`CERT_DIR`)** — precisa conter **todos** os `.pfx`:
- Procure: `CERT_DIR = Path(r"C:\Users\rodrigo.fechner\Desktop\certificados")`
- Troque para: `CERT_DIR = Path(r"C:\Users\automacao\Desktop\certificados")`
- **Ação do usuário:** copiar TODOS os arquivos `.pfx` (de todos os certificados que
  serão usados: ids 1, 3, 4, 6...) para essa pasta `C:\Users\automacao\Desktop\certificados`
  (crie a pasta). O nome de cada arquivo deve bater com `pfx_filename` no banco.

**2c. Destino na rede (`DEST_REDE`)** — confirme que a VM alcança o compartilhamento:
- Valor atual (provavelmente mantém): `DEST_REDE = Path(r"\\192.168.1.238\Dados")`
- **Validação obrigatória** (a VM PRECISA ter acesso de escrita):
  ```powershell
  New-Item -ItemType File "\\192.168.1.238\Dados\_teste_vm.txt" -Force; Remove-Item "\\192.168.1.238\Dados\_teste_vm.txt"
  ```
  Se der erro de acesso/rede, resolva o mapeamento/credenciais do share ANTES de rodar.

**2d. Diretório do serviço (`SERVICO_DIR`/`PROPERTIES_PATH`)** — confirme que existe:
  ```powershell
  Test-Path "C:\Program Files (x86)\Programas RFB\Receitanet Bx Servico\recnetbx-service.properties"
  ```
  Se `True`, não precisa mexer. Se instalou em outro lugar, ajuste `SERVICO_DIR`.

> ⚠️ **NÃO altere** os parâmetros por-sistema em `config.SISTEMAS` (tipoarquivo,
> tipopesquisa, campos, etc.). Eles foram **confirmados na Receita** e alguns têm
> grafia proposital que parece erro mas está CORRETA — ex.: ICMS usa
> `"Por Período da Escrituracao"` (sem ç/ã em "Escrituracao") e campos `"Data Inicio"/"Data Fim"`
> (sem "de", sem acento). Ver a tabela no fim deste doc.

---

## Passo 3 — 🔑 Regenerar o oráculo da criptografia (CRÍTICO — específico do MAC)

A chave de criptografia da senha vem do **MAC da placa de rede**. O valor atual de
`CRIPTO_ORACULO_CIFRA` foi gerado na máquina antiga e **não vai funcionar na VM**
(MAC diferente) → o orquestrador aborta com *"não foi possível determinar a chave"*.

**3a.** Garanta que o usuário **configurou o certificado Studio Varejo (id 4) no
configurador do ReceitanetBX e clicou em `Salvar`** (o serviço grava a senha
criptografada com o MAC da VM). O certificado de referência DEVE ser o id 4 para
casar com `CRIPTO_ORACULO_CERT_ID = 4`.

**3b.** Leia o novo valor gravado:
```powershell
Get-Content "C:\Program Files (x86)\Programas RFB\Receitanet Bx Servico\recnetbx-service.properties" | Select-String senhaCertificadoDigital
```
Vai sair `senhaCertificadoDigital=XXXX....==` (pode ter `\=` escapado no fim — ao copiar
para o config, use o valor **desescapado**, ex.: `...==`, não `...\=\=`).

**3c.** Em `config.py`, troque:
- Procure: `CRIPTO_ORACULO_CIFRA = "gVD8sOLkCiGMU3asm8dCJGqXgQUZfNd9"`
- Troque o valor pela cifra lida no 3b.

**3d.** Valide que o oráculo bate (isto NÃO toca em nada — só testa a chave):
```powershell
py -c "from receitanetbx import config, cripto_senha; from database.db_handler import DBHandler; o=DBHandler().buscar_certificado(config.CRIPTO_ORACULO_CERT_ID); k=cripto_senha.descobrir_chave(o['pfx_password'], config.CRIPTO_ORACULO_CIFRA); print('CRIPTO OK — chave de', len(k), 'bytes')"
```
- Saída `CRIPTO OK — chave de 24 bytes` → pronto.
- Se `RuntimeError` → a cifra copiada está errada (confira escape `\=`), ou o
  certificado salvo no GUI não é o id 4, ou o `pfx_password` do banco difere da senha
  digitada no GUI. Resolva antes de seguir.

---

## Passo 4 — Arquivos a copiar da máquina antiga (o usuário faz)

- **`.env`** (raiz do projeto): credenciais do banco AUTOMATAX (`DB_USER/PASSWORD/HOST/NAME/PORT`).
  Confirme que veio no zip. Sem ele, sem banco. Teste a conexão:
  ```powershell
  py -c "from database.db_handler import DBHandler; print('DB OK' if DBHandler().connect() else 'DB FALHOU')"
  ```
- **`.pfx`** dos certificados → para `CERT_DIR` (passo 2b).
- **`movidos.txt`** (raiz do projeto): controle de duplicados (hashes já enviados à
  rede). **Se copiar**, a VM não re-copia o que já está na rede. **Se não copiar**, a
  VM re-copia tudo pra rede na primeira passada (sobrescreve, não duplica, mas gasta
  tempo/banda). Recomendado copiar.

---

## Passo 5 — Conferir o serviço ReceitanetBX

- Serviço rodando e portas no ar:
  ```powershell
  Get-Service -Name ReceitanetBX
  (Invoke-WebRequest "http://127.0.0.1:2443/services/ReceitanetBX?wsdl" -UseBasicParsing).StatusCode  # espera 200
  ```
- No configurador: **Modo de gravação dos logs = Arquivo** (o parser lê logs em
  arquivo, não banco).
- O orquestrador reescreve o `.properties` (em Program Files) e reinicia o serviço,
  então **precisa rodar elevado (Administrador)**. O `main.py` já pede UAC no
  `--executar`; garanta que a VM permite a elevação.

---

## Passo 6 — Validação final (tudo read-only, NÃO cria pedido)

1. **Sintaxe/imports do projeto:**
   ```powershell
   py -c "from receitanetbx import config, orquestrador, operacoes, processador, log_parser; print('imports OK')"
   ```
2. **Dry-run do orquestrador** (não toca em serviço/banco/Receita — só mostra o plano):
   ```powershell
   py main.py orquestrar --sistema todos --cert 4
   ```
   Espera-se ver `Modo ReceitaBX completo: N linha(s) pendente(s)` e o grupo do Studio Varejo.
3. **Pesquisa read-only** (confirma que o serviço + certificado carregado respondem;
   NÃO gera pedido). Use um CNPJ que o Varejo representa:
   ```powershell
   py main.py pesquisar --cnpj 07906793000151
   ```
   `retorno : 1` (ou mensagem de negócio como "sem procuração"/"nenhum arquivo") = a
   comunicação está OK. Erro de conexão = serviço/porta.

Se 1–3 passarem e o Passo 3d deu `CRIPTO OK`, a VM está pronta. **Pare aqui** e avise o
usuário — a execução real (`--executar`) cria pedidos na Receita e deve ser decidida por ele.

---

## Apêndice — parâmetros por documento (JÁ confirmados; só referência, NÃO alterar)

| Documento | tipoarquivo | tipopesquisa | data início | regra |
|---|---|---|---|---|
| ECF | Escrituração | Período da Escrituração | 01/01/2014 | retificadora (mantém última) |
| PISCOFINS | Escrituração | Período da Escrituração | 01/01/2012 | retificadora mensal |
| ECD | Escrituração Contábil Digital | Por Período da Escrituração | 01/01/2008 | descarta `Situação SPED = SUBSTITUÍDA` |
| ICMS | Escrituração Fiscal Digital | Por Período da Escrituracao | 01/01/2012 | mantém tudo; 2 checkboxes = `V`; filiais → pasta do cliente |

Notas de implementação (não precisa mexer, é só pra entender):
- ECD/ICMS leem o CNPJ do atributo `CNPJ` do log; ECF/PISCOFINS de `Contribuinte`.
- ICMS: campos extras `Buscar Arquivos de Todos os Estabelecimentos=V` e
  `Último arquivo transmitido=V`; filiais (base de 8 dígitos) vão todas na pasta do cliente.
- O serviço baixa TODA a fila do certificado (não dá pra filtrar só "os nossos"); o
  filtro por cliente/documento é aplicado no processamento/move.

## Resumo dos itens de AÇÃO
- [ ] 1. `pip install -r requirements.txt`
- [ ] 2a. `BX_TEMP_BASE` → `C:\Users\automacao\Documents\download_bx`
- [ ] 2b. `CERT_DIR` → `C:\Users\automacao\Desktop\certificados` + copiar os `.pfx`
- [ ] 2c. testar escrita em `\\192.168.1.238\Dados`
- [ ] 2d. confirmar `PROPERTIES_PATH`
- [ ] 3. regenerar `CRIPTO_ORACULO_CIFRA` (salvar cert 4 no GUI → ler `.properties` → validar 3d)
- [ ] 4. `.env`, `.pfx`, `movidos.txt` no lugar; testar conexão do banco
- [ ] 5. serviço no ar (2443), logs em Arquivo, elevação OK
- [ ] 6. validações read-only (dry-run + pesquisa) — depois PARAR e avisar o usuário
