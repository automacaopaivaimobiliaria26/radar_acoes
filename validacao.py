#!/usr/bin/env python3
"""Compara watchlists históricas com o universo de cada mercado."""

from __future__ import annotations

import argparse
import logging
import re
from datetime import date, datetime
from pathlib import Path

from nota import carregar_precos, parse_numero
from radar.config import ARQUIVO_B3, ARQUIVO_EUA, PASTA_SAIDA


PADRAO_WATCHLIST = re.compile(r"^watchlist_(\d{4}-\d{2}-\d{2})\.md$")
PADRAO_SECAO = re.compile(r"^##\s+(B3|EUA)\s+—\s+referência:\s*(\d{4}-\d{2}-\d{2})")
HORIZONTES = (5, 20)
CUSTO_IDA_E_VOLTA = 0.001
# Quantis bilaterais de 95% da distribuição t de Student para amostras pequenas.
T_95 = (0.0, 12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306,
        2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110,
        2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056,
        2.052, 2.048, 2.045, 2.042)
LOG = logging.getLogger("radar.validacao")


def ler_watchlists(pasta: Path) -> dict[date, dict[str, list[dict[str, object]]]]:
    """Lê as tabelas Markdown produzidas pelo Radar, mantendo os 15 primeiros."""
    saida: dict[date, dict[str, list[dict[str, object]]]] = {}
    for caminho in sorted(pasta.iterdir() if pasta.exists() else []):
        correspondencia = PADRAO_WATCHLIST.fullmatch(caminho.name)
        if not correspondencia or not caminho.is_file():
            continue
        try:
            dia = date.fromisoformat(correspondencia.group(1))
            conteudo = caminho.read_text(encoding="utf-8")
        except (ValueError, OSError, UnicodeError) as erro:
            LOG.warning("Watchlist ignorada (%s): %s", caminho.name, erro)
            continue
        mercados: dict[str, list[dict[str, object]]] = {"B3": [], "EUA": []}
        mercado_atual: str | None = None
        referencia_atual = dia
        colunas: dict[str, int] = {}
        for linha in conteudo.splitlines():
            secao = PADRAO_SECAO.match(linha.strip())
            if secao:
                mercado_atual = secao.group(1)
                referencia_atual = date.fromisoformat(secao.group(2))
                colunas = {}
                continue
            if mercado_atual is None or not linha.strip().startswith("|"):
                continue
            celulas = [parte.strip() for parte in linha.strip().strip("|").split("|")]
            if celulas and celulas[0].casefold() == "ticker":
                colunas = {nome.casefold(): indice for indice, nome in enumerate(celulas)}
                continue
            if not colunas or not celulas or all(re.fullmatch(r":?-{2,}:?", item) for item in celulas):
                continue
            try:
                ticker = celulas[colunas["ticker"]].strip().upper()
                nota = parse_numero(celulas[colunas["nota"]])
            except (KeyError, IndexError):
                continue
            if ticker and nota is not None:
                mercados[mercado_atual].append({
                    "ticker": ticker, "nota": nota, "data_referencia": referencia_atual,
                })
        for lista in mercados.values():
            lista.sort(key=lambda item: (-float(item["nota"]), str(item["ticker"])))
        saida[dia] = {mercado: lista[:15] for mercado, lista in mercados.items()}
    return saida


def preparar_precos(caminho: Path, mercado: str) -> dict[str, dict[date, float]]:
    """Indexa preços válidos por ticker e pregão."""
    if not caminho.exists():
        LOG.warning("CSV de preços não encontrado: %s", caminho)
        return {}
    series = carregar_precos(caminho, mercado)
    return {
        ticker: {registro["data"]: float(registro["fechamento"]) for registro in serie}
        for ticker, serie in series.items()
    }


def retorno_no_pregao(
    ticker: str, data_base: date, data_alvo: date, precos: dict[str, dict[date, float]], custo: float
) -> float | None:
    serie = precos.get(ticker, {})
    inicial, final = serie.get(data_base), serie.get(data_alvo)
    if inicial is None or final is None or inicial <= 0:
        return None
    return final / inicial - 1 - custo


