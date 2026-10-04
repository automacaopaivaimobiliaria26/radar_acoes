#!/usr/bin/env python3
"""Executa a coleta, notícias e geração da watchlist em sequência."""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys

from . import b3
from .candidatas import preparar
from .config import (
    ARQUIVO_ATENCAO,
    ARQUIVO_B3,
    ARQUIVO_CANDIDATAS,
    ARQUIVO_EUA,
    ARQUIVO_LOG,
    ARQUIVO_NOMES_B3,
    ARQUIVO_NOMES_EUA,
    PASTA_DADOS,
    PASTA_LOGS,
    PASTA_SAIDA,
    RAIZ,
)


def configurar_log() -> logging.Logger:
    """Registra a execução no terminal e em logs/radar.log."""
    PASTA_LOGS.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("radar")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        formato = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        arquivo = logging.FileHandler(ARQUIVO_LOG, encoding="utf-8")
        console = logging.StreamHandler()
        arquivo.setFormatter(formato)
        console.setFormatter(formato)
        logger.addHandler(arquivo)
        logger.addHandler(console)
    return logger


def executar_comando(logger: logging.Logger, etapa: str, comando: list[str]) -> None:
    """Executa um script auxiliar e encaminha suas mensagens ao log principal."""
    logger.info("Iniciando etapa: %s", etapa)
    resultado = subprocess.run(comando, cwd=RAIZ, capture_output=True, text=True, check=False)
    if resultado.stdout.strip():
        for linha in resultado.stdout.splitlines():
            logger.info("%s", linha)
    if resultado.stderr.strip():
        for linha in resultado.stderr.splitlines():
            (logger.error if resultado.returncode else logger.info)("%s", linha)
    if resultado.returncode:
        raise RuntimeError(f"Etapa {etapa} terminou com código {resultado.returncode}.")


def executar(limite_eua: int | None = None) -> Path:
    """Roda todos os módulos e retorna o caminho da watchlist criada."""
    logger = configurar_log()
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    PASTA_SAIDA.mkdir(parents=True, exist_ok=True)
    python = sys.executable
    try:
        logger.info("Iniciando Radar de pesquisa; nenhuma recomendação é produzida.")
        b3.baixar_historico(ARQUIVO_B3, ARQUIVO_NOMES_B3)
        comando_eua = [python, str(RAIZ / "eua.py"), "--saida", str(ARQUIVO_EUA), "--nomes-saida", str(ARQUIVO_NOMES_EUA)]
        if limite_eua:
            comando_eua.extend(["--limite", str(limite_eua)])
        executar_comando(logger, "coleta dos EUA", comando_eua)

        candidatas = preparar()
        if not candidatas:
            raise RuntimeError("Não há ações com 21 pregões válidos para formar candidatas.")
        logger.info("%d candidatas selecionadas para as buscas de notícias.", len(candidatas))

        executar_comando(logger, "coleta de notícias", [
            python, str(RAIZ / "atencao.py"), "--entrada", str(ARQUIVO_CANDIDATAS), "--historico", str(ARQUIVO_ATENCAO),
        ])
        data_referencia = max(item["data"] for item in candidatas)
        watchlist = PASTA_SAIDA / f"watchlist_{data_referencia.isoformat()}.md"
        executar_comando(logger, "cálculo de notas e watchlist", [
            python, str(RAIZ / "nota.py"), "--b3", str(ARQUIVO_B3), "--eua", str(ARQUIVO_EUA),
            "--atencao", str(ARQUIVO_ATENCAO), "--saida", str(watchlist),
        ])
        logger.info("Radar concluído: %s", watchlist)
        return watchlist
    except Exception as erro:
        logger.exception("Execução do Radar interrompida: %s", erro)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Executa a sequência diária completa do Radar.")
    parser.add_argument("--limite-eua", type=int, help="Limita ações dos EUA em execução de teste (ex.: 20).")
    args = parser.parse_args()
    if args.limite_eua is not None and args.limite_eua < 1:
        parser.error("--limite-eua deve ser maior que zero")
    try:
        executar(args.limite_eua)
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
