#!/usr/bin/env python3
"""Calcula a nota diária e gera a watchlist separada por mercado."""

from __future__ import annotations

import argparse
import csv
import logging
import math
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from radar.config import (
    ARQUIVO_ATENCAO,
    ARQUIVO_B3,
    ARQUIVO_EUA,
    JANELA_VOLUME,
    LIMITE_WATCHLIST,
    MINIMO_LIQUIDEZ_B3,
    MINIMO_LIQUIDEZ_EUA,
    MINIMO_PRECO_EUA,
    PASTA_SAIDA,
    PESO_ATENCAO,
    PESO_MOMENTO,
    PESO_NOTICIAS,
    PESO_VOLUME,
)


JANELA_MOMENTO_CURTO = 5
JANELA_MOMENTO_LONGO = 20
SALTO_MAX_B3 = 0.40
GAP_MAX_DIAS = 45

# Fórmula: nota = 100 x (0,40 x percentil do volume + 0,25 x percentil do momento
# + 0,20 x percentil das notícias + 0,15 x percentil da atenção).
# O percentil do momento é a média dos percentis dos retornos de 5 e 20 pregões.
PESOS = {
    "volume": PESO_VOLUME,
    "momento": PESO_MOMENTO,
    "noticias": PESO_NOTICIAS,
    "atencao": PESO_ATENCAO,
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("radar.nota")


def parse_data(texto: str) -> date | None:
    """Aceita datas ISO e o formato brasileiro comum em CSVs."""
    texto = (texto or "").strip()
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(texto[:10], formato).date()
        except ValueError:
            continue
    return None


def parse_numero(texto: str) -> float | None:
    """Converte números com ponto decimal ou vírgula decimal."""
    texto = (texto or "").strip().replace("$", "").replace("R$", "").replace(" ", "")
    if not texto:
        return None
    # Se houver vírgula, ela é tratada como separador decimal.
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        numero = float(texto)
    except ValueError:
        return None
    return numero if math.isfinite(numero) else None


def carregar_precos(caminho: Path, mercado: str) -> dict[str, list[dict[str, object]]]:
    """Carrega cotações com os nomes de coluna usados pelos dois coletores."""
    series: dict[str, list[dict[str, object]]] = defaultdict(list)
    with caminho.open("r", newline="", encoding="utf-8-sig") as arquivo:
        leitor = csv.DictReader(arquivo)
        if not leitor.fieldnames:
            raise ValueError(f"CSV sem cabeçalho: {caminho}")
        for linha in leitor:
            dia = parse_data(linha.get("data", ""))
            ticker = (linha.get("ticker") or linha.get("symbol") or "").strip().upper()
            fechamento = parse_numero(linha.get("fechamento") or linha.get("close") or "")
            volume_financeiro = parse_numero(
                linha.get("volume_rs") or linha.get("volume_usd") or linha.get("volume_fin")
                or linha.get("volume") or linha.get("volume_financeiro") or ""
            )
            if dia is None or not ticker or fechamento is None or fechamento <= 0:
                continue
            # Se o CSV trouxer apenas quantidade, estima volume financeiro pelo fechamento.
            if volume_financeiro is None:
                quantidade = parse_numero(linha.get("quantidade") or linha.get("volume_shares") or "")
                if quantidade is None or quantidade <= 0:
                    continue
                volume_financeiro = fechamento * quantidade
            if volume_financeiro <= 0:
                continue
            series[ticker].append({
                "data": dia,
                "fechamento": fechamento,
                "volume_financeiro": volume_financeiro,
                "mercado": mercado,
            })

    for ticker in series:
        # Se uma linha aparecer duplicada, conserva o último registro lido daquele dia.
        por_data = {registro["data"]: registro for registro in series[ticker]}
        series[ticker] = sorted(por_data.values(), key=lambda registro: registro["data"])
    return series


def carregar_atencao(caminho: Path) -> dict[tuple[str, str, date], dict[str, object]]:
    """Indexa contagem e razão de atenção por ação e data."""
    resultado = {}
    if not caminho.exists():
        LOG.warning("%s não existe; as notas serão parciais sem notícias e atenção", caminho)
        return resultado
    with caminho.open("r", newline="", encoding="utf-8-sig") as arquivo:
        for linha in csv.DictReader(arquivo):
            dia = parse_data(linha.get("data", ""))
            mercado = (linha.get("mercado") or "").strip().upper()
            ticker = (linha.get("ticker") or "").strip().upper()
            if mercado == "US":
                mercado = "EUA"
            if dia is None or mercado not in {"B3", "EUA"} or not ticker:
                continue
            resultado[(mercado, ticker, dia)] = {
                "noticias": parse_numero(linha.get("noticias_24h") or linha.get("noticias") or ""),
                "atencao": parse_numero(linha.get("razao_atencao") or linha.get("razao") or ""),
                "parcial": (linha.get("parcial") or "").strip().casefold() in {"sim", "true", "1", "yes"},
            }
    return resultado


def indicadores(
    serie: list[dict[str, object]], ate: date, mercado: str | None = None
) -> dict[str, object] | None:
    """Calcula os indicadores apenas com observações até a data de referência."""
    conhecidas = [registro for registro in serie if registro["data"] <= ate]
    if len(conhecidas) < JANELA_MOMENTO_LONGO + 1:
        return None
    atual = conhecidas[-1]
    # Não usa um ticker que não tenha cotação na última data disponível do mercado.
    if atual["data"] != ate:
        return None

    anteriores_volume = conhecidas[-(JANELA_VOLUME + 1):-1]
    if (ate - conhecidas[-(JANELA_MOMENTO_LONGO + 1)]["data"]).days > GAP_MAX_DIAS:
        return None
    media_volume = sum(registro["volume_financeiro"] for registro in anteriores_volume) / JANELA_VOLUME
    if media_volume <= 0:
        return None
    fechamento_atual = atual["fechamento"]
    retorno_5 = fechamento_atual / conhecidas[-(JANELA_MOMENTO_CURTO + 1)]["fechamento"] - 1
    retorno_20 = fechamento_atual / conhecidas[-(JANELA_MOMENTO_LONGO + 1)]["fechamento"] - 1
    if mercado == "B3":
        fechamentos = [registro["fechamento"] for registro in conhecidas[-(JANELA_MOMENTO_LONGO + 1):]]
        saltos = [abs(atual / anterior - 1) for anterior, atual in zip(fechamentos, fechamentos[1:])]
        if saltos and max(saltos) > SALTO_MAX_B3:
            return None
    return {
        "data": ate,
        "volume_relativo": atual["volume_financeiro"] / media_volume,
        "media_volume_20": media_volume,
        "fechamento": fechamento_atual,
        "retorno_5": retorno_5,
        "retorno_20": retorno_20,
    }


def percentis(valores: dict[str, float]) -> dict[str, float]:
    """Calcula percentis médios em caso de empate, dentro do mercado do dia."""
    total = len(valores)
    if total == 0:
        return {}
    ordenados = sorted(valores.values())
    resultado = {}
    for ticker, valor in valores.items():
        menores = sum(1 for outro in ordenados if outro < valor)
        iguais = sum(1 for outro in ordenados if outro == valor)
        resultado[ticker] = (menores + iguais / 2) / total
    return resultado


def montar_mercado(
    mercado: str,
    series: dict[str, list[dict[str, object]]],
    atencao: dict[tuple[str, str, date], dict[str, object]],
    ate: date | None = None,
) -> tuple[date | None, list[dict[str, object]]]:
    """Calcula as notas de um mercado em sua última data disponível."""
    datas = [registro["data"] for serie in series.values() for registro in serie if ate is None or registro["data"] <= ate]
    if not datas:
        return None, []
    data_referencia = max(datas)
    base: dict[str, dict[str, object]] = {}
    for ticker, serie in series.items():
        item = indicadores(serie, data_referencia, mercado)
        if item is None:
            continue
        minimo_liquidez = MINIMO_LIQUIDEZ_B3 if mercado == "B3" else MINIMO_LIQUIDEZ_EUA
        if item["media_volume_20"] < minimo_liquidez:
            continue
        if mercado == "EUA" and item["fechamento"] <= MINIMO_PRECO_EUA:
            continue
        hist = atencao.get((mercado, ticker, data_referencia), {})
        item["ticker"] = ticker
        item["noticias"] = hist.get("noticias")
        item["atencao"] = hist.get("atencao")
        item["atencao_parcial"] = bool(hist.get("parcial", False))
        base[ticker] = item

    if not base:
        return data_referencia, []

    pv = percentis({ticker: item["volume_relativo"] for ticker, item in base.items()})
    p5 = percentis({ticker: item["retorno_5"] for ticker, item in base.items()})
    p20 = percentis({ticker: item["retorno_20"] for ticker, item in base.items()})
    pn = percentis({ticker: item["noticias"] for ticker, item in base.items() if item["noticias"] is not None})
    pa = percentis({ticker: item["atencao"] for ticker, item in base.items() if item["atencao"] is not None})

    pontuados = []
    for ticker, item in base.items():
        percentis_disponiveis: dict[str, float] = {
            "volume": pv[ticker],
            "momento": (p5[ticker] + p20[ticker]) / 2,
        }
        if ticker in pn:
            percentis_disponiveis["noticias"] = pn[ticker]
        if ticker in pa and not item["atencao_parcial"]:
            percentis_disponiveis["atencao"] = pa[ticker]
        soma_pesos = sum(PESOS[nome] for nome in percentis_disponiveis)
        nota = 100 * sum(PESOS[nome] * valor for nome, valor in percentis_disponiveis.items()) / soma_pesos
        item["nota"] = nota
        item["nota_parcial"] = (
            len(percentis_disponiveis) < len(PESOS)
            or item["atencao_parcial"]
        )
        pontuados.append(item)
    return data_referencia, sorted(pontuados, key=lambda item: (-item["nota"], item["ticker"]))


def motivo(item: dict[str, object]) -> str:
    """Resume em uma linha os indicadores que contribuíram para a nota."""
    partes = [
        f"volume {item['volume_relativo']:.2f}x a média de 20 pregões",
        f"retorno de 5 pregões {item['retorno_5']:+.1%}",
        f"retorno de 20 pregões {item['retorno_20']:+.1%}",
    ]
    if item["noticias"] is not None:
        partes.append(f"{int(item['noticias'])} manchetes em 24h")
    else:
        partes.append("notícias indisponíveis")
    if item["atencao"] is not None and not item["atencao_parcial"]:
        partes.append(f"atenção {item['atencao']:.2f}x a média de 7 dias")
    elif item["atencao_parcial"]:
        partes.append("atenção parcial")
    else:
        partes.append("atenção indisponível")
    if item["nota_parcial"]:
        partes.append("nota parcial")
    return "; ".join(partes).replace("|", "/")


def gravar_watchlist(caminho: Path, resultados: dict[str, tuple[date | None, list[dict[str, object]]]]) -> None:
    """Escreve as 15 maiores notas de cada mercado em Markdown."""
    linhas = [
        "# Radar de pesquisa. Nao e recomendacao de investimento.",
        "",
        "Notas calculadas somente com dados disponíveis até a data de referência de cada mercado.",
        "Percentis e notas são calculados separadamente para B3 e EUA.",
        "",
    ]
    for mercado in ("B3", "EUA"):
        data_referencia, lista = resultados[mercado]
        linhas.append(f"## {mercado} — referência: {data_referencia.isoformat() if data_referencia else 'sem dados'}")
        linhas.append("")
        if not lista:
            linhas.extend(["Sem ações com histórico suficiente para calcular os indicadores.", ""])
            continue
        linhas.extend([
            "| Ticker | Nota | Volume relativo | Retorno 5 pregões | Notícias 24h | Motivo |",
            "|---|---:|---:|---:|---:|---|",
        ])
        for item in lista[:LIMITE_WATCHLIST]:
            noticias = "—" if item["noticias"] is None else str(int(item["noticias"]))
            nota = f"{item['nota']:.1f}" + (" (parcial)" if item["nota_parcial"] else "")
            linhas.append(
                f"| {_celula_markdown(item['ticker'])} | {nota} | "
                f"{item['volume_relativo']:.2f}x | {item['retorno_5']:+.2%} | {noticias} | {_celula_markdown(motivo(item))} |"
            )
        linhas.append("")
    temporario = caminho.with_suffix(caminho.suffix + ".tmp")
    temporario.write_text("\n".join(linhas), encoding="utf-8")
    temporario.replace(caminho)


def _celula_markdown(valor: object) -> str:
    """Escapa separadores e espaços nas células da tabela Markdown."""
    return " ".join(str(valor).replace("|", "/").split())


def main() -> int:
    parser = argparse.ArgumentParser(description="Calcula notas diárias e gera a watchlist do Radar.")
    parser.add_argument("--b3", type=Path, default=ARQUIVO_B3, help="CSV de preços da B3 (padrão: b3.csv).")
    parser.add_argument("--eua", type=Path, default=ARQUIVO_EUA, help="CSV de preços dos EUA (padrão: eua.csv).")
    parser.add_argument("--atencao", type=Path, default=ARQUIVO_ATENCAO, help="Histórico de notícias (padrão: atencao_hist.csv).")
    parser.add_argument("--data", type=date.fromisoformat, help="Data de referência AAAA-MM-DD para reproduzir uma watchlist histórica.")
    parser.add_argument("--saida", type=Path, help="Markdown de saída; por padrão saida/watchlist_AAAA-MM-DD.md.")
    args = parser.parse_args()

    series_b3: dict[str, list[dict[str, object]]] = {}
    series_eua: dict[str, list[dict[str, object]]] = {}
    for mercado, caminho in (("B3", args.b3), ("EUA", args.eua)):
        if not caminho.exists():
            LOG.warning("%s não existe; esse mercado será omitido da watchlist.", caminho)
            continue
        try:
            series = carregar_precos(caminho, mercado)
        except (OSError, ValueError) as erro:
            LOG.error("Não foi possível carregar %s: %s; esse mercado será omitido.", caminho, erro)
            continue
        if mercado == "B3":
            series_b3 = series
        else:
            series_eua = series
    if not series_b3 and not series_eua:
        LOG.error("Não há cotações válidas nos CSVs de entrada.")
        return 1
    atencao = carregar_atencao(args.atencao)
    data_b3, lista_b3 = montar_mercado("B3", series_b3, atencao, args.data)
    data_eua, lista_eua = montar_mercado("EUA", series_eua, atencao, args.data)
    referencias = [dia for dia in (data_b3, data_eua) if dia is not None]
    if not referencias:
        LOG.error("Não há cotações válidas nos CSVs de entrada.")
        return 1
    data_arquivo = args.data or max(referencias)
    destino = args.saida or PASTA_SAIDA / f"watchlist_{data_arquivo.isoformat()}.md"
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        gravar_watchlist(destino, {"B3": (data_b3, lista_b3), "EUA": (data_eua, lista_eua)})
    except OSError as erro:
        LOG.error("Não foi possível gravar %s: %s", destino, erro)
        return 1
    LOG.info("Watchlist salva em %s (%d B3, %d EUA)", destino, len(lista_b3), len(lista_eua))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
