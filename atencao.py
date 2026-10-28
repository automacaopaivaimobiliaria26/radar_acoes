#!/usr/bin/env python3
"""Conta manchetes recentes para as ações candidatas do Radar de pesquisa."""

from __future__ import annotations

import argparse
import csv
import logging
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from radar.config import ARQUIVO_ATENCAO, ARQUIVO_CANDIDATAS, MAX_ACOES_NOTICIAS
from radar.config import RAIZ


URL_RSS = "https://news.google.com/rss/search"
ARQUIVO_HISTORICO = ARQUIVO_ATENCAO
MAX_ACOES = MAX_ACOES_NOTICIAS
PAUSA_BUSCAS = 2.0
TIMEOUT = 25
COLUNAS_HISTORICO = (
    "data", "mercado", "ticker", "volume_relativo", "noticias_24h", "media_7d",
    "razao_atencao", "parcial",
)
ARQUIVOS_EMPRESAS = {
    "B3": RAIZ / "referencias" / "empresas_b3.csv",
    "EUA": RAIZ / "referencias" / "empresas_nyse_nasdaq_bolsa.csv",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("radar.atencao")


class BloqueioRSS(RuntimeError):
    """O provedor limitou as consultas; a coleta deve parar sem insistir."""


def normalizar(texto: str) -> str:
    """Normaliza texto para comparar manchetes repetidas."""
    texto = unicodedata.normalize("NFKD", texto.casefold())
    texto = "".join(caractere for caractere in texto if not unicodedata.combining(caractere))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", texto)).strip()


def valor_coluna(linha: dict[str, str], *nomes: str) -> str:
    """Encontra uma coluna mesmo que o cabeçalho tenha variação de caixa."""
    por_nome = {chave.strip().casefold(): (valor or "").strip() for chave, valor in linha.items() if chave}
    for nome in nomes:
        if por_nome.get(nome.casefold()):
            return por_nome[nome.casefold()]
    return ""


def carregar_nomes_empresas(caminhos: dict[str, Path] | None = None) -> dict[tuple[str, str], str]:
    """Lê apenas ticker e nome dos cadastros locais, sem buscar nomes na rede."""
    resultado: dict[tuple[str, str], str] = {}
    for mercado, caminho in (caminhos or ARQUIVOS_EMPRESAS).items():
        if not caminho.exists():
            LOG.warning("Cadastro de nomes indisponível para %s: %s", mercado, caminho)
            continue
        try:
            conteudo = caminho.read_text(encoding="utf-8-sig")
            primeira_linha = next((linha for linha in conteudo.splitlines() if linha.strip()), "")
            try:
                separador = csv.Sniffer().sniff(primeira_linha, delimiters=",;").delimiter
            except csv.Error:
                separador = ";" if primeira_linha.count(";") > primeira_linha.count(",") else ","
            leitor = csv.DictReader(conteudo.splitlines(), delimiter=separador)
            if not leitor.fieldnames:
                continue
            for linha in leitor:
                ticker = valor_coluna(linha, "ticker", "symbol", "act symbol").upper()
                nome = valor_coluna(linha, "nome da empresa", "nome empresa", "company name", "empresa", "nome")
                if ticker and nome:
                    resultado[(mercado.upper(), ticker.replace(".", "-").replace("$", "-") )] = nome
        except (OSError, UnicodeError, csv.Error) as erro:
            LOG.warning("Não foi possível ler o cadastro %s: %s", caminho, erro)
    return resultado


def ler_candidatas(caminho: Path, limite: int = MAX_ACOES) -> list[dict[str, str]]:
    """Lê candidatas e conserva as maiores razões de volume relativo."""
    with caminho.open("r", newline="", encoding="utf-8-sig") as arquivo:
        leitor = csv.DictReader(arquivo)
        if not leitor.fieldnames:
            raise ValueError("O CSV de candidatas não tem cabeçalho.")
        candidatas = []
        for linha in leitor:
            mercado = valor_coluna(linha, "mercado", "market").upper()
            ticker = valor_coluna(linha, "ticker", "symbol").upper()
            bruto_rvol = valor_coluna(linha, "volume_relativo", "rvol", "relative_volume")
            if mercado not in {"B3", "EUA", "US"} or not ticker or not bruto_rvol:
                continue
            try:
                rvol = float(bruto_rvol.replace(",", "."))
            except ValueError:
                continue
            if mercado == "US":
                mercado = "EUA"
            candidatas.append({
                "mercado": mercado, "ticker": ticker, "volume_relativo": rvol,
            })

    # Mantém apenas uma linha por ação e prioriza seu maior volume relativo.
    unicas: dict[tuple[str, str], dict[str, str]] = {}
    for candidata in candidatas:
        chave = (candidata["mercado"], candidata["ticker"])
        if chave not in unicas or candidata["volume_relativo"] > unicas[chave]["volume_relativo"]:
            unicas[chave] = candidata
    return sorted(unicas.values(), key=lambda item: item["volume_relativo"], reverse=True)[:limite]


def consulta_rss(candidata: dict[str, str], agora: datetime, nome_empresa: str = "") -> int | None:
    """Busca notícias recentes e conta manchetes únicas nas últimas 24 horas."""
    mercado = candidata["mercado"]
    ticker = candidata["ticker"]
    termos = ['"' + ticker.replace('"', " ").strip() + '"']
    if nome_empresa and normalizar(nome_empresa) != normalizar(ticker):
        termos.append('"' + nome_empresa.replace('"', " ").strip() + '"')
    consulta = " OR ".join(termos)
    idioma = "pt-BR" if mercado == "B3" else "en-US"
    regiao = "BR" if mercado == "B3" else "US"
    codigo_edicao = "BR:pt-419" if mercado == "B3" else "US:en"
    parametros = urllib.parse.urlencode({"q": consulta, "hl": idioma, "gl": regiao, "ceid": codigo_edicao})
    pedido = urllib.request.Request(
        f"{URL_RSS}?{parametros}",
        headers={"User-Agent": "Mozilla/5.0 (compatible; RadarPesquisa/1.0)"},
    )
    try:
        with urllib.request.urlopen(pedido, timeout=TIMEOUT) as resposta:
            raiz = ET.fromstring(resposta.read())
    except urllib.error.HTTPError as erro:
        if erro.code in {429, 503}:
            raise BloqueioRSS(f"HTTP {erro.code} do Google Notícias") from erro
        LOG.warning("Falha no RSS de %s (%s): HTTP %s", ticker, mercado, erro.code)
        return None
    except (urllib.error.URLError, TimeoutError, ET.ParseError) as erro:
        LOG.warning("Falha no RSS de %s (%s): %s", ticker, mercado, erro)
        return None

    limite = agora - timedelta(hours=24)
    manchetes: set[str] = set()
    for item in raiz.findall("./channel/item"):
        titulo = (item.findtext("title") or "").strip()
        publicada = (item.findtext("pubDate") or "").strip()
        if not titulo or not publicada:
            continue
        try:
            instante = datetime.strptime(publicada, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                from email.utils import parsedate_to_datetime
                instante = parsedate_to_datetime(publicada).astimezone(timezone.utc)
            except (TypeError, ValueError, OverflowError):
                continue
        if limite <= instante <= agora:
            chave = normalizar(titulo)
            if chave:
                manchetes.add(chave)
    return len(manchetes)


def ler_historico(caminho: Path) -> list[dict[str, str]]:
    """Carrega contagens anteriores; começa vazio se ainda não houver arquivo."""
    if not caminho.exists():
        return []
    with caminho.open("r", newline="", encoding="utf-8-sig") as arquivo:
        return list(csv.DictReader(arquivo))


def calcular_razao(
    historico: list[dict[str, str]], mercado: str, ticker: str, hoje: date, contagem: int
) -> tuple[float | None, float | None, bool]:
    """Compara com as sete execuções diárias anteriores, sem incluir hoje.

    Retorna (razao, media, parcial).
    - razao: None se histórico insuficiente (<7 dias válidos) ou média=0;
             caso contrário, contagem/media.
    - media: None se histórico insuficiente; 0.0 se 7 dias válidos mas média=0;
             caso contrário, média dos 7 dias.
    - parcial: True se razao é None; False caso contrário.
    """
    esperados: list[date] = []
    dia = hoje - timedelta(days=1)
    while len(esperados) < 7:
        if dia.weekday() < 5:
            esperados.append(dia)
        dia -= timedelta(days=1)
    esperados_set = set(esperados)
    anteriores: dict[date, int] = {}
    for linha in historico:
        if linha.get("mercado", "").upper() != mercado or linha.get("ticker", "").upper() != ticker:
            continue
        try:
            dia = date.fromisoformat(linha["data"])
            qtd = int(float(linha["noticias_24h"]))
        except (KeyError, TypeError, ValueError):
            continue
        if (linha.get("parcial") or "").strip().casefold() in {"sim", "true", "1", "yes"}:
            continue
        if dia in esperados_set:
            anteriores[dia] = qtd

    # Exige 7 dias válidos (não-parciais) para calcular razão.
    if len(anteriores) < 7:
        return None, None, True

    media = sum(anteriores.values()) / 7
    # Média zero não permite uma razão válida.
    if media <= 0:
        return None, 0.0, True

    razao = float(contagem) / media
    return razao, media, False


def salvar_historico(caminho: Path, historico: list[dict[str, str]], novas: list[dict[str, object]]) -> None:
    """Substitui registros do dia e salva as novas contagens em CSV."""
    chaves_novas = {(str(linha["data"]), str(linha["mercado"]), str(linha["ticker"])) for linha in novas}
    restante = [
        linha for linha in historico
        if (linha.get("data", ""), linha.get("mercado", ""), linha.get("ticker", "")) not in chaves_novas
    ]
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", newline="", encoding="utf-8") as arquivo:
        # Históricos de versões anteriores podem conter nome, bolsa e descrição.
        escritor = csv.DictWriter(arquivo, fieldnames=COLUNAS_HISTORICO, extrasaction="ignore")
        escritor.writeheader()
        escritor.writerows(restante)
        escritor.writerows(novas)


def main() -> int:
    parser = argparse.ArgumentParser(description="Conta notícias recentes para as 60 maiores candidatas por volume relativo.")
    parser.add_argument("--entrada", type=Path, default=ARQUIVO_CANDIDATAS, help="CSV com mercado,ticker,volume_relativo.")
    parser.add_argument("--historico", type=Path, default=ARQUIVO_HISTORICO, help="CSV de histórico (padrão: dados/atencao_hist.csv).")
    parser.add_argument("--limite", type=int, default=MAX_ACOES, help="Máximo de ações consultadas (padrão: 60).")
    args = parser.parse_args()
    if args.limite < 1:
        parser.error("--limite deve ser maior que zero")

    try:
        candidatas = ler_candidatas(args.entrada, args.limite)
    except (OSError, ValueError) as erro:
        LOG.error("Não foi possível ler as candidatas: %s", erro)
        return 1
    if not candidatas:
        LOG.error("Nenhuma candidata válida. O CSV precisa ter mercado, ticker e volume_relativo.")
        return 1

    agora = datetime.now(timezone.utc)
    # Usa a data local do Radar; o corte das últimas 24 horas continua em UTC.
    hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    historico = ler_historico(args.historico)
    nomes = carregar_nomes_empresas()
    novas: list[dict[str, object]] = []
    consultas_interrompidas = False
    falhas_seguidas = 0
    for indice, candidata in enumerate(candidatas):
        if indice and not consultas_interrompidas:
            time.sleep(PAUSA_BUSCAS)
        contagem = None
        chave_nome = (candidata["mercado"], candidata["ticker"].replace(".", "-").replace("$", "-"))
        nome_empresa = nomes.get(chave_nome, "")
        if not consultas_interrompidas:
            try:
                contagem = consulta_rss(candidata, agora, nome_empresa)
            except BloqueioRSS as erro:
                consultas_interrompidas = True
                LOG.error("RSS limitou as consultas (%s); as próximas ações ficam sem contagem.", erro)
            if contagem is None:
                falhas_seguidas += 1
                if falhas_seguidas >= 3:
                    consultas_interrompidas = True
                    LOG.error("RSS: três falhas seguidas; interrompendo novas consultas nesta execução.")
            else:
                falhas_seguidas = 0
        mercado, ticker = candidata["mercado"], candidata["ticker"]
        if contagem is None:
            razao, media, parcial = "", "", True
            contagem_csv = ""
        else:
            razao, media, parcial = calcular_razao(historico, mercado, ticker, hoje, contagem)
            # Sem nome empresarial, o RSS é útil como fallback, mas a consulta
            # não cobre integralmente os termos definidos no PDF.
            if not nome_empresa:
                parcial = True
            contagem_csv = contagem
        novas.append({
            "data": hoje.isoformat(),
            "mercado": mercado,
            "ticker": ticker,
            "volume_relativo": f"{candidata['volume_relativo']:.6f}",
            "noticias_24h": contagem_csv,
            "media_7d": f"{media:.6f}" if isinstance(media, (int, float)) else "",
            "razao_atencao": f"{razao:.6f}" if isinstance(razao, (int, float)) else "",
            "parcial": "sim" if parcial else "nao",
        })
        if contagem is None:
            LOG.warning("%s (%s): RSS indisponível; linha marcada como parcial", ticker, mercado)
        else:
            atencao_texto = f"{razao:.3f}" if isinstance(razao, (int, float)) else "indisponível"
            sufixo = " (parcial)" if parcial else ""
            LOG.info(
                "%s (%s): %d manchetes, atenção %s%s",
                ticker, mercado, contagem, atencao_texto, sufixo,
            )

    try:
        salvar_historico(args.historico, historico, novas)
    except OSError as erro:
        LOG.error("Não foi possível salvar %s: %s", args.historico, erro)
        return 1
    LOG.info("Histórico atualizado em %s (%d ações)", args.historico, len(novas))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
