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
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="description" content="Painel diário do Radar de pesquisa de ações da B3 e dos EUA.">
  <title>Radar de Ações | Pesquisa</title>
  <style>
    :root {{ color-scheme: light; --ink:#152a3b; --muted:#6b7d8c; --line:#e3eaf0; --paper:#f4f7fa; --white:#fff; --navy:#102b40; --teal:#087f8c; --teal-light:#e3f5f4; --orange:#e89a43; --green:#247a5a; --red:#bb5360; --shadow:0 12px 32px rgba(24,48,68,.07); }}
    * {{ box-sizing:border-box }}
    body {{ margin:0; background:var(--paper); color:var(--ink); font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif }}
    .shell {{ max-width:1440px; margin:auto; padding:30px clamp(16px,4vw,56px) 56px }}
    header {{ display:flex; justify-content:space-between; align-items:center; gap:24px; color:#fff; background:linear-gradient(120deg,#102b40,#17485b); border-radius:24px; padding:30px 34px; box-shadow:var(--shadow) }}
    .eyebrow {{ color:#9ed8d6; font-size:12px; font-weight:800; letter-spacing:.16em; text-transform:uppercase }}
    h1 {{ margin:5px 0 4px; font-size:clamp(27px,4vw,38px); letter-spacing:-.04em }}
    header p {{ margin:0; color:#c5d5dd }}
    .header-mark {{ width:62px; height:62px; border:1px solid #5a8994; border-radius:18px; display:grid; place-items:center; font-size:30px; color:#a6e4dc; flex:0 0 auto }}
    .toolbar {{ display:flex; justify-content:space-between; align-items:end; gap:18px; flex-wrap:wrap; margin:26px 0 18px }}
    .toolbar label {{ display:grid; gap:6px; color:var(--muted); font-size:12px; font-weight:800; text-transform:uppercase; letter-spacing:.07em }}
    select {{ min-width:220px; border:1px solid var(--line); border-radius:11px; padding:12px 42px 12px 14px; background:#fff; color:var(--ink); font:inherit; font-weight:700 }}
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
    .company-cell {{ min-width:230px; max-width:320px }}
    .company-name {{ font-weight:800; color:var(--navy) }}
    .company-details {{ margin-top:3px; color:var(--muted); font-size:12px; line-height:1.35; display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:2; overflow:hidden }}
    .score {{ font-weight:850; color:#126d70 }}
    .badge {{ display:inline-block; margin-left:5px; padding:2px 6px; border-radius:999px; background:#fff2dc; color:#8a5c17; font-size:10px; font-weight:800; vertical-align:middle }}
    .positive {{ color:var(--green); font-weight:750 }} .negative {{ color:var(--red); font-weight:750 }}
    .reason {{ min-width:280px; color:#526675; line-height:1.45 }}
    .empty {{ padding:24px; color:var(--muted); text-align:center }}
    footer {{ display:flex; justify-content:space-between; gap:16px; margin-top:22px; color:var(--muted); font-size:12px }}
    .loading {{ opacity:.55; pointer-events:none }}
    @media(max-width:900px) {{ .cards {{ grid-template-columns:repeat(2,minmax(0,1fr)) }} .section-grid {{ grid-template-columns:1fr }} }}
    @media(max-width:560px) {{ .shell {{ padding:14px 12px 32px }} header {{ padding:23px 20px; border-radius:18px }} .header-mark {{ width:48px;height:48px;font-size:23px }} .cards {{ gap:9px }} .card {{ min-height:108px;padding:14px }} .card-value {{ font-size:25px }} .panel {{ padding:17px }} .table-panel {{ padding:0 }} .table-heading {{ padding:18px 16px 4px }} footer {{ flex-direction:column }} }}
  </style>
</head>
<body>
<main class="shell" id="app">
  <header><div><div class="eyebrow">Painel de pesquisa</div><h1>Radar de Ações</h1><p>Volume, momento e atenção em um só lugar.</p></div><div class="header-mark" aria-hidden="true">⌁</div></header>
  <div class="toolbar"><label>Data da watchlist<select id="dateSelect" aria-label="Selecione o dia da watchlist"></select></label><div class="asof" id="asof">Carregando arquivos…</div></div>
  <div class="notice">Ferramenta de estudo. Não é recomendação de investimento e não executa operações. Notas marcadas como parciais usam dados incompletos.</div>
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
function metricCard(label,value,detail){{return `<article class="card"><div class="card-label">${{label}}</div><div class="card-value">${{value}}</div><div class="card-detail">${{detail}}</div></article>`}}
function marketChart(name,rows){{
  const top=rows.slice(0,5); const max=Math.max(1,...top.map(x=>x.nota));
  return `<article class="panel"><h2>${{name}} · maiores notas</h2><p class="panel-sub">Top 5 da watchlist selecionada</p>${{top.length?top.map(x=>`<div class="bar-row"><span class="bar-ticker">${{safe(x.ticker)}}</span><div class="bar-track"><div class="bar" style="width:${{Math.max(3,x.nota/max*100)}}%"></div></div><span class="bar-value">${{fmt(x.nota)}}</span></div>`).join(''):'<div class="empty">Sem ações neste mercado.</div>'}}</article>`
}}
function marketTable(name,rows){{
 const body=rows.map((x,i)=>{{const detalhes=[x.bolsa,x.descricao].filter(v=>v&&v!=='—').map(safe).join(' · ')||'—';return `<tr><td class="num">${{i+1}}</td><td class="ticker">${{safe(x.ticker)}}</td><td class="company-cell"><div class="company-name">${{safe(x.empresa||'—')}}</div><div class="company-details">${{detalhes}}</div></td><td class="num score">${{fmt(x.nota)}}${{x.parcial?'<span class="badge">parcial</span>':''}}</td><td class="num">${{fmt(x.volume_relativo,2)}}x</td><td class="num ${{x.retorno_5>=0?'positive':'negative'}}">${{pct(x.retorno_5)}}</td><td class="num ${{x.retorno_20==null?'':x.retorno_20>=0?'positive':'negative'}}">${{pct(x.retorno_20)}}</td><td class="num">${{x.noticias==null?'—':safe(x.noticias)}}</td><td class="reason">${{safe(x.motivo)}}</td></tr>`}}).join('');
 return `<article class="panel table-panel"><div class="table-heading"><div><h2>${{name}}</h2><p class="panel-sub">${{rows.length}} ações na watchlist</p></div><span class="market-tag">${{name==='B3'?'BRASIL':'ESTADOS UNIDOS'}}</span></div>${{rows.length?`<div class="table-wrap"><table><thead><tr><th class="num">#</th><th>Ticker</th><th>Empresa</th><th class="num">Nota</th><th class="num">Vol. relativo</th><th class="num">Ret. 5 pregões</th><th class="num">Ret. 20 pregões</th><th class="num">Notícias 24h</th><th>Motivo</th></tr></thead><tbody>${{body}}</tbody></table></div>`:'<div class="empty">Nenhuma ação nesta seção.</div>'}}</article>`
}}
function render(data,date){{
 const b3=data.mercados.B3.acoes,eua=data.mercados.EUA.acoes;
 const topB3=b3[0],topEua=eua[0];
 $('#asof').textContent=`Referência dos dados: B3 ${{data.mercados.B3.referencia||date}} · EUA ${{data.mercados.EUA.referencia||date}}`;
 $('#cards').innerHTML=[
  metricCard('Ações B3',b3.length,'na watchlist desta data'),
  metricCard('Ações EUA',eua.length,'na watchlist desta data'),
  metricCard('Maior nota · B3',topB3?fmt(topB3.nota):'—',topB3?`${{safe(topB3.ticker)}} · volume ${{fmt(topB3.volume_relativo,2)}}x`:'sem dados'),
  metricCard('Maior nota · EUA',topEua?fmt(topEua.nota):'—',topEua?`${{safe(topEua.ticker)}} · volume ${{fmt(topEua.volume_relativo,2)}}x`:'sem dados'),
 ].join('');
 $('#charts').innerHTML=marketChart('B3',b3)+marketChart('EUA',eua);
 $('#tables').innerHTML=marketTable('B3',b3)+marketTable('EUA',eua);
 $('#updated').textContent=`Arquivo: watchlist_${{date}}.md`;
}}
async function iniciar(){{
 const select=$('#dateSelect');
 try{{
  const resposta=await fetch(`${{BASE}}/api/datas`,{{cache:'no-store'}}); if(!resposta.ok)throw new Error('Não foi possível listar as watchlists.');
  const datas=await resposta.json();
  if(!datas.length){{select.innerHTML='<option>Nenhuma watchlist encontrada</option>';$('#asof').textContent='O Radar ainda não gerou arquivos.';$('#cards').innerHTML='';return;}}
  select.innerHTML=datas.map((d,i)=>`<option value="${{d}}">${{i===0?'Mais recente · ':''}}${{d}}</option>`).join('');
  async function carregar(){{
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


def resolver_get(url: str, base_path: str) -> tuple[int, bytes, str]:
    """Resolve rotas de leitura; separado do socket para permitir testes locais."""
    base = "/" + base_path.strip("/") if base_path.strip("/") else ""
    requisicao = urlsplit(url)
    caminho = requisicao.path.rstrip("/") or "/"
    api_path = caminho[len(base):] if base and caminho.startswith(base + "/") else caminho

    if caminho in {"/", base or "/", base + "/"}:
        return 200, html_dashboard(base).encode("utf-8"), "text/html; charset=utf-8"
    if api_path == "/healthz":
        return 200, b"ok", "text/plain; charset=utf-8"
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
    return 404, b"not found", "text/plain; charset=utf-8"


def criar_servidor(host: str, port: int, base_path: str) -> ThreadingHTTPServer:
    """Cria o servidor; a senha é aplicada no EasyPanel/Traefik."""
    base = "/" + base_path.strip("/") if base_path.strip("/") else ""

    class Handler(BaseHTTPRequestHandler):
        server_version = "RadarDashboard/1.0"

        def responder(self, status: int, conteudo: bytes, tipo: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(conteudo)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(conteudo)

        def do_GET(self) -> None:  # noqa: N802 - nome definido pela biblioteca padrão
            status, corpo, tipo = resolver_get(self.path, base)
            self.responder(status, corpo, tipo)

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
