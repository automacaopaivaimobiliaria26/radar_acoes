"""Prepara as 60 ações com maior volume relativo para a consulta de notícias."""

from __future__ import annotations

import csv
from pathlib import Path

from nota import carregar_precos, indicadores

from .config import (
    ARQUIVO_B3,
    ARQUIVO_CANDIDATAS,
    ARQUIVO_EUA,
    ARQUIVO_NOMES_B3,
    ARQUIVO_NOMES_EUA,
    MAX_ACOES_NOTICIAS,
    MINIMO_LIQUIDEZ_B3,
    MINIMO_LIQUIDEZ_EUA,
    MINIMO_PRECO_EUA,
)


def carregar_nomes(caminho: Path) -> dict[str, str]:
    """Lê um mapa de ticker para nome empresarial, se o arquivo existir."""
    if not caminho.exists():
        return {}
    with caminho.open("r", newline="", encoding="utf-8-sig") as arquivo:
        return {
            (linha.get("ticker") or "").strip().upper(): (linha.get("nome") or "").strip()
            for linha in csv.DictReader(arquivo)
            if (linha.get("ticker") or "").strip()
        }


def preparar(
    arquivo_b3: Path = ARQUIVO_B3,
    arquivo_eua: Path = ARQUIVO_EUA,
    destino: Path = ARQUIVO_CANDIDATAS,
    limite: int = MAX_ACOES_NOTICIAS,
) -> list[dict[str, object]]:
    """Ordena B3 e EUA juntos por volume relativo e salva o CSV de entrada do RSS."""
    series_b3 = carregar_precos(arquivo_b3, "B3") if arquivo_b3.exists() else {}
    series_eua = carregar_precos(arquivo_eua, "EUA") if arquivo_eua.exists() else {}
    nomes = {"B3": carregar_nomes(ARQUIVO_NOMES_B3), "EUA": carregar_nomes(ARQUIVO_NOMES_EUA)}
    candidatas = []
    for mercado, series in (("B3", series_b3), ("EUA", series_eua)):
        datas = [registro["data"] for serie in series.values() for registro in serie]
        if not datas:
            continue
        referencia = max(datas)
        for ticker, serie in series.items():
            metricas = indicadores(serie, referencia)
            if metricas is None:
                continue
            limite_liquidez = MINIMO_LIQUIDEZ_B3 if mercado == "B3" else MINIMO_LIQUIDEZ_EUA
            if metricas["media_volume_20"] < limite_liquidez:
                continue
            if mercado == "EUA" and metricas["fechamento"] <= MINIMO_PRECO_EUA:
                continue
            candidatas.append({
                "mercado": mercado,
                "ticker": ticker,
                "nome": nomes[mercado].get(ticker, ticker),
                "volume_relativo": metricas["volume_relativo"],
                "data": referencia,
            })

    candidatas.sort(key=lambda item: (-item["volume_relativo"], item["mercado"], item["ticker"]))
    selecionadas = candidatas[:limite]
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix(destino.suffix + ".tmp")
    with temporario.open("w", newline="", encoding="utf-8") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=("mercado", "ticker", "nome", "volume_relativo"))
        escritor.writeheader()
        for item in selecionadas:
            escritor.writerow({
                "mercado": item["mercado"], "ticker": item["ticker"], "nome": item["nome"],
                "volume_relativo": f"{item['volume_relativo']:.8f}",
            })
    temporario.replace(destino)
    return selecionadas
