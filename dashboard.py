#!/usr/bin/env python3
"""Dashboard de leitura das watchlists Markdown do Radar."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import unicodedata
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


PASTA_SAIDA = Path("/app/saida")
PASTA_CADASTRO = Path(os.environ.get("RADAR_CADASTRO_DIR", Path(__file__).resolve().parent / "referencias"))
PADRAO_ARQUIVO = re.compile(r"^watchlist_(\d{4}-\d{2}-\d{2})\.md$")
PADRAO_SECAO = re.compile(r"^##\s+(B3|EUA)\s+—\s+referência:\s*(.+?)\s*$", re.IGNORECASE)
PADRAO_NUMERO = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
LOG = logging.getLogger("radar.dashboard")
_CACHE_CADASTRO: dict[Path, tuple[int | None, int | None, dict[str, dict[str, str]]]] = {}


def normalizar_ticker(ticker: str) -> str:
    """Normaliza tickers de referência e watchlist para a chave de cruzamento."""
    normalizado = (ticker or "").strip().upper()
    return normalizado[:-3] if normalizado.endswith(".SA") else normalizado


def _normalizar_cabecalho(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.casefold())
    texto = "".join(caractere for caractere in texto if not unicodedata.combining(caractere))
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def _ler_arquivo_cadastro(caminho: Path) -> dict[str, dict[str, str]]:
    """Carrega um CSV de empresas, aceitando cabeçalhos em qualquer ordem."""
    conteudo = None
    for codificacao in ("utf-8-sig", "latin-1"):
        try:
            conteudo = caminho.read_text(encoding=codificacao)
            break
        except UnicodeDecodeError:
            continue
    if conteudo is None:
        raise UnicodeError(f"Não foi possível decodificar {caminho}")

    primeira_linha = next((linha for linha in conteudo.splitlines() if linha.strip()), "")
    try:
        separador = csv.Sniffer().sniff(primeira_linha, delimiters=",;").delimiter
    except csv.Error:
        separador = ";" if primeira_linha.count(";") > primeira_linha.count(",") else ","

    leitor = csv.DictReader(conteudo.splitlines(), delimiter=separador)
    if not leitor.fieldnames:
        raise ValueError("CSV sem cabeçalho")
    campos = {_normalizar_cabecalho(campo): campo for campo in leitor.fieldnames if campo}

    def localizar(predicado) -> str | None:
        return next((original for normalizado, original in campos.items() if predicado(normalizado)), None)

    coluna_ticker = localizar(lambda texto: "ticker" in texto or "simbolo" in texto)
    coluna_empresa = localizar(lambda texto: "nome" in texto or "empresa" in texto)
    coluna_bolsa = localizar(lambda texto: "bolsa" in texto)
    coluna_descricao = localizar(lambda texto: "descri" in texto)
    if not coluna_ticker or not coluna_empresa:
        raise ValueError("CSV precisa ter colunas de ticker/símbolo e nome/empresa")

    cadastro: dict[str, dict[str, str]] = {}
    for linha in leitor:
        ticker = normalizar_ticker(linha.get(coluna_ticker) or "")
        if not ticker:
            continue
        cadastro[ticker] = {
            "empresa": (linha.get(coluna_empresa) or "").strip(),
            "bolsa": (linha.get(coluna_bolsa) or "").strip() if coluna_bolsa else "",
            "descricao": (linha.get(coluna_descricao) or "").strip() if coluna_descricao else "",
        }
    return cadastro


def _carregar_arquivo_cadastro(caminho: Path) -> dict[str, dict[str, str]]:
    """Usa cache por caminho, tamanho e data de modificação; falhas são isoladas."""
    try:
        stat = caminho.stat()
        modificacao, tamanho = stat.st_mtime_ns, stat.st_size
    except OSError as erro:
        anterior = _CACHE_CADASTRO.get(caminho)
        if anterior and anterior[:2] == (None, None):
            return anterior[2]
        LOG.warning("Cadastro de empresas indisponível em %s: %s", caminho, erro)
        vazio: dict[str, dict[str, str]] = {}
        _CACHE_CADASTRO[caminho] = (None, None, vazio)
        return vazio

    anterior = _CACHE_CADASTRO.get(caminho)
    if anterior and anterior[:2] == (modificacao, tamanho):
        return anterior[2]
    try:
        cadastro = _ler_arquivo_cadastro(caminho)
    except (OSError, UnicodeError, csv.Error, ValueError) as erro:
        LOG.warning("Erro ao ler cadastro de empresas %s: %s", caminho, erro)
        cadastro = {}
    _CACHE_CADASTRO[caminho] = (modificacao, tamanho, cadastro)
    return cadastro


def carregar_cadastro() -> dict[str, dict[str, dict[str, str]]]:
    """Carrega os cadastros da B3 e dos EUA, com fallback para o CSV americano antigo."""
    arquivo_b3 = PASTA_CADASTRO / "empresas_b3.csv"
    arquivo_eua_novo = PASTA_CADASTRO / "empresas_nyse_nasdaq_bolsa.csv"
    arquivo_eua_antigo = PASTA_CADASTRO / "empresas_nyse_nasdaq.csv"
    arquivo_eua = arquivo_eua_novo if arquivo_eua_novo.is_file() else arquivo_eua_antigo
    return {
        "B3": _carregar_arquivo_cadastro(arquivo_b3),
        "EUA": _carregar_arquivo_cadastro(arquivo_eua),
    }


def _valor_watchlist_valido(valor: str, ticker: str, campo: str) -> bool:
    texto = (valor or "").strip()
    marcadores = {"", "nao informada", "descricao indisponivel"}
    if _normalizar_cabecalho(texto) in marcadores:
        return False
    if campo == "empresa" and normalizar_ticker(texto) == normalizar_ticker(ticker):
        return False
    return True


def numero(texto: str) -> float | None:
    """Extrai número de nota, volume relativo ou retorno formatado."""
    encontrado = PADRAO_NUMERO.search(texto.replace("%", ""))
    if not encontrado:
        return None
    try:
        return float(encontrado.group().replace(",", "."))
    except ValueError:
        return None


def ler_watchlist(caminho: Path) -> dict[str, object]:
    """Converte as duas tabelas Markdown em dados para a página."""
    cadastro_por_mercado = carregar_cadastro()
    mercados: dict[str, dict[str, object]] = {
        "B3": {"referencia": None, "acoes": []},
        "EUA": {"referencia": None, "acoes": []},
    }
    mercado_atual: str | None = None
    colunas: dict[str, int] = {}

    for linha in caminho.read_text(encoding="utf-8").splitlines():
        secao = PADRAO_SECAO.match(linha.strip())
        if secao:
            mercado_atual = secao.group(1).upper()
            mercados[mercado_atual]["referencia"] = secao.group(2)
            continue
        if not mercado_atual or not linha.strip().startswith("|"):
            continue

        celulas = [parte.strip() for parte in linha.strip().strip("|").split("|")]
        if celulas and celulas[0].casefold() == "ticker":
            colunas = {nome.casefold(): indice for indice, nome in enumerate(celulas)}
            continue
        if not celulas or all(re.fullmatch(r":?-{2,}:?", parte) for parte in celulas):
            continue

        # Mantém compatibilidade com arquivos antigos que não tinham cabeçalho.
        if not colunas:
            colunas = {"ticker": 0, "nota": 1, "volume relativo": 2, "retorno 5 pregões": 3,
                       "notícias 24h": 4, "motivo": 5}
        def campo(nome: str, padrao: str = "") -> str:
            indice = colunas.get(nome.casefold())
            return celulas[indice] if indice is not None and indice < len(celulas) else padrao

        nota = numero(campo("nota"))
        volume = numero(campo("volume relativo"))
        retorno_5 = numero(campo("retorno 5 pregões"))
        noticias = numero(campo("notícias 24h"))
        if nota is None or volume is None or retorno_5 is None:
            continue

        retorno_20 = None
        motivo = campo("motivo")
        captura_20 = re.search(r"retorno de 20 pregões\s+([-+]?\d+(?:[.,]\d+)?)%", motivo, re.IGNORECASE)
        if captura_20:
            retorno_20 = numero(captura_20.group(1))
        acoes = mercados[mercado_atual]["acoes"]
        assert isinstance(acoes, list)
        ticker = campo("ticker")
        cadastro = cadastro_por_mercado.get(mercado_atual, {}).get(normalizar_ticker(ticker), {})
        empresa_watchlist = campo("empresa", campo("nome da empresa"))
        bolsa_watchlist = campo("bolsa")
        descricao_watchlist = campo("descrição", campo("descricao", campo("descrição resumida")))
        acoes.append({
            "ticker": ticker,
            "empresa": empresa_watchlist if _valor_watchlist_valido(empresa_watchlist, ticker, "empresa") else cadastro.get("empresa") or "—",
            "bolsa": bolsa_watchlist if _valor_watchlist_valido(bolsa_watchlist, ticker, "bolsa") else cadastro.get("bolsa") or "—",
            "descricao": descricao_watchlist if _valor_watchlist_valido(descricao_watchlist, ticker, "descricao") else cadastro.get("descricao") or "—",
            "nota": nota,
            "parcial": "parcial" in campo("nota").casefold() or "nota parcial" in motivo.casefold(),
            "volume_relativo": volume,
            "retorno_5": retorno_5 / 100,
            "retorno_20": retorno_20 / 100 if retorno_20 is not None else None,
            "noticias": int(noticias) if noticias is not None else None,
            "motivo": motivo,
        })

    for dados in mercados.values():
        acoes = dados["acoes"]
        assert isinstance(acoes, list)
        acoes.sort(key=lambda item: (-item["nota"], item["ticker"]))

    return {"mercados": mercados}


def listar_datas() -> list[str]:
    """Lista somente os arquivos com nome de watchlist e data válida."""
    datas = []
    if not PASTA_SAIDA.exists():
        return datas
    for caminho in PASTA_SAIDA.iterdir():
        correspondencia = PADRAO_ARQUIVO.fullmatch(caminho.name)
        if not correspondencia or not caminho.is_file():
            continue
        texto = correspondencia.group(1)
        try:
            datetime.strptime(texto, "%Y-%m-%d")
        except ValueError:
            continue
        datas.append(texto)
    return sorted(datas, reverse=True)


def html_dashboard(base_path: str) -> str:
    """Página responsiva com filtros, cartões, gráficos de barras e tabelas."""
    base_json = json.dumps(base_path)
    href_watchlists = base_path + "/" if base_path else "/"
    href_resultados = (base_path or "") + "/resultados"
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="description" content="Painel diário do Radar de pesquisa de ações da B3 e dos EUA.">
  <title>Radar de Ações | Pesquisa</title>
  <style>
    :root {{ color-scheme: light; --ink:#152a3b; --muted:#6b7d8c; --line:#e3eaf0; --paper:#f4f7fa; --white:#fff; --navy:#102b40; --teal:#087f8c; --teal-light:#e3f5f4; --orange:#e89a43; --green:#247a5a; --red:#bb5360; --shadow:0 12px 32px rgba(24,48,68,.07); }}
    * {{ box-sizing:border-box }}
    [hidden] {{ display:none !important }}
    body {{ margin:0; background:var(--paper); color:var(--ink); font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif }}
    .shell {{ max-width:1440px; margin:auto; padding:30px clamp(16px,4vw,56px) 56px }}
    header {{ display:flex; justify-content:space-between; align-items:center; gap:24px; color:#fff; background:linear-gradient(120deg,#102b40,#17485b); border-radius:24px; padding:30px 34px; box-shadow:var(--shadow) }}
    .eyebrow {{ color:#9ed8d6; font-size:12px; font-weight:800; letter-spacing:.16em; text-transform:uppercase }}
    h1 {{ margin:5px 0 4px; font-size:clamp(27px,4vw,38px); letter-spacing:-.04em }}
    header p {{ margin:0; color:#c5d5dd }}
    .header-mark {{ width:62px; height:62px; border:1px solid #5a8994; border-radius:18px; display:grid; place-items:center; font-size:30px; color:#a6e4dc; flex:0 0 auto }}
    .main-nav {{ display:flex; gap:8px; margin:18px 0 4px; border-bottom:1px solid var(--line) }}
    .nav-link {{ display:inline-block; padding:10px 15px; color:var(--muted); font-weight:800; text-decoration:none; border-bottom:3px solid transparent }}
    .nav-link:hover,.nav-link.active {{ color:var(--teal); border-color:var(--teal) }}
    .toolbar {{ display:flex; justify-content:space-between; align-items:end; gap:18px; flex-wrap:wrap; margin:26px 0 18px }}
    .toolbar label {{ display:grid; gap:6px; color:var(--muted); font-size:12px; font-weight:800; text-transform:uppercase; letter-spacing:.07em }}
    select {{ min-width:220px; border:1px solid var(--line); border-radius:11px; padding:12px 42px 12px 14px; background:#fff; color:var(--ink); font:inherit; font-weight:700 }}
    .toolbar-actions {{ display:flex; align-items:center; justify-content:flex-end; gap:14px; flex-wrap:wrap }}
    .download-button {{ border:0; border-radius:11px; padding:12px 16px; background:var(--teal); color:#fff; font:inherit; font-weight:800; text-decoration:none; cursor:pointer }}
    .download-button:hover:not(:disabled) {{ background:#066b75 }}
    .download-button:disabled {{ opacity:.5; cursor:not-allowed }}
    .asof {{ color:var(--muted); font-size:13px }}
    .notice {{ padding:12px 15px; margin:0 0 20px; border:1px solid #f0d7b2; border-radius:12px; color:#75501c; background:#fff8ed; font-size:13px }}
    .cards {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin-bottom:18px }}
    .card {{ min-height:125px; padding:19px 20px; border:1px solid var(--line); border-radius:17px; background:var(--white); box-shadow:var(--shadow) }}
    .card-label {{ color:var(--muted); font-size:12px; font-weight:800; letter-spacing:.06em; text-transform:uppercase }}
    .card-value {{ margin-top:11px; font-size:30px; font-weight:850; letter-spacing:-.04em; line-height:1 }}
    .card-detail {{ margin-top:8px; color:var(--muted); font-size:13px }}
    .section-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin:18px 0 }}
    .panel {{ min-width:0; padding:22px; border:1px solid var(--line); border-radius:18px; background:var(--white); box-shadow:var(--shadow) }}
    .panel h2 {{ margin:0; font-size:19px; letter-spacing:-.02em }}
    .panel-sub {{ margin:4px 0 17px; color:var(--muted); font-size:13px }}
    .bar-row {{ display:grid; grid-template-columns:65px 1fr 68px; gap:10px; align-items:center; margin:11px 0; font-size:13px }}
    .bar-ticker {{ font-weight:800 }}
    .bar-track {{ height:10px; overflow:hidden; border-radius:99px; background:#edf2f5 }}
    .bar {{ height:100%; border-radius:99px; background:linear-gradient(90deg,var(--teal),#53b7ac) }}
    .bar-value {{ text-align:right; color:var(--muted); font-weight:750; font-variant-numeric:tabular-nums }}
    .table-panel {{ padding:0; overflow:hidden; margin-top:18px }}
    .table-heading {{ display:flex; justify-content:space-between; align-items:center; gap:12px; padding:21px 22px 4px }}
    .market-tag {{ padding:5px 9px; border-radius:999px; background:var(--teal-light); color:#126b72; font-size:11px; font-weight:850; letter-spacing:.08em }}
    .table-wrap {{ overflow:auto; padding:0 14px 14px }}
    table {{ width:100%; border-collapse:collapse; min-width:1050px; font-size:13px }}
    th {{ padding:12px 10px; border-bottom:1px solid var(--line); color:var(--muted); text-align:left; font-size:11px; letter-spacing:.06em; text-transform:uppercase; white-space:nowrap }}
    td {{ padding:13px 10px; border-bottom:1px solid #edf1f4; vertical-align:top }}
    tbody tr:hover {{ background:#f8fbfc }}
    td.num, th.num {{ text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums }}
    .ticker {{ font-weight:850; color:var(--navy) }}
    .ticker-link {{ color:inherit; text-decoration:underline; text-decoration-color:#9ab9c5; text-underline-offset:3px; cursor:pointer }}
    .ticker-link:hover {{ color:var(--teal); text-decoration-color:currentColor }}
    .company-cell {{ min-width:230px; max-width:320px }}
    .company-name {{ font-weight:800; color:var(--navy) }}
    .company-details {{ margin-top:3px; color:var(--muted); font-size:12px; line-height:1.35; display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:2; overflow:hidden }}
    .score {{ font-weight:850; color:#126d70 }}
    .badge {{ display:inline-block; margin-left:5px; padding:2px 6px; border-radius:999px; background:#fff2dc; color:#8a5c17; font-size:10px; font-weight:800; vertical-align:middle }}
    .positive {{ color:var(--green); font-weight:750 }} .negative {{ color:var(--red); font-weight:750 }}
    .reason {{ min-width:280px; color:#526675; line-height:1.45 }}
    .empty {{ padding:24px; color:var(--muted); text-align:center }}
    .chart-view {{ margin:0 0 18px }}
    .chart-heading {{ display:flex; justify-content:space-between; align-items:center; gap:16px; margin-bottom:14px }}
    .chart-heading h2 {{ margin:7px 0 2px; font-size:clamp(21px,3vw,29px); letter-spacing:-.03em }}
    .chart-company {{ margin:0; color:var(--muted); font-size:14px }}
    .chart-back {{ flex:0 0 auto; border:1px solid var(--line); border-radius:10px; padding:10px 14px; background:#fff; color:var(--navy); font:inherit; font-weight:750; cursor:pointer }}
    .chart-back:hover {{ border-color:var(--teal); color:var(--teal) }}
    .chart-status {{ min-height:22px; margin:0 0 10px; color:var(--muted); font-size:13px }}
    .tv-chart-frame {{ width:100%; height:min(78vh,800px); min-height:500px; overflow:hidden; border:1px solid var(--line); border-radius:14px; background:#fff }}
    .tv-chart-frame .tradingview-widget-container, .tv-chart-frame .tradingview-widget-container__widget {{ width:100%; height:100% }}
    .tv-chart-frame iframe {{ display:block; width:100% !important; height:100% !important }}
    .chart-attribution {{ margin:9px 2px 0; color:var(--muted); font-size:11px }}
    footer {{ display:flex; justify-content:space-between; gap:16px; margin-top:22px; color:var(--muted); font-size:12px }}
    .loading {{ opacity:.55; pointer-events:none }}
    @media(max-width:900px) {{ .cards {{ grid-template-columns:repeat(2,minmax(0,1fr)) }} .section-grid {{ grid-template-columns:1fr }} }}
    @media(max-width:560px) {{ .shell {{ padding:14px 12px 32px }} header {{ padding:23px 20px; border-radius:18px }} .header-mark {{ width:48px;height:48px;font-size:23px }} .cards {{ gap:9px }} .card {{ min-height:108px;padding:14px }} .card-value {{ font-size:25px }} .panel {{ padding:17px }} .table-panel {{ padding:0 }} .table-heading {{ padding:18px 16px 4px }} .chart-heading {{ align-items:flex-start; flex-direction:column }} .chart-back {{ width:100% }} .tv-chart-frame {{ height:75vh; min-height:500px }} footer {{ flex-direction:column }} }}
  </style>
</head>
<body>
<main class="shell" id="app">
  <header><div><div class="eyebrow">Painel de pesquisa</div><h1>Radar de Ações</h1><p>Volume, momento e atenção em um só lugar.</p></div><div class="header-mark" aria-hidden="true">⌁</div></header>
  <nav class="main-nav" aria-label="Navegação principal"><a class="nav-link active" href="{href_watchlists}">Watchlists</a><a class="nav-link" href="{href_resultados}">Resultados</a></nav>
  <div class="toolbar" id="watchlistToolbar"><label>Data da watchlist<select id="dateSelect" aria-label="Selecione o dia da watchlist"></select></label><div class="toolbar-actions"><button class="download-button" id="downloadButton" type="button" disabled>Baixar watchlist (.md)</button><div class="asof" id="asof">Carregando arquivos…</div></div></div>
  <div class="notice">Ferramenta de estudo. Não é recomendação de investimento e não executa operações. Notas marcadas como parciais usam dados incompletos.</div>
  <section class="panel chart-view" id="chartView" hidden aria-labelledby="chartTitle">
    <div class="chart-heading"><div><span class="market-tag" id="chartMarket"></span><h2 id="chartTitle">Gráfico do ativo</h2><p class="chart-company" id="chartCompany"></p></div><button class="chart-back" id="chartBack" type="button">← Voltar à watchlist</button></div>
    <p class="chart-status" id="chartStatus" role="status" aria-live="polite"></p>
    <div class="tv-chart-frame" id="chartWidget"></div>
    <p class="chart-attribution">Gráfico fornecido pelo TradingView. A disponibilidade e o atraso das cotações dependem da bolsa e dos dados oferecidos pelo TradingView.</p>
  </section>
  <section class="cards" id="cards"></section>
  <section class="section-grid" id="charts"></section>
  <section id="tables"></section>
  <footer><span>Os percentis são calculados separadamente para B3 e EUA.</span><span id="updated"></span></footer>
</main>
<script>
const BASE={base_json};
const $=s=>document.querySelector(s);
const fmt=(n,d=1)=>n==null?'—':new Intl.NumberFormat('pt-BR',{{minimumFractionDigits:d,maximumFractionDigits:d}}).format(n);
const pct=n=>n==null?'—':`${{n>=0?'+':''}}${{fmt(n*100,2)}}%`;
const safe=x=>String(x??'—').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
let dadosAtuais=null,dataAtual=null,sequenciaGrafico=0;
function parametrosGrafico(){{
 if(!location.hash.startsWith('#grafico?'))return null;
 return new URLSearchParams(location.hash.slice(location.hash.indexOf('?')+1));
}}
function linkTicker(acao,mercado){{
 const parametros=new URLSearchParams({{mercado,ticker:acao.ticker,data:dataAtual||''}});
 return `<a class="ticker-link" href="${{safe(`#grafico?${{parametros.toString()}}`)}}" title="Abrir gráfico de ${{safe(acao.ticker)}}">${{safe(acao.ticker)}}</a>`;
}}
function metricCard(label,value,detail){{return `<article class="card"><div class="card-label">${{label}}</div><div class="card-value">${{value}}</div><div class="card-detail">${{detail}}</div></article>`}}
function marketChart(name,rows,mercado){{
  const top=rows.slice(0,5); const max=Math.max(1,...top.map(x=>x.nota));
  return `<article class="panel"><h2>${{name}} · maiores notas</h2><p class="panel-sub">Top 5 da watchlist selecionada · clique no ticker para abrir o gráfico</p>${{top.length?top.map(x=>`<div class="bar-row"><span class="bar-ticker">${{linkTicker(x,mercado)}}</span><div class="bar-track"><div class="bar" style="width:${{Math.max(3,x.nota/max*100)}}%"></div></div><span class="bar-value">${{fmt(x.nota)}}</span></div>`).join(''):'<div class="empty">Sem ações neste mercado.</div>'}}</article>`
}}
function marketTable(name,rows){{
 const body=rows.map((x,i)=>{{const detalhes=[x.bolsa,x.descricao].filter(v=>v&&v!=='—').map(safe).join(' · ')||'—';return `<tr><td class="num">${{i+1}}</td><td class="ticker">${{linkTicker(x,name==='B3'?'B3':'EUA')}}</td><td class="company-cell"><div class="company-name">${{safe(x.empresa||'—')}}</div><div class="company-details">${{detalhes}}</div></td><td class="num score">${{fmt(x.nota)}}${{x.parcial?'<span class="badge">parcial</span>':''}}</td><td class="num">${{fmt(x.volume_relativo,2)}}x</td><td class="num ${{x.retorno_5>=0?'positive':'negative'}}">${{pct(x.retorno_5)}}</td><td class="num ${{x.retorno_20==null?'':x.retorno_20>=0?'positive':'negative'}}">${{pct(x.retorno_20)}}</td><td class="num">${{x.noticias==null?'—':safe(x.noticias)}}</td><td class="reason">${{safe(x.motivo)}}</td></tr>`}}).join('');
 return `<article class="panel table-panel"><div class="table-heading"><div><h2>${{name}}</h2><p class="panel-sub">${{rows.length}} ações na watchlist</p></div><span class="market-tag">${{name==='B3'?'BRASIL':'ESTADOS UNIDOS'}}</span></div>${{rows.length?`<div class="table-wrap"><table><thead><tr><th class="num">#</th><th>Ticker</th><th>Empresa</th><th class="num">Nota</th><th class="num">Vol. relativo</th><th class="num">Ret. 5 pregões</th><th class="num">Ret. 20 pregões</th><th class="num">Notícias 24h</th><th>Motivo</th></tr></thead><tbody>${{body}}</tbody></table></div>`:'<div class="empty">Nenhuma ação nesta seção.</div>'}}</article>`
}}
function render(data,date){{
 dadosAtuais=data;dataAtual=date;
 $('#downloadButton').disabled=false;
 const b3=data.mercados.B3.acoes,eua=data.mercados.EUA.acoes;
 const topB3=b3[0],topEua=eua[0];
 $('#asof').textContent=`Referência dos dados: B3 ${{data.mercados.B3.referencia||date}} · EUA ${{data.mercados.EUA.referencia||date}}`;
 $('#cards').innerHTML=[
  metricCard('Ações B3',b3.length,'na watchlist desta data'),
  metricCard('Ações EUA',eua.length,'na watchlist desta data'),
  metricCard('Maior nota · B3',topB3?fmt(topB3.nota):'—',topB3?`${{linkTicker(topB3,'B3')}} · volume ${{fmt(topB3.volume_relativo,2)}}x`:'sem dados'),
  metricCard('Maior nota · EUA',topEua?fmt(topEua.nota):'—',topEua?`${{linkTicker(topEua,'EUA')}} · volume ${{fmt(topEua.volume_relativo,2)}}x`:'sem dados'),
 ].join('');
 $('#charts').innerHTML=marketChart('B3',b3,'B3')+marketChart('EUA',eua,'EUA');
 $('#tables').innerHTML=marketTable('B3',b3)+marketTable('EUA',eua);
 $('#updated').textContent=`Arquivo: watchlist_${{date}}.md`;
 atualizarTelaGrafico();
}}
function simboloTradingView(mercado,acao){{
 const ticker=String(acao.ticker||'').trim().toUpperCase().replace(/\\.SA$/,'');
 if(!/^[A-Z0-9][A-Z0-9._-]{{0,19}}$/.test(ticker))return null;
 if(mercado==='B3')return `BMFBOVESPA:${{ticker}}`;
 const bolsa=String(acao.bolsa||'').trim().toLocaleLowerCase('pt-BR');
 if(bolsa.includes('nasdaq'))return `NASDAQ:${{ticker}}`;
 if(bolsa.includes('nyse american')||bolsa.includes('amex'))return `AMEX:${{ticker}}`;
 if(bolsa.includes('nyse'))return `NYSE:${{ticker}}`;
 return null;
}}
function adicionarLinkExterno(texto,url){{
 const link=document.createElement('a');link.href=url;link.target='_blank';
 link.rel='noopener noreferrer';link.textContent=texto;$('#chartStatus').append(' ',link);
}}
function atualizarTelaGrafico(){{
 const parametros=parametrosGrafico(),ids=['watchlistToolbar','cards','charts','tables'];
 const chartView=$('#chartView');
 if(!parametros){{
  sequenciaGrafico++;
  ids.forEach(id=>{{const elemento=$(`#${{id}}`);if(elemento)elemento.hidden=false;}});
  chartView.hidden=true;
  return;
 }}
 ids.forEach(id=>{{const elemento=$(`#${{id}}`);if(elemento)elemento.hidden=true;}});
 chartView.hidden=false;
 const mercado=parametros.get('mercado'),ticker=parametros.get('ticker'),data=parametros.get('data');
 const acao=dadosAtuais?.mercados?.[mercado]?.acoes?.find(item=>item.ticker===ticker);
 $('#chartMarket').textContent=mercado==='B3'?'B3':'ESTADOS UNIDOS';
 $('#chartTitle').textContent=ticker||'Gráfico do ativo';
 $('#chartCompany').textContent=acao?.empresa&&acao.empresa!=='—'?acao.empresa:`${{mercado==='B3'?'B3':'EUA'}} · ${{ownerDate(data)}}`;
 const area=$('#chartWidget');area.replaceChildren();
 const sequencia=++sequenciaGrafico;
 if(!acao){{$('#chartStatus').textContent='Esse ticker não está disponível na watchlist selecionada.';return;}}
 const simbolo=simboloTradingView(mercado,acao);
 if(!simbolo){{
  $('#chartStatus').textContent='Não foi possível identificar a bolsa deste ticker para formar o símbolo do TradingView.';
  adicionarLinkExterno('Buscar ticker no TradingView.',`https://www.tradingview.com/search/?query=${{encodeURIComponent(ticker||'')}}`);
  return;
 }}
 $('#chartStatus').textContent=`Carregando gráfico de ${{simbolo}}…`;
 const container=document.createElement('div');
 container.className='tradingview-widget-container';
 const quadro=document.createElement('div');
 quadro.className='tradingview-widget-container__widget';
 const credito=document.createElement('div');
 credito.className='tradingview-widget-copyright';
 const link=document.createElement('a');
 link.href=`https://br.tradingview.com/symbols/${{encodeURIComponent(simbolo.replace(':','-'))}}/`;
 link.rel='noopener nofollow';link.target='_blank';
 const texto=document.createElement('span');
 texto.className='blue-text';texto.textContent=`Gráfico de ações de ${{simbolo}}`;
 link.append(texto);credito.append(link,document.createTextNode(' por TradingView'));
 const widget=document.createElement('script');
 widget.type='text/javascript';widget.async=true;
 widget.src='https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';
 widget.textContent=JSON.stringify({{
  allow_symbol_change:true,
  calendar:false,
  details:false,
  hide_side_toolbar:true,
  hide_top_toolbar:false,
  hide_legend:false,
  hide_volume:false,
  hotlist:false,
  interval:'D',
  locale:'br',
  save_image:true,
  style:'1',
  symbol:simbolo,
  theme:'light',
  timezone:'America/Sao_Paulo',
  backgroundColor:'#ffffff',
  gridColor:'rgba(46, 46, 46, 0.2)',
  watchlist:[],
  withdateranges:false,
  compareSymbols:[],
  support_host:'https://www.tradingview.com',
  studies:['STD;24h%Volume'],
  autosize:true
 }});
 widget.onload=()=>{{if(sequencia===sequenciaGrafico)$('#chartStatus').textContent='';}};
 widget.onerror=()=>{{
  if(sequencia===sequenciaGrafico){{
   $('#chartStatus').textContent='O widget externo não pôde ser carregado.';
   adicionarLinkExterno('Abrir gráfico no TradingView.',`https://www.tradingview.com/chart/?symbol=${{encodeURIComponent(simbolo)}}`);
  }}
 }};
 container.append(quadro,credito);
 area.replaceChildren(container);
 container.append(widget);
}}
function ownerDate(data){{return data||'data selecionada';}}
$('#chartBack').addEventListener('click',()=>{{location.hash='';}});
window.addEventListener('hashchange',atualizarTelaGrafico);
async function iniciar(){{
 const select=$('#dateSelect');
 const downloadButton=$('#downloadButton');
 downloadButton.addEventListener('click',()=>{{
  if(!dataAtual)return;
  const parametros=new URLSearchParams({{data:dataAtual}});
  window.location.assign(`${{BASE}}/api/download?${{parametros.toString()}}`);
 }});
 try{{
  const resposta=await fetch(`${{BASE}}/api/datas`,{{cache:'no-store'}}); if(!resposta.ok)throw new Error('Não foi possível listar as watchlists.');
  const datas=await resposta.json();
  if(!datas.length){{select.innerHTML='<option>Nenhuma watchlist encontrada</option>';$('#asof').textContent='O Radar ainda não gerou arquivos.';$('#cards').innerHTML='';return;}}
  select.innerHTML=datas.map((d,i)=>`<option value="${{d}}">${{i===0?'Mais recente · ':''}}${{d}}</option>`).join('');
  const dataSolicitada=parametrosGrafico()?.get('data');if(dataSolicitada&&datas.includes(dataSolicitada))select.value=dataSolicitada;
  async function carregar(){{
   downloadButton.disabled=true;
   $('#app').classList.add('loading');
   try{{const r=await fetch(`${{BASE}}/api/watchlist?data=${{encodeURIComponent(select.value)}}`,{{cache:'no-store'}});if(!r.ok)throw new Error('Arquivo da data selecionada indisponível.');render(await r.json(),select.value);}}
   catch(e){{$('#asof').textContent=e.message;$('#cards').innerHTML='';$('#charts').innerHTML='';$('#tables').innerHTML='';}}
   finally{{$('#app').classList.remove('loading');}}
  }}
  select.addEventListener('change',carregar); await carregar();
 }}catch(e){{$('#asof').textContent=e.message;}}
}}
iniciar();
</script>
</body></html>"""


