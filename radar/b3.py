#!/usr/bin/env python3
"""Baixa o histórico COTAHIST diário da B3 e salva dados em CSV."""

from __future__ import annotations

import csv
import io
import logging
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import ARQUIVO_B3, ARQUIVO_NOMES_B3, JANELA_HISTORICO_B3


URL_MODELO = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_D{data}.ZIP"
# A B3 aceita a URL direta com User-Agent de navegador; identificadores próprios
# do script recebiam HTTP 403, embora o mesmo arquivo estivesse disponível no browser.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
COLUNAS = ("data", "ticker", "fechamento", "quantidade", "volume_rs")
COLUNAS_NOMES = ("ticker", "nome")
TENTATIVAS = 3
PAUSA_REPETICAO = 2.0
DIAS_BUSCA = 130
LOG = logging.getLogger("radar.b3")


def baixar_pregao(dia: date, nomes: dict[str, str] | None = None) -> list[list[object]]:
    """Baixa um arquivo diário e extrai ações do mercado à vista em lote padrão."""
    url = URL_MODELO.format(data=dia.strftime("%d%m%Y"))
    pedido = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    conteudo = None
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            with urllib.request.urlopen(pedido, timeout=45) as resposta:
                conteudo = resposta.read()
            break
        except urllib.error.HTTPError as erro:
            # Ausência do arquivo normalmente significa que não houve pregão.
            if erro.code == 404:
                return []
            if tentativa == TENTATIVAS:
                LOG.warning("Falha ao baixar %s: %s", dia, erro)
                return []
        except (urllib.error.URLError, TimeoutError, OSError) as erro:
            if tentativa == TENTATIVAS:
                LOG.warning("Falha ao baixar %s após tentativas: %s", dia, erro)
                return []
        if tentativa < TENTATIVAS:
            time.sleep(PAUSA_REPETICAO * tentativa)

    try:
        with zipfile.ZipFile(io.BytesIO(conteudo or b"")) as arquivo_zip:
            nome_arquivo = next(nome for nome in arquivo_zip.namelist() if not nome.endswith("/"))
            linhas = arquivo_zip.read(nome_arquivo).decode("latin-1").splitlines()
    except (zipfile.BadZipFile, StopIteration, OSError) as erro:
        LOG.warning("Arquivo COTAHIST inválido para %s: %s", dia, erro)
        return []

    resultado = []
    for linha in linhas:
        # CODBDI 02 e TPMERC 010 correspondem ao recorte usado no material de referência.
        if len(linha) < 188 or linha[:2] != "01" or linha[10:12] != "02" or linha[24:27] != "010":
            continue
        try:
            # Índices fixos do layout oficial; preços e volume financeiro vêm em centavos.
            # O campo de data do COTAHIST vem no formato AAAAMMDD.
            data_linha = date(int(linha[2:6]), int(linha[6:8]), int(linha[8:10]))
            ticker = linha[12:24].strip()
            fechamento = int(linha[108:121]) / 100
            quantidade = int(linha[152:170])
            volume_rs = int(linha[170:188]) / 100
        except (ValueError, IndexError):
            continue
        if ticker and fechamento > 0 and quantidade > 0 and volume_rs > 0:
            resultado.append([data_linha.isoformat(), ticker, fechamento, quantidade, volume_rs])
            if nomes is not None:
                nome = linha[27:39].strip()
                if nome:
                    nomes[ticker] = nome
    return resultado


def baixar_historico(
    destino: Path = ARQUIVO_B3,
    arquivo_nomes: Path = ARQUIVO_NOMES_B3,
    quantidade_pregoes: int = JANELA_HISTORICO_B3,
) -> int:
    """Busca pregões recentes, grava cotações e tabela ticker/nome."""
    if quantidade_pregoes < 21:
        raise ValueError("São necessários pelo menos 21 pregões para os cálculos do Radar.")
    hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    registros: list[list[object]] = []
    nomes: dict[str, str] = {}
    dias = set()
    dia = hoje
    while len(dias) < quantidade_pregoes and (hoje - dia).days <= DIAS_BUSCA:
        lote = baixar_pregao(dia, nomes)
        registros.extend(lote)
        dias.update(linha[0] for linha in lote)
        dia -= timedelta(days=1)
    if len(dias) < 21:
        raise RuntimeError(f"A B3 retornou apenas {len(dias)} pregões; são necessários ao menos 21.")

    gravar_csv_atomico(destino, COLUNAS, registros)
    gravar_csv_atomico(arquivo_nomes, COLUNAS_NOMES, [[ticker, nome] for ticker, nome in sorted(nomes.items())])
    LOG.info("B3: %d pregões e %d linhas gravadas em %s", len(dias), len(registros), destino)
    return len(dias)


def gravar_csv_atomico(destino: Path, colunas: tuple[str, ...], linhas: list[list[object]]) -> None:
    """Grava para arquivo temporário e troca o destino ao concluir."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix(destino.suffix + ".tmp")
    with temporario.open("w", newline="", encoding="utf-8") as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow(colunas)
        escritor.writerows(linhas)
    temporario.replace(destino)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Baixa COTAHIST diário e salva b3.csv.")
    parser.add_argument("--saida", type=Path, default=ARQUIVO_B3)
    parser.add_argument("--nomes", type=Path, default=ARQUIVO_NOMES_B3)
    parser.add_argument("--pregoes", type=int, default=JANELA_HISTORICO_B3)
    args = parser.parse_args()
    try:
        baixar_historico(args.saida, args.nomes, args.pregoes)
    except (OSError, RuntimeError, ValueError) as erro:
        LOG.error("Coleta da B3 não concluída: %s", erro)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