def avaliar_horizonte(
    dia: date,
    mercado: str,
    selecionadas: list[dict[str, object]],
    precos: dict[str, dict[date, float]],
    horizonte: int,
    custo: float = CUSTO_IDA_E_VOLTA,
) -> dict[str, object]:
    """Compara a carteira igualmente ponderada com o universo no mesmo dia."""
    sessoes = sorted({data for serie in precos.values() for data in serie})
    indice_sessao = {sessao: indice for indice, sessao in enumerate(sessoes)}
    futuras = [sessao for sessao in sessoes if sessao > dia]
    if len(futuras) < horizonte:
        return {"status": "pendente", "data": dia, "mercado": mercado, "horizonte": horizonte}
    alvo = futuras[horizonte - 1]
    carteira = []
    faltantes = 0
    for item in selecionadas[:15]:
        ticker = str(item["ticker"]).upper()
        retorno = retorno_no_pregao(ticker, dia, alvo, precos, custo)
        if retorno is None:
            faltantes += 1
        else:
            carteira.append(retorno)

    papeis_na_data = [ticker for ticker, serie in precos.items() if dia in serie]
    universo = [
        retorno_no_pregao(ticker, dia, alvo, precos, custo)
        for ticker in papeis_na_data
        if alvo in precos[ticker]
    ]
    universo = [retorno for retorno in universo if retorno is not None]
    universo_faltantes = len(papeis_na_data) - len(universo)
    if not carteira or not universo:
        return {
            "status": "sem_amostra", "data": dia, "mercado": mercado, "horizonte": horizonte,
            "data_alvo": alvo, "faltantes": faltantes, "selecionadas_validas": len(carteira),
            "universo_valido": len(universo), "universo_faltantes": universo_faltantes,
        }
    media_carteira = sum(carteira) / len(carteira)
    media_universo = sum(universo) / len(universo)
    return {
        "status": "ok", "data": dia, "mercado": mercado, "horizonte": horizonte,
        "data_alvo": alvo, "media_carteira": media_carteira, "media_universo": media_universo,
        "_indice_base": indice_sessao[dia], "_indice_alvo": indice_sessao[alvo],
        "excesso": media_carteira - media_universo,
        "acertos_positivos": sum(retorno > 0 for retorno in carteira) / len(carteira),
        "acertos_acoes_vs_mercado": sum(retorno > media_universo for retorno in carteira) / len(carteira),
        "acertos_vs_mercado": float(media_carteira > media_universo),
        "pior_acao": min(carteira), "pior_caso": min(media_carteira, min(carteira)),
        "retornos_acoes": carteira,
        "faltantes": faltantes, "selecionadas_validas": len(carteira), "universo_valido": len(universo),
        "universo_faltantes": universo_faltantes,
    }


def resumo(resultados: list[dict[str, object]]) -> dict[str, float | int | None]:
    """Resume média, mediana, acertos e pior caso entre datas avaliadas."""
    validos = [item for item in resultados if item.get("status") == "ok"]
    if not validos:
        return {"dias": 0, "media_carteira": None, "mediana_carteira": None,
                "media_universo": None, "media_excesso": None, "acerto_positivo": None,
                "media_acao": None, "mediana_acao": None, "acerto_acao_positivo": None,
                "acerto_acao_vs_mercado": None, "acerto_vs_mercado": None,
                "pior_caso": None, "pior_acao": None}
    carteiras = sorted(float(item["media_carteira"]) for item in validos)

    def mediana(valores: list[float]) -> float:
        meio = len(valores) // 2
        return valores[meio] if len(valores) % 2 else (valores[meio - 1] + valores[meio]) / 2

    retornos_acoes = [float(retorno) for item in validos for retorno in item["retornos_acoes"]]
    retornos_acoes.sort()
    acertos_acao_vs_mercado = [
        float(item["acertos_acoes_vs_mercado"]) * len(item["retornos_acoes"])
        for item in validos
    ]

    return {
        "dias": len(validos),
        "media_carteira": sum(carteiras) / len(carteiras),
        "mediana_carteira": mediana(carteiras),
        "media_universo": sum(float(item["media_universo"]) for item in validos) / len(validos),
        "media_excesso": sum(float(item["excesso"]) for item in validos) / len(validos),
        "acerto_positivo": sum(float(item["acertos_positivos"]) for item in validos) / len(validos),
        "media_acao": sum(retornos_acoes) / len(retornos_acoes),
        "mediana_acao": mediana(retornos_acoes),
        "acerto_acao_positivo": sum(retorno > 0 for retorno in retornos_acoes) / len(retornos_acoes),
        "acerto_acao_vs_mercado": sum(acertos_acao_vs_mercado) / len(retornos_acoes),
        "acerto_vs_mercado": sum(float(item["acertos_vs_mercado"]) for item in validos) / len(validos),
        "pior_caso": min(float(item["pior_caso"]) for item in validos),
        "pior_acao": min(float(item["pior_acao"]) for item in validos),
    }


