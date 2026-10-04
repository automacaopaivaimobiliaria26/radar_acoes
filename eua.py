#!/usr/bin/env python3
"""Baixa e filtra dados diários de ações listadas nos EUA para o Radar."""

from __future__ import annotations

import argparse
import csv
import io
import logging
import re
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from radar.config import (
    ARQUIVO_EUA,
    ARQUIVO_NOMES_EUA,
    MINIMO_LIQUIDEZ_EUA,
    MINIMO_PRECO_EUA,
)

try:
    import yfinance as yf
except ImportError:
    print("Dependência ausente. Instale com: python3 -m pip install yfinance", file=sys.stderr)
    raise SystemExit(1)


URLS_LISTAS = (
    "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
)
ARQUIVO_SAIDA = ARQUIVO_EUA
COLUNAS = ("data", "ticker", "fechamento", "quantidade", "volume")
TAMANHO_LOTE = 200
DIAS_HISTORICO = 60
PAUSA_LOTES = 2.0
TENTATIVAS = 3
PAUSA_REPETICAO = 5.0
DIAS_PRESERVAR_HISTORICO = 150
PRECO_MINIMO = MINIMO_PRECO_EUA
VOLUME_MEDIO_MINIMO = MINIMO_LIQUIDEZ_EUA

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("radar.eua")


def ler_lista(url: str) -> list[dict[str, str]]:
    """Baixa uma lista delimitada por | e confere o cabeçalho especial."""
    pedido = urllib.request.Request(url, headers={"User-Agent": "RadarPesquisa/1.0"})
    with urllib.request.urlopen(pedido, timeout=30) as resposta:
        conteudo = resposta.read().decode("utf-8-sig")
    linhas = [linha for linha in conteudo.splitlines() if linha and not linha.startswith("File Creation Time")]
    leitor = csv.DictReader(io.StringIO("\n".join(linhas)), delimiter="|")
    if not leitor.fieldnames or "Test Issue" not in leitor.fieldnames:
        raise ValueError(f"Formato inesperado na lista {url}")
    return list(leitor)


def acoes_com_nomes() -> dict[str, str]:
    """Retorna símbolos e nomes de ações comuns, sem ETFs ou papéis de teste."""
    simbolos: dict[str, str] = {}
    padrao_acao = re.compile(r"\b(common stock|common shares?|ordinary shares?|class [a-z]+ common)\b", re.I)
    nao_acao = re.compile(r"\b( warrant| warrants| right| rights| unit| units| preferred| depositary|notes?|debentures?)\b", re.I)

    for url in URLS_LISTAS:
        for linha in ler_lista(url):
            # Other-listed traz várias bolsas; conserva NYSE (N) e NYSE American (A).
            if "otherlisted" in url and (linha.get("Exchange") or "").strip().upper() not in {"N", "A"}:
                continue
            nome = (linha.get("Security Name") or "").strip()
            ticker = (linha.get("Symbol") or linha.get("ACT Symbol") or "").strip()
            if not ticker or not nome:
                continue
            if (linha.get("ETF") or "").strip().upper() == "Y":
                continue
            if (linha.get("Test Issue") or "").strip().upper() == "Y":
                continue
            if not padrao_acao.search(nome) or nao_acao.search(nome):
                continue
            # O Yahoo usa hífen em classes como BRK.B, que vêm com ponto na lista.
            ticker_yahoo = ticker.replace(".", "-").replace("$", "-")
            simbolos[ticker_yahoo] = nome
    return dict(sorted(simbolos.items()))


def simbolos_comuns() -> list[str]:
    """Mantém a interface simples usada pelos comandos e integrações anteriores."""
    return list(acoes_com_nomes())


def extrair_coluna(dados, ticker: str, campo: str):
    """Lê uma coluna do retorno do yfinance, com ou sem MultiIndex."""
    import pandas as pd

    if not isinstance(dados.columns, pd.MultiIndex):
        return dados[campo] if campo in dados.columns else None
    nivel0 = dados.columns.get_level_values(0)
    nivel1 = dados.columns.get_level_values(1)
    if campo in nivel0:
        quadro = dados[campo]
        return quadro[ticker] if ticker in quadro.columns else None
    if ticker in nivel0:
        quadro = dados[ticker]
        return quadro[campo] if campo in quadro.columns else None
    if campo in nivel1:
        quadro = dados.xs(campo, axis=1, level=1)
        return quadro[ticker] if ticker in quadro.columns else None
    return None


