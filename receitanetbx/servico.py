"""
servico.py — Gestão do ReceitanetBX Serviço para o modo multi-certificado.

O serviço usa UM certificado fixo, lido do arquivo de propriedades
``recnetbx-service.properties``. Para atender vários procuradores, o
orquestrador troca o certificado ativo entre lotes:

  1. aplicar_certificado()  → reescreve as chaves do .properties (cert, senha,
     pasta de gravação, intervalo e downloads simultâneos), preservando todas
     as demais linhas e comentários;
  2. reiniciar()            → Restart-Service (exige processo elevado/admin);
  3. aguardar_online()      → espera o endpoint SOAP responder ao WSDL.

Nada de regra de negócio aqui — só configuração e ciclo de vida do serviço.
"""

import subprocess
import time

import requests

from . import config, cripto_senha

# Chaves do .properties que o orquestrador reescreve a cada troca.
_CHAVE_CERT = "certificadoDigital"
_CHAVE_SENHA = "senhaCertificadoDigital"
_CHAVE_GRAVACAO = "caminhoGravacaoArquivos"
_CHAVE_INTERVALO = "intervaloAtualizacaoPedidos"
_CHAVE_DOWNLOADS = "downloadsSimultaneos"


def _escapar(valor: str) -> str:
    """Escapa um valor no formato .properties do Java (como o arquivo já usa):
    ``\\`` → ``\\\\``, ``:`` → ``\\:``, ``=`` → ``\\=``. Round-trip seguro: o
    Java desescapa de volta ao ler."""
    return (
        str(valor)
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("=", "\\=")
    )


def aplicar_certificado(pfx_path, senha: str, caminho_gravacao) -> None:
    """Reescreve certificado, senha, pasta de gravação, intervalo de atualização
    e downloads simultâneos no .properties, preservando o resto do arquivo
    (porta, cache, comentários...).

    ``senha`` entra em TEXTO PURO e é criptografada aqui no formato do serviço
    (DESede/Base64) — gravar texto puro derruba o serviço. ``caminho_gravacao``
    recebe uma barra final (o serviço espera diretório). O intervalo/downloads
    vêm de config (SERVICO_INTERVALO_ALVO / SERVICO_DOWNLOADS_SIMULTANEOS).
    """
    path = config.PROPERTIES_PATH
    if not path.exists():
        raise FileNotFoundError(f".properties não encontrado: {path}")

    gravacao = str(caminho_gravacao)
    if not gravacao.endswith("\\"):
        gravacao += "\\"

    novos = {
        _CHAVE_CERT: _escapar(str(pfx_path)),
        _CHAVE_SENHA: _escapar(cripto_senha.criptografar(senha)),
        _CHAVE_GRAVACAO: _escapar(gravacao),
        _CHAVE_INTERVALO: str(config.SERVICO_INTERVALO_ALVO),
        _CHAVE_DOWNLOADS: str(config.SERVICO_DOWNLOADS_SIMULTANEOS),
    }
    vistos = set()

    # Lê e reescreve em latin-1 (formato do arquivo; valores não têm acento).
    linhas = path.read_text(encoding="latin-1").splitlines(keepends=True)
    saida = []
    for linha in linhas:
        despida = linha.lstrip()
        if despida.startswith("#") or "=" not in linha:
            saida.append(linha)
            continue
        chave = linha.split("=", 1)[0].strip()
        if chave in novos:
            nl = "\r\n" if linha.endswith("\r\n") else "\n"
            saida.append(f"{chave}={novos[chave]}{nl}")
            vistos.add(chave)
        else:
            saida.append(linha)

    # Garante que as 3 chaves existam mesmo que faltassem no arquivo.
    for chave, valor in novos.items():
        if chave not in vistos:
            saida.append(f"{chave}={valor}\n")

    path.write_text("".join(saida), encoding="latin-1")


def reiniciar() -> None:
    """Para e sobe o serviço Windows. Exige admin.

    Usa Stop (ignorando se já estiver parado/quebrado) seguido de Start, que é
    mais robusto que Restart-Service quando o serviço está em estado ruim.
    """
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Stop-Service -Name '{config.SERVICO_NOME}' -Force "
         f"-ErrorAction SilentlyContinue; "
         f"Start-Service -Name '{config.SERVICO_NOME}'"],
        check=True,
        capture_output=True,
        text=True,
    )


def ler_intervalo_minutos() -> int:
    """Lê ``intervaloAtualizacaoPedidos`` do .properties (min entre ciclos do
    serviço). Fallback para config.SERVICO_INTERVALO_MIN se não conseguir ler."""
    try:
        for linha in config.PROPERTIES_PATH.read_text(encoding="latin-1").splitlines():
            if linha.strip().startswith("intervaloAtualizacaoPedidos"):
                return int(linha.split("=", 1)[1].strip())
    except (OSError, ValueError):
        pass
    return config.SERVICO_INTERVALO_MIN


def aguardar_online(timeout: int = None, intervalo: int = 5) -> bool:
    """Espera o endpoint SOAP responder ao WSDL após o restart.

    Returns True se ficou online dentro do timeout; False caso contrário.
    """
    if timeout is None:
        timeout = config.SERVICO_TIMEOUT_ONLINE
    fim = time.monotonic() + timeout
    while time.monotonic() < fim:
        try:
            resp = requests.get(config.WSDL_URL, timeout=10)
            if resp.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(intervalo)
    return False
