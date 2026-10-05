#!/usr/bin/env python3
"""Executa a coleta, notícias e geração da watchlist em sequência."""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from datetime import datetime, time as hora, timedelta
from zoneinfo import ZoneInfo

from . import b3
from .candidatas import preparar
from .config import (
    ARQUIVO_ATENCAO,
    ARQUIVO_B3,
    ARQUIVO_CANDIDATAS,
    ARQUIVO_EUA,
    ARQUIVO_LOG,
    PASTA_DADOS,
    PASTA_LOGS,
    PASTA_SAIDA,
    RAIZ,
)

FUSO_RADAR = ZoneInfo("America/Sao_Paulo")
HORARIO_RADAR = hora(21, 30)


def slot_esperado(agora: datetime) -> datetime:
    """Retorna o último horário útil de execução (21h30) que já passou."""
    dia = agora.date()
    if agora.timetz().replace(tzinfo=None) < HORARIO_RADAR:
        dia -= timedelta(days=1)
    while dia.weekday() >= 5:
        dia -= timedelta(days=1)
    return datetime.combine(dia, HORARIO_RADAR, tzinfo=FUSO_RADAR)


def precisa_recuperar(agora: datetime, dados: Path) -> bool:
    """Recupera só quando não houve tentativa depois do último horário previsto."""
    try:
        ultima = datetime.fromisoformat((dados / "ultima_tentativa.txt").read_text().strip())
    except (OSError, ValueError):
        return True
    if ultima.tzinfo is None:
        ultima = ultima.replace(tzinfo=FUSO_RADAR)
    return ultima < slot_esperado(agora)


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
    """Roda o Radar; com limite, executa apenas uma coleta EUA isolada de produção."""
    logger = configurar_log()
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    PASTA_SAIDA.mkdir(parents=True, exist_ok=True)
    python = sys.executable
    if limite_eua is not None:
        teste = PASTA_DADOS / "eua_teste.csv"
        logger.info("Modo de teste: coletando até %d símbolos sem atualizar arquivos de produção.", limite_eua)
        executar_comando(logger, "teste de coleta dos EUA", [
            python, str(RAIZ / "eua.py"), "--limite", str(limite_eua),
            "--saida", str(teste),
        ])
        logger.info("Coleta de teste salva em %s; nenhuma watchlist foi alterada.", teste)
        return teste

    try:
        agora = datetime.now(FUSO_RADAR)
        (PASTA_DADOS / "ultima_tentativa.txt").write_text(agora.isoformat(timespec="seconds"))
        logger.info("Iniciando Radar de pesquisa; nenhuma recomendação é produzida.")
        etapas_com_erro: list[str] = []
        try:
            b3.baixar_historico(ARQUIVO_B3)
        except Exception:
            etapas_com_erro.append("B3")
            logger.exception("Falha ao atualizar a B3; vou tentar continuar com o histórico existente.")
        comando_eua = [python, str(RAIZ / "eua.py"), "--saida", str(ARQUIVO_EUA)]
        try:
            executar_comando(logger, "coleta dos EUA", comando_eua)
        except RuntimeError:
            etapas_com_erro.append("EUA")
            logger.exception("Falha ao atualizar os EUA; vou tentar continuar com o histórico existente.")

        candidatas = preparar()
        if not candidatas:
            raise RuntimeError("Não há ações com 21 pregões válidos para formar candidatas.")
        logger.info("%d candidatas selecionadas para as buscas de notícias.", len(candidatas))

        try:
            executar_comando(logger, "coleta de notícias", [
                python, str(RAIZ / "atencao.py"), "--entrada", str(ARQUIVO_CANDIDATAS), "--historico", str(ARQUIVO_ATENCAO),
            ])
        except RuntimeError:
            etapas_com_erro.append("notícias")
            logger.exception("Notícias indisponíveis; vou gerar a watchlist com os componentes disponíveis.")
        data_referencia = max(item["data"] for item in candidatas)
        watchlist = PASTA_SAIDA / f"watchlist_{data_referencia.isoformat()}.md"
        executar_comando(logger, "cálculo de notas e watchlist", [
            python, str(RAIZ / "nota.py"), "--b3", str(ARQUIVO_B3), "--eua", str(ARQUIVO_EUA),
            "--atencao", str(ARQUIVO_ATENCAO), "--saida", str(watchlist),
        ])
        if etapas_com_erro:
            raise RuntimeError(
                f"Watchlist parcial gerada em {watchlist}; etapas com falha: {', '.join(etapas_com_erro)}. "
                "Consulte o log antes de considerar os dados completos."
            )
        logger.info("Radar concluído: %s", watchlist)
        (PASTA_DADOS / "ultimo_sucesso.txt").write_text(datetime.now(FUSO_RADAR).isoformat(timespec="seconds"))
        return watchlist
    except Exception as erro:
        logger.exception("Execução do Radar interrompida: %s", erro)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Executa a sequência diária completa do Radar.")
    parser.add_argument(
        "--limite-eua", type=int,
        help="Executa somente uma coleta de teste dos EUA, em arquivos separados (ex.: 20). Não gera watchlist.",
    )
    parser.add_argument("--recuperar", action="store_true", help="Só executa se o horário diário já passou sem tentativa.")
    args = parser.parse_args()
    if args.limite_eua is not None and args.limite_eua < 1:
        parser.error("--limite-eua deve ser maior que zero")
    if args.recuperar:
        agora = datetime.now(FUSO_RADAR)
        if not precisa_recuperar(agora, PASTA_DADOS):
            return 0
        logger = configurar_log()
        logger.warning("Recuperação: nenhuma tentativa registrada depois de %s.", slot_esperado(agora).isoformat())
    try:
        executar(args.limite_eua)
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