def diagnostico_ruido(resultados: list[dict[str, object]]) -> dict[str, float | int | bool | None]:
    """Estima a incerteza do excesso médio usando janelas de pregões não sobrepostas."""
    validos = sorted(
        (item for item in resultados if item.get("status") == "ok"),
        key=lambda item: (int(item.get("_indice_base", 0)), str(item["data"])),
    )
    janelas: list[dict[str, object]] = []
    proxima_base = -1
    datas_base: set[date] = set()
    for item in validos:
        data_base = item["data"]
        indice_base = int(item.get("_indice_base", 0))
        indice_alvo = int(item.get("_indice_alvo", indice_base))
        if data_base in datas_base or indice_base < proxima_base:
            continue
        janelas.append(item)
        datas_base.add(data_base)
        proxima_base = indice_alvo

    excessos = [float(item["excesso"]) for item in janelas]
    n = len(excessos)
    if n < 2:
        return {"janelas": n, "media": sum(excessos) / n if n else None,
                "ic_inferior": None, "ic_superior": None, "ruido": True}
    media = sum(excessos) / n
    variancia = sum((valor - media) ** 2 for valor in excessos) / (n - 1)
    erro_padrao = (variancia / n) ** 0.5
    quantil = T_95[n - 1] if n <= 31 else 1.96
    margem = quantil * erro_padrao
    inferior, superior = media - margem, media + margem
    return {"janelas": n, "media": media, "ic_inferior": inferior,
            "ic_superior": superior, "ruido": inferior <= 0 <= superior}


