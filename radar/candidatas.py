"""Prepara até 60 ações por mercado pela classificação preliminar."""

from __future__ import annotations

import csv
from pathlib import Path

from nota import carregar_precos, montar_mercado
from .config import (
    ARQUIVO_B3,
    ARQUIVO_CANDIDATAS,
    ARQUIVO_EUA,
    MAX_ACOES_NOTICIAS,
)


def preparar(
    arquivo_b3: Path = ARQUIVO_B3,
    arquivo_eua: Path = ARQUIVO_EUA,
    destino: Path = ARQUIVO_CANDIDATAS,
    limite: int = MAX_ACOES_NOTICIAS,
) -> list[dict[str, object]]:
    """Seleciona por mercado as melhores notas sem notícias e salva o CSV do RSS."""
    series_b3 = carregar_precos(arquivo_b3, "B3") if arquivo_b3.exists() else {}
    series_eua = carregar_precos(arquivo_eua, "EUA") if arquivo_eua.exists() else {}
    selecionadas = []
    for mercado, series in (("B3", series_b3), ("EUA", series_eua)):
        _referencia, classificacao = montar_mercado(mercado, series, {})
        do_mercado = [
            {
                "mercado": mercado,
                "ticker": item["ticker"],
                "volume_relativo": item["volume_relativo"],
                "data": item["data"],
            }
            for item in classificacao[:limite]
        ]
        selecionadas.extend(do_mercado)
    selecionadas.sort(key=lambda item: (-item["volume_relativo"], item["mercado"], item["ticker"]))
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix(destino.suffix + ".tmp")
    with temporario.open("w", newline="", encoding="utf-8") as arquivo:
        escritor = csv.DictWriter(
            arquivo,
            fieldnames=("mercado", "ticker", "volume_relativo"),
        )
        escritor.writeheader()
        for item in selecionadas:
            escritor.writerow({
                "mercado": item["mercado"], "ticker": item["ticker"],
                "volume_relativo": f"{item['volume_relativo']:.8f}",
            })
    temporario.replace(destino)
    return selecionadas
