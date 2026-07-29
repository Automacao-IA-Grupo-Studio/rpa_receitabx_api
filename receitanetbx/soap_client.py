"""
soap_client.py — Cliente SOAP genérico do ReceitanetBX Serviço.

Isola TODO o transporte SOAP num só lugar. O padrão da API é peculiar:
todas as operações recebem um único parâmetro string ``entrada`` (com o XML
de negócio escapado dentro) e devolvem ``retorno`` (int) + ``saida`` (string).

Este módulo cuida de:
  - montar o envelope SOAP em volta do XML de negócio;
  - fazer o POST com os headers corretos (SOAPAction, Content-Type);
  - extrair ``retorno`` e ``saida`` da resposta.

Nenhuma regra de negócio aqui — só encanamento.
"""

import xml.etree.ElementTree as ET
from html import escape

import requests

from . import config


def _montar_envelope(operacao: str, xml_entrada: str) -> str:
    """Envelopa o XML de negócio (escapado) dentro de <ns:entrada>."""
    entrada = escape(xml_entrada, quote=False)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<soapenv:Envelope xmlns:soapenv="{config.SOAP_ENV}" '
        f'xmlns:ns="{config.NS_AXIS2}">'
        "<soapenv:Header/><soapenv:Body>"
        f"<ns:{operacao}>"
        f"<ns:entrada>{entrada}</ns:entrada>"
        f"</ns:{operacao}>"
        "</soapenv:Body></soapenv:Envelope>"
    )


def chamar(operacao: str, xml_entrada: str) -> tuple:
    """Executa uma operação SOAP.

    Args:
        operacao: nome da operação (ex.: "PesquisarArquivos", "SolicitarArquivos").
        xml_entrada: XML de negócio (será escapado e envelopado).

    Returns:
        (retorno, saida, http_status)
          - retorno: string "1" em caso de sucesso, ou None se não veio.
          - saida:   XML de resposta de negócio (string), ou None.
          - http_status: código HTTP do POST.
    """
    envelope = _montar_envelope(operacao, xml_entrada)
    headers = {
        "Content-Type": "text/xml; charset=UTF-8",
        "SOAPAction": f"urn:{operacao}",
    }
    resp = requests.post(
        config.ENDPOINT,
        data=envelope.encode("utf-8"),
        headers=headers,
        timeout=config.HTTP_TIMEOUT,
    )

    retorno = saida = None
    try:
        root = ET.fromstring(resp.content)
        for el in root.iter():
            tag = el.tag.split("}")[-1]
            if tag == "retorno":
                retorno = el.text
            elif tag == "saida":
                saida = el.text
    except ET.ParseError:
        pass

    return retorno, saida, resp.status_code
