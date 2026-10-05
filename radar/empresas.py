"""Carrega nomes, descrições e bolsas das listas empresariais versionadas com o Radar."""

from __future__ import annotations

import csv
from pathlib import Path

from .config import ARQUIVO_EMPRESAS_B3, ARQUIVO_EMPRESAS_EUA


def normalizar_ticker(ticker: str) -> str:
    """Normaliza o ticker para o formato empregado pelos históricos do Radar."""
    return ticker.strip().upper().replace(".", "-")


def _ler_arquivo(caminho: Path, mercado: str, delimitador: str) -> dict[tuple[str, str], dict[str, str]]:
    if not caminho.is_file():
        raise FileNotFoundError(f"Arquivo-fonte de empresas não encontrado: {caminho}")
    with caminho.open("r", newline="", encoding="utf-8-sig") as arquivo:
        leitor = csv.DictReader(arquivo, delimiter=delimitador)
        if not leitor.fieldnames:
            raise ValueError(f"Arquivo-fonte de empresas sem cabeçalho: {caminho}")
        campos = {nome.strip().casefold(): nome for nome in leitor.fieldnames if nome}
        obrigatorios = ("ticker", "nome da empresa", "descrição resumida")
        ausentes = [nome for nome in obrigatorios if nome not in campos]
        if ausentes:
            raise ValueError(f"Cabeçalhos ausentes em {caminho}: {', '.join(ausentes)}")

        empresas = {}
        for linha in leitor:
            ticker = normalizar_ticker(linha.get(campos["ticker"]) or "")
            nome = (linha.get(campos["nome da empresa"]) or "").strip()
            descricao = " ".join((linha.get(campos["descrição resumida"]) or "").split())
            if mercado == "B3":
                bolsa = "B3"
            else:
                bolsa = (linha.get(campos["bolsa"]) or "").strip() if "bolsa" in campos else "Não informada"
            if ticker and nome:
                empresas[(mercado, ticker)] = {
                    "nome": nome,
                    "descricao": descricao,
                    "bolsa": bolsa or "Não informada",
                }
        return empresas


def carregar_empresas(
    arquivo_b3: Path = ARQUIVO_EMPRESAS_B3,
    arquivo_eua: Path = ARQUIVO_EMPRESAS_EUA,
) -> dict[tuple[str, str], dict[str, str]]:
    """Lê exclusivamente os dois CSVs de referência fornecidos para o Radar."""
    empresas = _ler_arquivo(arquivo_b3, "B3", ",")
    empresas.update(_ler_arquivo(arquivo_eua, "EUA", ";"))
    return empresas


def enriquecer_candidatas(candidatas: list[dict[str, object]]) -> list[dict[str, object]]:
    """Anexa metadados dos CSVs-fonte; não consulta provedores externos."""
    empresas = carregar_empresas()
    resultado = []
    for candidata in candidatas:
        item = dict(candidata)
        mercado = str(item["mercado"]).upper()
        ticker = normalizar_ticker(str(item["ticker"]))
        fonte = empresas.get((mercado, ticker), {})
        item["nome"] = fonte.get("nome", ticker)
        item["descricao"] = fonte.get("descricao", "")
        item["bolsa"] = fonte.get("bolsa", "B3" if mercado == "B3" else "Não informada")
        resultado.append(item)
    return resultado