def baixar_lote(simbolos: list[str]):
    """Tenta baixar um lote inteiro e repete em caso de falha."""
    erro_final = None
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            dados = yf.download(
                tickers=simbolos,
                period="6mo",  # Seis meses dão margem para obter 60 pregões úteis.
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                actions=False,
                threads=False,
                progress=False,
                timeout=30,
            )
            if dados is not None and not dados.empty:
                return dados
            erro_final = RuntimeError("O lote não retornou dados")
        except Exception as erro:  # Registra erro temporário e tenta novamente.
            erro_final = erro
        if tentativa < TENTATIVAS:
            LOG.warning("Falha no lote (tentativa %d/%d): %s; tentando novamente", tentativa, TENTATIVAS, erro_final)
            time.sleep(PAUSA_REPETICAO * tentativa)
    LOG.error("Lote ignorado após %d tentativas: %s", TENTATIVAS, erro_final)
    return None


def coletar(simbolos: list[str]) -> list[list[object]]:
    """Baixa históricos, aplica filtros de preço e liquidez e prepara linhas."""
    saida: list[list[object]] = []
    lotes = [simbolos[i:i + TAMANHO_LOTE] for i in range(0, len(simbolos), TAMANHO_LOTE)]

    for indice, lote in enumerate(lotes, start=1):
        LOG.info("Baixando lote %d/%d (%d símbolos)", indice, len(lotes), len(lote))
        dados = baixar_lote(lote)
        if dados is None:
            raise RuntimeError(
                f"Lote {indice}/{len(lotes)} falhou após as tentativas; o CSV anterior será preservado."
            )
        for ticker in lote:
            fechamento = extrair_coluna(dados, ticker, "Close")
            quantidade = extrair_coluna(dados, ticker, "Volume")
            if fechamento is None or quantidade is None:
                continue

            pares = []
            for dia, preco, qtd in zip(fechamento.index, fechamento, quantidade):
                if preco is None or qtd is None:
                    continue
                try:
                    preco_num = float(preco)
                    qtd_num = int(float(qtd))
                except (TypeError, ValueError, OverflowError):
                    continue
                if preco_num > 0 and qtd_num > 0:
                    pares.append((dia, preco_num, qtd_num))

            pares = pares[-DIAS_HISTORICO:]
            if len(pares) < 21:
                continue
            # Usa os 20 pregões anteriores ao mais recente para medir liquidez.
            media_dolar_20 = sum(preco * qtd for _, preco, qtd in pares[-21:-1]) / 20
            if pares[-1][1] <= PRECO_MINIMO or media_dolar_20 <= VOLUME_MEDIO_MINIMO:
                continue

            for dia, preco, qtd in pares:
                data = dia.date().isoformat() if hasattr(dia, "date") else str(dia)[:10]
                saida.append([data, ticker, round(preco, 6), qtd, round(preco * qtd, 2)])
        if indice < len(lotes):
            time.sleep(PAUSA_LOTES)

    saida.sort(key=lambda linha: (linha[0], linha[1]))
    return saida