def gerar_relatorio(
    watchlists: dict[date, dict[str, list[dict[str, object]]]],
    precos_por_mercado: dict[str, dict[str, dict[date, float]]],
    custo: float = CUSTO_IDA_E_VOLTA,
) -> str:
    """Gera resumo por mercado e horizonte; datas ainda sem futuro ficam pendentes."""
    linhas = [
        "# Validação histórica do Radar", "",
        f"Custo aplicado: {custo:.2%} por operação completa (compra e venda).",
        "Retornos medidos do fechamento da watchlist ao fechamento do pregão futuro; pesos iguais por ação.",
        "O universo de comparação contém os papéis com preço disponível no pregão da watchlist e no pregão-alvo. Papéis sem preço futuro não recebem retorno inventado; a contagem expõe possível viés de sobrevivência.",
        "Resultados pendentes ainda não têm pregões futuros suficientes. O diagnóstico de ruído usa intervalos t de 95% sobre janelas não sobrepostas; se o intervalo do excesso incluir zero, o desempenho observado ainda é compatível com ruído. Com menos de duas janelas, não há dados para estimar esse intervalo. É uma indicação estatística simples, não uma prova nem uma previsão.",
    ]
    for mercado in ("B3", "EUA"):
        linhas.extend(["", f"## {mercado}", ""])
        precos = precos_por_mercado[mercado]
        for horizonte in HORIZONTES:
            por_referencia: dict[date, tuple[date, list[dict[str, object]]]] = {}
            for dia, listas in sorted(watchlists.items()):
                if listas[mercado]:
                    referencia = listas[mercado][0].get("data_referencia", dia)
                    por_referencia[referencia] = (dia, listas[mercado])
            resultados = [
                avaliar_horizonte(
                    referencia, mercado, lista, precos, horizonte, custo,
                )
                for referencia, (dia, lista) in sorted(por_referencia.items())
            ]
            estatisticas = resumo(resultados)
            ruido = diagnostico_ruido(resultados)
            linhas.append(f"### {horizonte} pregões")
            linhas.append("")
            linhas.append(
                f"Dias avaliados: {estatisticas['dias']}; média da carteira: {_pct(estatisticas['media_carteira'])}; "
                f"mediana: {_pct(estatisticas['mediana_carteira'])}; média do universo: {_pct(estatisticas['media_universo'])}; "
                f"excesso médio: {_pct(estatisticas['media_excesso'])}."
            )
            linhas.append(
                f"Retorno médio/mediano por ação: {_pct(estatisticas['media_acao'])}/{_pct(estatisticas['mediana_acao'])}; "
                f"ações com retorno positivo: {_pct(estatisticas['acerto_acao_positivo'])}; "
                f"ações selecionadas que superaram a média do mercado: {_pct(estatisticas['acerto_acao_vs_mercado'])}; "
                f"dias acima do universo: {_pct(estatisticas['acerto_vs_mercado'])}; "
                f"pior retorno de uma ação: {_pct(estatisticas['pior_acao'])}; "
                f"pior média diária da carteira: {_pct(estatisticas['pior_caso'])}."
            )
            if ruido["ic_inferior"] is None:
                texto_ruido = "inconclusivo (menos de duas janelas)"
            elif ruido["ruido"]:
                texto_ruido = "compatível com ruído (IC inclui zero)"
            else:
                texto_ruido = "IC não inclui zero; evidência ainda descritiva"
            janelas_label = "janela não sobreposta" if ruido["janelas"] == 1 else "janelas não sobrepostas"
            linhas.append(
                f"Diagnóstico de ruído: {ruido['janelas']} {janelas_label}; "
                f"IC 95% do excesso médio {_pct(ruido['ic_inferior'])} a {_pct(ruido['ic_superior'])}; {texto_ruido}."
            )
            pendentes = sum(item.get("status") == "pendente" for item in resultados)
            incompletos = sum(int(item.get("faltantes", 0)) for item in resultados if item.get("status") in {"ok", "sem_amostra"})
            universo_incompleto = sum(int(item.get("universo_faltantes", 0)) for item in resultados if item.get("status") in {"ok", "sem_amostra"})
            linhas.append(
                f"Datas pendentes: {pendentes}; posições da watchlist sem preço futuro: {incompletos}; "
                f"papéis do universo sem preço futuro: {universo_incompleto}."
            )
            linhas.append("")
            linhas.append("| Data-base | Data-alvo | Carteira líquida | Universo líquido | Excesso | Ações válidas |")
            linhas.append("|---|---|---:|---:|---:|---:|")
            for item in resultados:
                if item.get("status") != "ok":
                    linhas.append(f"| {item['data']} | — | pendente/sem amostra | — | — | {item.get('selecionadas_validas', 0)} |")
                    continue
                linhas.append(
                    f"| {item['data']} | {item['data_alvo']} | {_pct(item['media_carteira'])} | "
                    f"{_pct(item['media_universo'])} | {_pct(item['excesso'])} | {item['selecionadas_validas']} |"
                )
    return "\n".join(linhas) + "\n"


def _pct(valor: object) -> str:
    return "—" if valor is None else f"{float(valor):+.2%}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compara watchlists com o desempenho futuro do universo da B3 e dos EUA.")
    parser.add_argument("--watchlists", type=Path, default=PASTA_SAIDA)
    parser.add_argument("--b3", type=Path, default=ARQUIVO_B3)
    parser.add_argument("--eua", type=Path, default=ARQUIVO_EUA)
    parser.add_argument("--custo", type=float, default=CUSTO_IDA_E_VOLTA,
                        help="Custo total de ida e volta em proporção (padrão: 0.001 = 0,1%%).")
    parser.add_argument("--saida", type=Path, help="Markdown de saída; por padrão saida/validacao_AAAA-MM-DD.md.")
    args = parser.parse_args()
    if args.custo < 0:
        parser.error("--custo não pode ser negativo")
    watchlists = ler_watchlists(args.watchlists)
    if not watchlists:
        LOG.error("Nenhuma watchlist válida encontrada em %s", args.watchlists)
        return 1
    precos = {
        "B3": preparar_precos(args.b3, "B3"),
        "EUA": preparar_precos(args.eua, "EUA"),
    }
    relatorio = gerar_relatorio(watchlists, precos, args.custo)
    destino = args.saida or args.watchlists / f"validacao_{date.today().isoformat()}.md"
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        temporario = destino.with_suffix(destino.suffix + ".tmp")
        temporario.write_text(relatorio, encoding="utf-8")
        temporario.replace(destino)
    except OSError as erro:
        LOG.error("Não foi possível gravar %s: %s", destino, erro)
        return 1
    print(relatorio, end="")
    LOG.info("Relatório salvo em %s", destino)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
