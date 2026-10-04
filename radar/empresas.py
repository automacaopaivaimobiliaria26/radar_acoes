"""Busca e mantém em cache descrições curtas de empresas listadas."""

from __future__ import annotations

import csv
import logging
import re
from datetime import date, timedelta
from pathlib import Path

from .config import ARQUIVO_EMPRESAS


LOG = logging.getLogger("radar.empresas")
COLUNAS = ("mercado", "ticker", "bolsa", "descricao", "consultado_em")
VALIDADE_SUCESSO = timedelta(days=180)
VALIDADE_FALHA = timedelta(days=14)
TAMANHO_MAXIMO = 240


def resumir(texto: str) -> str:
    """Limpa um perfil empresarial e limita a descrição a uma frase curta."""
    texto = re.sub(r"\s+", " ", (texto or "")).strip()
    if not texto:
        return ""
    # Prefere a primeira frase, mas não deixa uma frase excepcionalmente longa dominar a tabela.
    primeira = re.split(r"(?<=[.!?])\s+", texto, maxsplit=1)[0].strip()
    if len(primeira) > TAMANHO_MAXIMO:
        primeira = primeira[:TAMANHO_MAXIMO].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return primeira


def ler_cache(caminho: Path) -> dict[tuple[str, str], dict[str, str]]:
    """Carrega descrições já consultadas; arquivo ausente equivale a cache vazio."""
    if not caminho.exists():
        return {}
    with caminho.open("r", newline="", encoding="utf-8-sig") as arquivo:
        resultado = {}
        for linha in csv.DictReader(arquivo):
            chave = ((linha.get("mercado") or "").upper(), (linha.get("ticker") or "").upper())
            if all(chave):
                resultado[chave] = {
                    "bolsa": (linha.get("bolsa") or "").strip(),
                    "descricao": resumir(linha.get("descricao") or ""),
                    "consultado_em": linha.get("consultado_em") or "",
                }
        return resultado


def nome_bolsa(perfil: dict[str, object], mercado: str) -> str:
    """Converte códigos comuns do Yahoo Finance em nomes de bolsa legíveis."""
    if mercado == "B3":
        return "B3"
    nome = str(perfil.get("fullExchangeName") or "").strip()
    if nome:
        nomes = {
            "NasdaqGS": "Nasdaq Global Select Market",
            "NasdaqGM": "Nasdaq Global Market",
            "NasdaqCM": "Nasdaq Capital Market",
            "NYSEArca": "NYSE Arca",
            "NYSEAmerican": "NYSE American",
        }
        return nomes.get(nome, nome)
    codigo = str(perfil.get("exchange") or "").strip().upper()
    codigos = {
        "NMS": "Nasdaq Global Select Market",
        "NGM": "Nasdaq Global Market",
        "NCM": "Nasdaq Capital Market",
        "NYQ": "NYSE",
        "ASE": "NYSE American",
        "PCX": "NYSE Arca",
        "SAO": "B3",
    }
    return codigos.get(codigo, codigo or "Não informada")


def consultar_perfil(mercado: str, ticker: str) -> tuple[str, str]:
    """Obtém descrição e bolsa no perfil público do Yahoo Finance sem interromper o Radar."""
    simbolo = f"{ticker}.SA" if mercado == "B3" else ticker
    try:
        import yfinance as yf

        perfil = yf.Ticker(simbolo).get_info()
        descricao = resumir(perfil.get("longBusinessSummary") or perfil.get("description") or "")
        return descricao, nome_bolsa(perfil, mercado)
    except Exception as erro:  # O perfil é enriquecimento opcional; RSS e notas seguem mesmo sem ele.
        LOG.warning("Não foi possível obter metadados de %s (%s): %s", ticker, mercado, erro)
        return "", "B3" if mercado == "B3" else "Não informada"


def enriquecer_candidatas(
    candidatas: list[dict[str, str]],
    caminho: Path = ARQUIVO_EMPRESAS,
    hoje: date | None = None,
) -> list[dict[str, str]]:
    """Anexa descrições recentes às candidatas e salva o cache com substituição atômica."""
    hoje = hoje or date.today()
    cache = ler_cache(caminho)
    resultado = []
    alterado = False
    for candidata in candidatas:
        item = dict(candidata)
        mercado, ticker = item["mercado"].upper(), item["ticker"].upper()
        chave = (mercado, ticker)
        registro = cache.get(chave, {})
        try:
            consultado_em = date.fromisoformat(registro.get("consultado_em", ""))
        except ValueError:
            consultado_em = date.min
        validade = VALIDADE_SUCESSO if registro.get("descricao") else VALIDADE_FALHA
        if hoje - consultado_em >= validade or not registro.get("bolsa"):
            descricao, bolsa = consultar_perfil(mercado, ticker)
            registro = {
                "descricao": descricao or registro.get("descricao", ""),
                "bolsa": bolsa or registro.get("bolsa") or ("B3" if mercado == "B3" else "Não informada"),
                "consultado_em": hoje.isoformat(),
            }
            cache[chave] = registro
            alterado = True
        item["descricao"] = registro.get("descricao", "")
        item["bolsa"] = registro.get("bolsa") or ("B3" if mercado == "B3" else "Não informada")
        resultado.append(item)

    if alterado:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        temporario = caminho.with_suffix(caminho.suffix + ".tmp")
        with temporario.open("w", newline="", encoding="utf-8") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=COLUNAS)
            escritor.writeheader()
            for (mercado, ticker), registro in sorted(cache.items()):
                escritor.writerow({
                    "mercado": mercado,
                    "ticker": ticker,
                    "bolsa": registro.get("bolsa") or ("B3" if mercado == "B3" else "Não informada"),
                    "descricao": registro["descricao"],
                    "consultado_em": registro["consultado_em"],
                })
        temporario.replace(caminho)
    return resultado