def gravar_csv(linhas: list[list[object]], destino: Path = ARQUIVO_SAIDA) -> None:
    """Grava o CSV atomicamente, sem substituir dados válidos por uma gravação interrompida."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix(destino.suffix + ".tmp")
    with temporario.open("w", newline="", encoding="utf-8") as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow(COLUNAS)
        escritor.writerows(linhas)
    temporario.replace(destino)


def preservar_historico_anterior(linhas: list[list[object]], origem: Path) -> list[list[object]]:
    """Mantém até 150 dias de papéis que saíram dos filtros atuais, para reprocessar datas passadas."""
    if not origem.exists():
        return linhas
    tickers_atualizados = {str(linha[1]).upper() for linha in linhas}
    corte = (date.today() - timedelta(days=DIAS_PRESERVAR_HISTORICO)).isoformat()
    anteriores: list[list[object]] = []
    with origem.open("r", newline="", encoding="utf-8-sig") as arquivo:
        for registro in csv.DictReader(arquivo):
            dia = (registro.get("data") or "").strip()[:10]
            ticker = (registro.get("ticker") or registro.get("symbol") or "").strip().upper()
            if not dia or dia < corte or not ticker or ticker in tickers_atualizados:
                continue
            try:
                fechamento = float(registro.get("fechamento") or registro.get("close") or "")
                quantidade = int(float(registro.get("quantidade") or registro.get("volume_shares") or ""))
                volume = float(
                    registro.get("volume") or registro.get("volume_usd") or registro.get("volume_fin")
                    or registro.get("volume_financeiro") or ""
                )
            except (TypeError, ValueError, OverflowError):
                continue
            if fechamento > 0 and quantidade > 0 and volume > 0:
                anteriores.append([dia, ticker, fechamento, quantidade, volume])
    combinadas = {(str(linha[0]), str(linha[1]).upper()): linha for linha in anteriores}
    combinadas.update({(str(linha[0]), str(linha[1]).upper()): linha for linha in linhas})
    return sorted(combinadas.values(), key=lambda linha: (str(linha[0]), str(linha[1])))


def main() -> int:
    parser = argparse.ArgumentParser(description="Baixa dados diários de ações dos EUA para o Radar.")
    parser.add_argument("--limite", type=int, help="Limita a coleta aos primeiros N símbolos; grava em arquivo de teste por padrão.")
    parser.add_argument("--saida", type=Path, help="Caminho do CSV gerado (sem --saida, usa dados/eua.csv ou dados/eua_teste.csv).")
    parser.add_argument("--nomes-saida", type=Path, help="CSV de nomes (sem opção, acompanha o destino escolhido).")
    args = parser.parse_args()
    if args.limite is not None and args.limite < 1:
        parser.error("--limite deve ser maior que zero")

    destino_padrao = ARQUIVO_SAIDA if args.limite is None else ARQUIVO_SAIDA.with_name("eua_teste.csv")
    nomes_padrao = ARQUIVO_NOMES_EUA if args.limite is None else ARQUIVO_NOMES_EUA.with_name("nomes_eua_teste.csv")
    destino = args.saida or destino_padrao
    nomes_destino = args.nomes_saida or nomes_padrao
    if args.limite is not None and destino.resolve() == ARQUIVO_SAIDA.resolve():
        parser.error("coletas com --limite não podem gravar em dados/eua.csv; escolha outro --saida")
    if args.limite is not None and nomes_destino.resolve() == ARQUIVO_NOMES_EUA.resolve():
        parser.error("coletas com --limite não podem substituir dados/nomes_eua.csv; escolha outro --nomes-saida")

    try:
        nomes = acoes_com_nomes()
        simbolos = list(nomes)
    except Exception as erro:
        LOG.exception("Não foi possível carregar as listas de símbolos: %s", erro)
        return 1
    if args.limite:
        simbolos = simbolos[:args.limite]
        nomes = {ticker: nomes[ticker] for ticker in simbolos}
    if not simbolos:
        LOG.error("Nenhuma ação comum foi encontrada nas listas.")
        return 1

    LOG.info("%d símbolos selecionados", len(simbolos))
    try:
        linhas = coletar(simbolos)
    except Exception as erro:
        LOG.exception("Coleta dos EUA não concluída: %s", erro)
        return 1
    try:
        if not linhas:
            raise RuntimeError("A coleta não retornou linhas; o CSV anterior foi preservado.")
        if destino.resolve() == ARQUIVO_SAIDA.resolve():
            linhas = preservar_historico_anterior(linhas, destino)
        gravar_csv(linhas, destino)
    except RuntimeError as erro:
        LOG.error("Coleta dos EUA não concluída: %s", erro)
        return 1
    except OSError:
        LOG.exception("Não foi possível gravar %s", destino)
        return 1
    LOG.info("%s salvo: %d linhas, %d ações aprovadas no filtro", destino, len(linhas), len({linha[1] for linha in linhas}))
    try:
        nomes_destino.parent.mkdir(parents=True, exist_ok=True)
        temporario_nomes = nomes_destino.with_suffix(nomes_destino.suffix + ".tmp")
        with temporario_nomes.open("w", newline="", encoding="utf-8") as arquivo:
            escritor = csv.writer(arquivo)
            escritor.writerow(("ticker", "nome"))
            escritor.writerows(sorted(nomes.items()))
        temporario_nomes.replace(nomes_destino)
    except OSError:
        LOG.exception("Não foi possível gravar nomes em %s", nomes_destino)
        return 1
    LOG.info("Coleta referente a %s", date.today().isoformat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