def montar_resultados() -> dict[str, object]:
    """Agrega as observações de cada ação em todos os arquivos de watchlist."""
    datas = sorted(listar_datas())
    mercados: dict[str, dict[str, dict[str, object]]] = {"B3": {}, "EUA": {}}
    for data in datas:
        arquivo = PASTA_SAIDA / f"watchlist_{data}.md"
        try:
            watchlist = ler_watchlist(arquivo)
        except (OSError, UnicodeError, ValueError) as erro:
            LOG.exception("Falha ao agregar resultados de %s: %s", arquivo, erro)
            continue
        dados_mercados = watchlist.get("mercados", {})
        for mercado in ("B3", "EUA"):
            dados_mercado = dados_mercados.get(mercado, {})
            acoes = dados_mercado.get("acoes", [])
            for acao in acoes:
                ticker = str(acao.get("ticker") or "").strip().upper()
                if not ticker:
                    continue
                registro = mercados[mercado].setdefault(ticker, {
                    "ticker": ticker,
                    "empresa": acao.get("empresa") or "—",
                    "bolsa": acao.get("bolsa") or "—",
                    "observacoes": {},
                })
                if registro.get("empresa") in (None, "", "—") and acao.get("empresa"):
                    registro["empresa"] = acao["empresa"]
                if registro.get("bolsa") in (None, "", "—") and acao.get("bolsa"):
                    registro["bolsa"] = acao["bolsa"]
                observacoes = registro["observacoes"]
                assert isinstance(observacoes, dict)
                observacoes[data] = {
                    "nota": acao.get("nota"),
                    "parcial": bool(acao.get("parcial")),
                    "volume_relativo": acao.get("volume_relativo"),
                    "retorno_5": acao.get("retorno_5"),
                    "retorno_20": acao.get("retorno_20"),
                }

    resultado: dict[str, object] = {"datas": datas, "mercados": {}}
    saida_mercados = resultado["mercados"]
    assert isinstance(saida_mercados, dict)
    for mercado, acoes in mercados.items():
        lista = []
        for ticker, registro in acoes.items():
            observacoes = registro.pop("observacoes")
            assert isinstance(observacoes, dict)
            registro["serie"] = [
                {"data": data, **observacoes[data]} if data in observacoes else {
                    "data": data,
                    "nota": None,
                    "parcial": False,
                    "volume_relativo": None,
                    "retorno_5": None,
                    "retorno_20": None,
                }
                for data in datas
            ]
            lista.append(registro)
        lista.sort(key=lambda item: item["ticker"])
        saida_mercados[mercado] = lista
    return resultado


