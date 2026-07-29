"""
cripto_senha.py — Criptografia da senha do certificado no formato do serviço.

O ReceitanetBX Serviço NÃO guarda a senha do .pfx em texto puro no
``recnetbx-service.properties``: guarda **criptografada**. Escrever texto puro
faz o serviço quebrar ao iniciar ("Bad Base64 input character" / "Null input
buffer") e as portas 2443/2444 não sobem.

Esquema (descoberto por engenharia reversa de ``receitanetbx-ws-1.9.26.jar``,
classe ``webservices.a``):

  - algoritmo: DESede/ECB/PKCS5Padding (Triple DES);
  - chave: os 6 bytes do MAC da placa de rede ativa, repetidos 4x (24 bytes);
  - texto em ISO-8859-1; resultado em Base64.

A máquina pode ter vários MACs; descobrimos o correto testando cada um contra
um ORÁCULO (um par conhecido texto/cifra já gravado pelo próprio serviço). Se
nenhum MAC reproduz o oráculo, abortamos — melhor falhar do que gravar uma
senha inválida e derrubar o serviço de novo.
"""

import base64
import subprocess

from Crypto.Cipher import DES3
from Crypto.Util.Padding import pad, unpad

_chave = None  # cache da chave (24 bytes) desta máquina


def _macs_da_maquina():
    """MACs (bytes de 6) das placas, priorizando as que estão 'Up'."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-NetAdapter | Sort-Object @{E={$_.Status -ne 'Up'}},ifIndex | "
         "ForEach-Object { $_.MacAddress }"],
        capture_output=True, text=True,
    ).stdout
    macs = []
    for linha in out.splitlines():
        h = linha.strip().replace("-", "").replace(":", "")
        if len(h) == 12:
            try:
                macs.append(bytes.fromhex(h))
            except ValueError:
                pass
    return macs


def _chave_do_mac(mac: bytes) -> bytes:
    return mac * 4  # 6 bytes -> 24 bytes (DESede)


def _cifra(chave: bytes):
    return DES3.new(chave, DES3.MODE_ECB)


def descobrir_chave_entre(candidatos, oraculo_cifra_b64: str) -> bytes:
    """Determina a chave desta máquina testando cada MAC contra o oráculo,
    aceitando VÁRIAS senhas candidatas (a 1ª que reproduzir a cifra vence).

    Resiliente à reorganização da tabela de certificados: se o certificado de
    referência mudar de id (ou sumir), basta que a senha do oráculo ainda esteja
    entre as senhas cadastradas. Decifra a cifra UMA vez por MAC e checa se o
    resultado bate com alguma candidata.

    ``candidatos``: iterável de senhas em texto puro. ``oraculo_cifra_b64``: a
    senha do oráculo já criptografada pelo serviço. Levanta RuntimeError se
    nenhum MAC reproduzir nenhuma candidata (aí NÃO se grava no .properties).
    """
    global _chave
    alvo = base64.b64decode(oraculo_cifra_b64)
    textos = {t for t in candidatos if t}
    for mac in _macs_da_maquina():
        chave = _chave_do_mac(mac)
        try:
            dec = unpad(_cifra(chave).decrypt(alvo), 8).decode("iso-8859-1")
        except (ValueError, KeyError):
            continue
        if dec in textos:
            _chave = chave
            return chave
    raise RuntimeError(
        "Não foi possível determinar a chave de criptografia do serviço "
        "(nenhum MAC reproduziu o oráculo). Confira as senhas dos certificados "
        "ou reconfigure um certificado pelo configurador oficial."
    )


def descobrir_chave(oraculo_texto: str, oraculo_cifra_b64: str) -> bytes:
    """Versão de 1 senha (retrocompatível) — ver ``descobrir_chave_entre``."""
    return descobrir_chave_entre([oraculo_texto], oraculo_cifra_b64)


def criptografar(senha_texto: str) -> str:
    """Criptografa a senha no formato do serviço (Base64 de DESede/ECB)."""
    if _chave is None:
        raise RuntimeError("Chave não inicializada — chame descobrir_chave() antes.")
    ct = _cifra(_chave).encrypt(pad(senha_texto.encode("iso-8859-1"), 8))
    return base64.b64encode(ct).decode("ascii")