def html_resultados(base_path: str) -> str:
    """Página histórica com evolução por ativo e por indicador."""
    return r'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Evolução histórica das ações que apareceram nas watchlists do Radar.">
<title>Radar de Ações | Resultados</title>
<style>
:root{color-scheme:light;--ink:#152a3b;--muted:#6b7d8c;--line:#e3eaf0;--paper:#f4f7fa;--white:#fff;--navy:#102b40;--teal:#087f8c;--teal-light:#e3f5f4;--green:#247a5a;--red:#bb5360;--shadow:0 12px 32px rgba(24,48,68,.07)}
*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.shell{max-width:1500px;margin:auto;padding:30px clamp(14px,4vw,56px) 56px}header{display:flex;justify-content:space-between;align-items:center;gap:24px;color:#fff;background:linear-gradient(120deg,#102b40,#17485b);border-radius:24px;padding:27px 32px;box-shadow:var(--shadow)}
.eyebrow{color:#9ed8d6;font-size:12px;font-weight:800;letter-spacing:.16em;text-transform:uppercase}h1{margin:5px 0 4px;font-size:clamp(27px,4vw,38px);letter-spacing:-.04em}header p{margin:0;color:#c5d5dd}.header-mark{width:58px;height:58px;border:1px solid #5a8994;border-radius:18px;display:grid;place-items:center;font-size:29px;color:#a6e4dc;flex:0 0 auto}
.main-nav{display:flex;gap:8px;margin:18px 0 20px;border-bottom:1px solid var(--line)}.nav-link{display:inline-block;padding:10px 15px;color:var(--muted);font-weight:800;text-decoration:none;border-bottom:3px solid transparent}.nav-link:hover,.nav-link.active{color:var(--teal);border-color:var(--teal)}
.notice{padding:12px 15px;margin:0 0 18px;border:1px solid #cce6e5;border-radius:12px;color:#275c60;background:#f0fbfa;font-size:13px}.toolbar{display:flex;align-items:end;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:16px}.filters{display:flex;align-items:end;gap:10px;flex-wrap:wrap}.field{display:grid;gap:5px;color:var(--muted);font-size:11px;font-weight:800;letter-spacing:.06em;text-transform:uppercase}.field input,.field select{min-height:40px;min-width:150px;border:1px solid var(--line);border-radius:10px;padding:8px 11px;background:#fff;color:var(--ink);font:inherit;font-weight:650}.field.search input{min-width:220px}.period{color:var(--muted);font-size:12px}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}.card,.panel{background:#fff;border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow)}.card{padding:15px 17px;min-height:96px}.card-label{color:var(--muted);font-size:11px;font-weight:800;letter-spacing:.06em;text-transform:uppercase}.card-value{margin-top:7px;font-size:25px;font-weight:850;letter-spacing:-.03em;line-height:1}.card-detail{margin-top:5px;color:var(--muted);font-size:12px}
.market-tabs{display:flex;gap:8px;margin:18px 0 12px}.market-tab{border:1px solid var(--line);border-radius:999px;background:#fff;padding:9px 17px;color:var(--muted);font:inherit;font-weight:800;cursor:pointer}.market-tab.active{background:var(--navy);border-color:var(--navy);color:#fff}.table-panel{overflow:hidden}.table-heading{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:18px 20px 10px}.table-heading h2{margin:0;font-size:19px}.table-heading p{margin:3px 0 0;color:var(--muted);font-size:12px}.market-tag{padding:5px 9px;border-radius:999px;background:var(--teal-light);color:#126b72;font-size:10px;font-weight:850;letter-spacing:.08em}
.table-wrap{overflow:auto;padding:0 12px 12px}table{width:100%;min-width:1030px;border-collapse:collapse;font-size:12px}th{position:sticky;top:0;z-index:1;padding:10px 9px;border-bottom:1px solid var(--line);background:#fff;color:var(--muted);text-align:left;font-size:10px;letter-spacing:.05em;text-transform:uppercase;white-space:nowrap}td{padding:10px 9px;border-bottom:1px solid #edf1f4;vertical-align:middle}tbody tr:hover{background:#f8fbfc}.ticker{font-weight:850;color:var(--navy);white-space:nowrap}.company{min-width:140px;color:var(--muted);font-size:11px}.latest{font-variant-numeric:tabular-nums;font-weight:800;text-align:right;white-space:nowrap}.metric-cell{min-width:150px}.spark-wrap{display:flex;align-items:center;gap:7px}.spark{display:block;width:92px;height:34px;overflow:visible}.spark-value{min-width:48px;text-align:right;font-variant-numeric:tabular-nums;font-weight:750;white-space:nowrap}.positive{color:var(--green)}.negative{color:var(--red)}.partial{display:inline-block;margin-left:4px;padding:1px 5px;border-radius:99px;background:#fff2dc;color:#8a5c17;font-size:9px;font-weight:800;vertical-align:middle}.expand{border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--teal);font:inherit;font-weight:800;cursor:pointer;padding:5px 8px}.detail-row td{padding:0 15px 15px;background:#f8fbfc}.history{width:100%;min-width:0;margin-top:10px}.history th{position:static;background:#f8fbfc}.history td,.history th{padding:7px 9px}.empty{padding:25px;color:var(--muted);text-align:center}.error{padding:16px;border:1px solid #edc8cb;border-radius:12px;background:#fff4f4;color:#8b3441}.footer{margin-top:15px;color:var(--muted);font-size:11px}
@media(max-width:960px){.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.filters{width:100%}}@media(max-width:600px){.shell{padding:13px 10px 30px}header{padding:21px 18px;border-radius:17px}.header-mark{width:45px;height:45px}.cards{gap:8px}.card{padding:12px;min-height:88px}.card-value{font-size:21px}.filters{display:grid;grid-template-columns:1fr 1fr}.field.search{grid-column:1/-1}.field.search input{min-width:0}.period{width:100%}.table-heading{padding:15px 14px 8px}}
</style></head><body><main class="shell" id="app">
<header><div><div class="eyebrow">Histórico das watchlists</div><h1>Resultados</h1><p>Acompanhe a evolução dos indicadores por ação.</p></div><div class="header-mark" aria-hidden="true">↗</div></header>
<nav class="main-nav" aria-label="Navegação principal"><a class="nav-link" id="watchlistLink">Watchlists</a><a class="nav-link active" id="resultsLink">Resultados</a></nav>
<div class="notice">Cada ponto corresponde a uma watchlist em que a ação apareceu. Ausência em uma data é exibida como lacuna, sem assumir valor zero.</div>
<section class="toolbar"><div class="filters"><label class="field search">Buscar ação<input id="search" type="search" placeholder="Ticker ou empresa"></label><label class="field">De<input id="from" type="date"></label><label class="field">Até<input id="to" type="date"></label><label class="field">Ordenar<select id="sort"><option value="ticker">Ticker (A–Z)</option><option value="nota">Nota mais alta</option><option value="volume_relativo">Vol. relativo</option><option value="retorno_5">Ret. 5 pregões</option><option value="retorno_20">Ret. 20 pregões</option></select></label></div><div id="period" class="period"></div></section>
<section id="cards" class="cards"></section><div class="market-tabs" role="tablist"><button class="market-tab active" data-market="B3" role="tab">B3</button><button class="market-tab" data-market="EUA" role="tab">EUA</button></div>
<section class="panel table-panel"><div class="table-heading"><div><h2 id="marketTitle">B3</h2><p id="tableCaption"></p></div><span id="marketTag" class="market-tag">BRASIL</span></div><div class="table-wrap" id="tableWrap"></div></section>
<div class="footer">Retornos são os valores registrados nas watchlists. Notas parciais permanecem sinalizadas.</div></main>
<script>
const BASE=__BASE__,METRICS=[['nota','Nota'],['volume_relativo','Vol. relativo'],['retorno_5','Ret. 5 pregões'],['retorno_20','Ret. 20 pregões']];let dados=null,mercadoAtual='B3';
const $=s=>document.querySelector(s),esc=x=>String(x??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function label(metric,v){if(v==null)return '—';if(metric==='nota')return new Intl.NumberFormat('pt-BR',{maximumFractionDigits:1}).format(v);if(metric==='volume_relativo')return `${new Intl.NumberFormat('pt-BR',{maximumFractionDigits:2}).format(v)}x`;return `${v>=0?'+':''}${new Intl.NumberFormat('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2}).format(v*100)}%`}
function datasVisiveis(){const a=$('#from').value,b=$('#to').value;return dados.datas.filter(d=>(!a||d>=a)&&(!b||d<=b))}
function observacaoAtual(acao,dates){const ok=new Set(dates);return [...acao.serie].reverse().find(o=>ok.has(o.data)&&o.nota!=null)||null}
function spark(serie,metric,dates){const pontos=dates.map(d=>serie.find(x=>x.data===d)).map(x=>x&&x[metric]!=null?x[metric]:null),w=92,h=34,p=3,vals=pontos.filter(x=>x!=null);if(!vals.length)return '<svg class="spark" viewBox="0 0 92 34" aria-label="Sem observações no período"></svg>';let lo=Math.min(...vals),hi=Math.max(...vals);if(lo===hi){lo-=Math.abs(lo||1)*.08;hi+=Math.abs(hi||1)*.08}const xy=(v,i)=>[p+(dates.length<=1?0:i*(w-2*p)/(dates.length-1)),h-p-(v-lo)/(hi-lo)*(h-2*p)];let seg=[],segments=[];pontos.forEach((v,i)=>{if(v==null){if(seg.length)segments.push(seg);seg=[]}else seg.push(xy(v,i))});if(seg.length)segments.push(seg);const d=segments.map(s=>s.map((q,i)=>`${i?'L':'M'}${q[0].toFixed(1)},${q[1].toFixed(1)}`).join(' ')).join(' ');const last=segments.at(-1).at(-1);return `<svg class="spark" viewBox="0 0 ${w} ${h}" role="img" aria-label="Evolução de ${esc(METRICS.find(m=>m[0]===metric)[1])}"><path d="${d}" fill="none" stroke="#087f8c" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="${last[0]}" cy="${last[1]}" r="2.5" fill="#087f8c"/></svg>`}
function historyRows(acao,dates){const rows=dates.slice().reverse().map(d=>{const o=acao.serie.find(x=>x.data===d);if(!o||o.nota==null)return '';return `<tr><td>${esc(d)}</td><td class="latest">${label('nota',o.nota)}${o.parcial?'<span class="partial">parcial</span>':''}</td><td class="latest">${label('volume_relativo',o.volume_relativo)}</td><td class="latest ${o.retorno_5>=0?'positive':'negative'}">${label('retorno_5',o.retorno_5)}</td><td class="latest ${o.retorno_20==null?'':o.retorno_20>=0?'positive':'negative'}">${label('retorno_20',o.retorno_20)}</td></tr>`}).filter(Boolean).join('');return `<table class="history"><thead><tr><th>Data da watchlist</th><th>Nota</th><th>Vol. relativo</th><th>Ret. 5 pregões</th><th>Ret. 20 pregões</th></tr></thead><tbody>${rows||'<tr><td colspan="5">Sem observações neste período.</td></tr>'}</tbody></table>`}
function render(){if(!dados)return;const dates=datasVisiveis(),all=dados.mercados[mercadoAtual]||[],query=$('#search').value.trim().toLocaleLowerCase('pt-BR'),metricSort=$('#sort').value;let stocks=all.filter(a=>!query||a.ticker.toLocaleLowerCase('pt-BR').includes(query)||(a.empresa||'').toLocaleLowerCase('pt-BR').includes(query));stocks.sort((a,b)=>{if(metricSort==='ticker')return a.ticker.localeCompare(b.ticker);const av=observacaoAtual(a,dates)?.[metricSort],bv=observacaoAtual(b,dates)?.[metricSort];return (bv??-Infinity)-(av??-Infinity)||a.ticker.localeCompare(b.ticker)});const observations=all.reduce((n,a)=>n+a.serie.filter(o=>dates.includes(o.data)&&o.nota!=null).length,0);$('#cards').innerHTML=[['Ações no histórico',all.length],['Ações encontradas',stocks.length],['Watchlists no período',dates.length],['Observações',observations]].map(([k,v])=>`<article class="card"><div class="card-label">${k}</div><div class="card-value">${v}</div><div class="card-detail">${mercadoAtual==='B3'?'B3':'Estados Unidos'}</div></article>`).join('');$('#period').textContent=dates.length?`${dates[0]} a ${dates.at(-1)} · ${dates.length} datas`:'Sem watchlists neste intervalo';$('#marketTitle').textContent=mercadoAtual;$('#marketTag').textContent=mercadoAtual==='B3'?'BRASIL':'ESTADOS UNIDOS';$('#tableCaption').textContent=`${stocks.length} ações · ${observations} observações no período selecionado`;if(!stocks.length){$('#tableWrap').innerHTML='<div class="empty">Nenhuma ação encontrada com estes filtros.</div>';return}const rows=stocks.map((a,i)=>{const latest=observacaoAtual(a,dates);const company=a.empresa&&a.empresa!=='—'?a.empresa:'';const cells=METRICS.map(([key])=>`<td class="metric-cell"><div class="spark-wrap">${spark(a.serie,key,dates)}<span class="spark-value">${label(key,latest?.[key]??null)}</span></div></td>`).join('');return `<tbody><tr><td class="ticker">${esc(a.ticker)}</td><td class="company">${esc(company)}</td>${cells}<td><button class="expand" data-detail="${i}" aria-expanded="false">Detalhes</button></td></tr><tr class="detail-row" id="detail-${i}" hidden><td colspan="7">${historyRows(a,dates)}</td></tr></tbody>`}).join('');$('#tableWrap').innerHTML=`<table><thead><tr><th>Ação</th><th>Empresa</th>${METRICS.map(([,n])=>`<th>${n} · evolução</th>`).join('')}<th></th></tr></thead>${rows}</table>`;$('#tableWrap').querySelectorAll('.expand').forEach(b=>b.addEventListener('click',()=>{const tr=$(`#detail-${b.dataset.detail}`),open=tr.hidden;tr.hidden=!open;b.setAttribute('aria-expanded',String(open));b.textContent=open?'Fechar':'Detalhes'}))}
document.querySelectorAll('.market-tab').forEach(b=>b.addEventListener('click',()=>{mercadoAtual=b.dataset.market;document.querySelectorAll('.market-tab').forEach(x=>x.classList.toggle('active',x===b));render()}));['search','from','to','sort'].forEach(id=>$('#'+id).addEventListener('input',render));$('#sort').addEventListener('change',render);
$('#watchlistLink').href=BASE?BASE+'/':'/';$('#resultsLink').href=(BASE||'')+'/resultados';
async function start(){try{const r=await fetch(`${BASE}/api/resultados`,{cache:'no-store'});if(!r.ok)throw new Error('Não foi possível carregar o histórico.');dados=await r.json();if(dados.datas.length){$('#from').value=dados.datas[0];$('#to').value=dados.datas.at(-1)}render()}catch(e){$('#tableWrap').innerHTML=`<div class="error">${esc(e.message)}</div>`}}start();
</script></body></html>'''.replace("__BASE__", json.dumps(base_path))


def resolver_get(url: str, base_path: str) -> tuple[int, bytes, str]:
    """Resolve rotas de leitura; separado do socket para permitir testes locais."""
    base = "/" + base_path.strip("/") if base_path.strip("/") else ""
    requisicao = urlsplit(url)
    caminho = requisicao.path.rstrip("/") or "/"
    api_path = caminho[len(base):] if base and caminho.startswith(base + "/") else caminho

    if caminho in {"/", base or "/", base + "/"}:
        return 200, html_dashboard(base).encode("utf-8"), "text/html; charset=utf-8"
    if api_path == "/resultados":
        return 200, html_resultados(base).encode("utf-8"), "text/html; charset=utf-8"
    if api_path == "/healthz":
        return 200, b"ok", "text/plain; charset=utf-8"
    if api_path == "/api/resultados":
        try:
            corpo = json.dumps(montar_resultados(), ensure_ascii=False).encode("utf-8")
        except (OSError, UnicodeError, ValueError) as erro:
            LOG.exception("Falha ao montar o histórico de resultados: %s", erro)
            corpo = json.dumps({"erro": "Não foi possível carregar o histórico."}, ensure_ascii=False).encode("utf-8")
            return 500, corpo, "application/json; charset=utf-8"
        return 200, corpo, "application/json; charset=utf-8"
    if api_path == "/api/datas":
        corpo = json.dumps(listar_datas(), ensure_ascii=False).encode("utf-8")
        return 200, corpo, "application/json; charset=utf-8"
    if api_path == "/api/watchlist":
        parametros = parse_qs(requisicao.query)
        datas = parametros.get("data", [])
        if len(datas) != 1 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", datas[0]):
            corpo = json.dumps({"erro": "Data inválida."}, ensure_ascii=False).encode("utf-8")
            return 400, corpo, "application/json; charset=utf-8"
        try:
            datetime.strptime(datas[0], "%Y-%m-%d")
        except ValueError:
            corpo = json.dumps({"erro": "Data inválida."}, ensure_ascii=False).encode("utf-8")
            return 400, corpo, "application/json; charset=utf-8"
        arquivo = PASTA_SAIDA / f"watchlist_{datas[0]}.md"
        if not arquivo.is_file():
            corpo = json.dumps({"erro": "Watchlist não encontrada."}, ensure_ascii=False).encode("utf-8")
            return 404, corpo, "application/json; charset=utf-8"
        try:
            corpo = json.dumps(ler_watchlist(arquivo), ensure_ascii=False).encode("utf-8")
        except (OSError, UnicodeError, ValueError) as erro:
            LOG.exception("Falha ao ler %s: %s", arquivo, erro)
            corpo = json.dumps({"erro": "Não foi possível ler o arquivo."}, ensure_ascii=False).encode("utf-8")
            return 500, corpo, "application/json; charset=utf-8"
        return 200, corpo, "application/json; charset=utf-8"
    if api_path == "/api/download":
        parametros = parse_qs(requisicao.query)
        datas = parametros.get("data", [])
        if len(datas) != 1 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", datas[0]):
            corpo = json.dumps({"erro": "Data inválida."}, ensure_ascii=False).encode("utf-8")
            return 400, corpo, "application/json; charset=utf-8"
        try:
            datetime.strptime(datas[0], "%Y-%m-%d")
        except ValueError:
            corpo = json.dumps({"erro": "Data inválida."}, ensure_ascii=False).encode("utf-8")
            return 400, corpo, "application/json; charset=utf-8"
        arquivo = PASTA_SAIDA / f"watchlist_{datas[0]}.md"
        if not arquivo.is_file():
            corpo = json.dumps({"erro": "Watchlist não encontrada."}, ensure_ascii=False).encode("utf-8")
            return 404, corpo, "application/json; charset=utf-8"
        try:
            return 200, arquivo.read_bytes(), "text/markdown; charset=utf-8"
        except OSError as erro:
            LOG.exception("Falha ao preparar o download de %s: %s", arquivo, erro)
            corpo = json.dumps({"erro": "Não foi possível baixar o arquivo."}, ensure_ascii=False).encode("utf-8")
            return 500, corpo, "application/json; charset=utf-8"
    return 404, b"not found", "text/plain; charset=utf-8"


def criar_servidor(host: str, port: int, base_path: str) -> ThreadingHTTPServer:
    """Cria o servidor; a senha é aplicada no EasyPanel/Traefik."""
    base = "/" + base_path.strip("/") if base_path.strip("/") else ""

    class Handler(BaseHTTPRequestHandler):
        server_version = "RadarDashboard/1.0"

        def responder(self, status: int, conteudo: bytes, tipo: str, nome_arquivo: str | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(conteudo)))
            if nome_arquivo:
                self.send_header("Content-Disposition", f'attachment; filename="{nome_arquivo}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(conteudo)

        def do_GET(self) -> None:  # noqa: N802 - nome definido pela biblioteca padrão
            status, corpo, tipo = resolver_get(self.path, base)
            requisicao = urlsplit(self.path)
            caminho = requisicao.path.rstrip("/") or "/"
            api_path = caminho[len(base):] if base and caminho.startswith(base + "/") else caminho
            parametros = parse_qs(requisicao.query)
            datas = parametros.get("data", [])
            nome_arquivo = None
            if status == 200 and api_path == "/api/download" and len(datas) == 1:
                # O resolvedor já validou a data e confirmou que o arquivo existe.
                nome_arquivo = f"watchlist_{datas[0]}.md"
            self.responder(status, corpo, tipo, nome_arquivo)

        def log_message(self, formato: str, *args: object) -> None:
            LOG.info("%s - %s", self.address_string(), formato % args)

    return ThreadingHTTPServer((host, port), Handler)


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve o dashboard das watchlists do Radar.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--base-path", default="/radar", help="Prefixo de rota usado no domínio (ex.: /radar).")
    args = parser.parse_args()
    base_path = args.base_path.rstrip("/")
    if base_path and not base_path.startswith("/"):
        parser.error("--base-path deve começar com '/'")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    servidor = criar_servidor(args.host, args.port, base_path)
    LOG.info("Dashboard ouvindo em %s:%d com prefixo %s", args.host, args.port, base_path or "/")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
