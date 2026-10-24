from __future__ import annotations

import io
import tempfile
import urllib.parse
import zipfile
import unittest
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

from radar import b3
import atencao
import eua
import nota


class RespostaFalsa:
    def __init__(self, conteudo: bytes):
        self.conteudo = conteudo

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return self.conteudo


class TestRadar(unittest.TestCase):
    def test_filtra_universo_das_bolsas_dos_eua(self):
        arquivos = {
            "nasdaqlisted.txt": (
                "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
                "AAA|Alpha Common Stock|Q|N|N|100|N|N\n"
                "ETF1|Fund Common Stock|Q|N|N|100|Y|N\n"
                "TEST|Test Corp Common Stock|Q|Y|N|100|N|N\n"
                "File Creation Time: today\n"
            ).encode(),
            "otherlisted.txt": (
                "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue\n"
                "BBB|Beta Common Shares|N|BBB|N|100|N\n"
                "CCC|Gamma Ordinary Shares|A|CCC|N|100|N\n"
                "DDD|Delta Common Stock|P|DDD|N|100|N\n"
            ).encode(),
        }

        def resposta_falsa(pedido, **_kwargs):
            chave = "nasdaqlisted.txt" if "nasdaqlisted" in pedido.full_url else "otherlisted.txt"
            return RespostaFalsa(arquivos[chave])

        with patch.object(eua.urllib.request, "urlopen", side_effect=resposta_falsa):
            selecionadas = eua.simbolos_comuns()
        self.assertEqual(set(selecionadas), {"AAA", "BBB", "CCC", "DDD"})

    def test_coleta_eua_processa_lote_simulado(self):
        import pandas as pd

        dias = pd.date_range("2026-01-01", periods=25, freq="B")
        colunas = pd.MultiIndex.from_product([["AAA"], ["Close", "Volume"]])
        dados = pd.DataFrame([[100 + i, 100_000] for i in range(25)], index=dias, columns=colunas)
        with patch.object(eua.yf, "download", return_value=dados):
            linhas = eua.coletar(["AAA"])
        self.assertEqual(len(linhas), 25)
        self.assertEqual(linhas[-1][1], "AAA")
        self.assertGreater(linhas[-1][4], 5_000_000)

    def test_le_registro_cotahist_de_largura_fixa(self):
        linha = bytearray(b" " * 188)
        linha[0:2] = b"01"
        linha[2:10] = b"20261002"
        linha[10:12] = b"02"
        linha[12:24] = b"PETR4       "
        linha[24:27] = b"010"
        linha[27:39] = b"PETROBRAS   "
        linha[108:121] = b"0000000012345"
        linha[152:170] = b"000000000000001000"
        linha[170:188] = b"000000000000123450"
        conteudo_zip = io.BytesIO()
        with zipfile.ZipFile(conteudo_zip, "w") as arquivo:
            arquivo.writestr("COTAHIST.TXT", bytes(linha))

        with patch.object(b3.urllib.request, "urlopen", return_value=RespostaFalsa(conteudo_zip.getvalue())):
            dados, teve_dados = b3.baixar_pregao(date(2026, 10, 2))

        self.assertEqual(dados, [["2026-10-02", "PETR4", 123.45, 1000, 1234.5]])
        self.assertTrue(teve_dados)

    def test_rss_conta_titulos_unicos_das_ultimas_24_horas(self):
        agora = datetime(2026, 10, 4, 20, tzinfo=timezone.utc)
        publicacoes = [
            ("Alpha News: Resultados", agora - timedelta(hours=1)),
            ("ALPHA NEWS resultados", agora - timedelta(hours=2)),
            ("Manchete antiga", agora - timedelta(hours=25)),
            ("Manchete futura", agora + timedelta(hours=1)),
        ]
        itens = "".join(
            f"<item><title>{titulo}</title><pubDate>{format_datetime(instante)}</pubDate></item>"
            for titulo, instante in publicacoes
        )
        xml = f"<rss><channel>{itens}</channel></rss>".encode()
        candidata = {"mercado": "B3", "ticker": "ABC"}
        urls = []

        def resposta(pedido, **_kwargs):
            urls.append(pedido.full_url)
            return RespostaFalsa(xml)

        with patch.object(atencao.urllib.request, "urlopen", side_effect=resposta):
            total = atencao.consulta_rss(candidata, agora, "Alpha Energia")
        self.assertEqual(total, 1)
        self.assertIn('"ABC" OR "Alpha Energia"', urllib.parse.unquote_plus(urls[0]))

    def test_carrega_nome_empresarial_de_cadastro_delimitado_por_ponto_e_virgula(self):
        with tempfile.TemporaryDirectory() as temporario:
            caminho = Path(temporario) / "empresas.csv"
            caminho.write_text("Ticker;Nome da Empresa;Bolsa\nAAA;Alpha S.A.;B3\n", encoding="utf-8")
            nomes = atencao.carregar_nomes_empresas({"B3": caminho})
        self.assertEqual(nomes[("B3", "AAA")], "Alpha S.A.")

    def test_atencao_usa_sete_observacoes_anteriores(self):
        hoje = date(2026, 10, 4)
        dias = []
        dia = hoje - timedelta(days=1)
        while len(dias) < 7:
            if dia.weekday() < 5:
                dias.append(dia)
            dia -= timedelta(days=1)
        historico = [
            {"data": data.isoformat(), "mercado": "B3", "ticker": "ABC", "noticias_24h": str(indice + 1)}
            for indice, data in enumerate(dias)
        ]
        razao, media, parcial = atencao.calcular_razao(historico, "B3", "ABC", hoje, 14)
        self.assertEqual(media, 4.0)
        self.assertEqual(razao, 3.5)
        self.assertFalse(parcial)

    def test_atencao_exige_sete_dias_nao_parciais_para_razao_valida(self):
        """Verifica que razão só é calculada com 7 dias completos não-parciais;
        caso contrário retorna None e parcial=True."""
        hoje = date(2026, 10, 4)
        dias = []
        dia = hoje - timedelta(days=1)
        while len(dias) < 7:
            if dia.weekday() < 5:
                dias.append(dia)
            dia -= timedelta(days=1)

        # Cenário 1: 1 dia parcial + 6 completos → 6 válidos → INSuficiente
        historico_1_parcial = [
            {"data": data.isoformat(), "mercado": "B3", "ticker": "ABC",
             "noticias_24h": str(indice + 1), "parcial": "sim" if indice == 0 else "nao"}
            for indice, data in enumerate(dias)
        ]
        razao, media, parcial = atencao.calcular_razao(historico_1_parcial, "B3", "ABC", hoje, 14)
        self.assertIsNone(razao)
        self.assertIsNone(media)
        self.assertTrue(parcial)

        # Cenário 2: 7 dias completos, média=0, hoje=3 → razão indefinida
        historico_zeros = [
            {"data": data.isoformat(), "mercado": "B3", "ticker": "ABC",
             "noticias_24h": "0", "parcial": "nao"}
            for data in dias
        ]
        razao, media, parcial = atencao.calcular_razao(historico_zeros, "B3", "ABC", hoje, 3)
        self.assertIsNone(razao)
        self.assertEqual(media, 0.0)
        self.assertTrue(parcial)

        # Cenário 3: 7 dias completos, média=4.0, hoje=14 → razão=3.5
        historico_normal = [
            {"data": data.isoformat(), "mercado": "B3", "ticker": "ABC",
             "noticias_24h": str(indice + 1), "parcial": "nao"}
            for indice, data in enumerate(dias)
        ]
        razao, media, parcial = atencao.calcular_razao(historico_normal, "B3", "ABC", hoje, 14)
        self.assertEqual(razao, 3.5)
        self.assertEqual(media, 4.0)
        self.assertFalse(parcial)

    def test_coleta_eua_exclui_split_recente(self):
        import pandas as pd

        dias = pd.date_range("2026-01-01", periods=25, freq="B")
        colunas = pd.MultiIndex.from_product([["AAA"], ["Close", "Volume", "Stock Splits"]])
        linhas = [[100 + i, 100_000, 2 if i == 22 else 0] for i in range(25)]
        dados = pd.DataFrame(linhas, index=dias, columns=colunas)
        with patch.object(eua.yf, "download", return_value=dados):
            resultado = eua.coletar(["AAA"])
        self.assertEqual(resultado, [])

    def test_indicadores_ignoram_cotacao_posterior(self):
        inicio = date(2026, 9, 1)
        serie = [
            {"data": inicio + timedelta(days=i), "fechamento": 100 + i, "volume_financeiro": 2_000_000}
            for i in range(22)
        ]
        data_corte = serie[-2]["data"]
        resultado = nota.indicadores(serie, data_corte)
        self.assertEqual(resultado["data"], data_corte)
        self.assertAlmostEqual(resultado["fechamento"], 120)
        self.assertAlmostEqual(resultado["retorno_5"], 5 / 115)

    def test_watchlist_csv_contem_campos_e_mercado(self):
        with tempfile.TemporaryDirectory() as temporario:
            caminho = Path(temporario) / "watchlist.csv"
            item = {
                "ticker": "ABC", "nota": 80.0, "nota_parcial": True,
                "volume_relativo": 2.0, "retorno_5": 0.1, "retorno_20": 0.2,
                "noticias": 3, "atencao": 1.5, "media_7d": 2.0,
                "atencao_parcial": False,
            }
            data = date(2026, 10, 2)
            nota.gravar_watchlist_csv(caminho, {"B3": (data, [item]), "EUA": (None, [])})
            import csv
            with caminho.open(newline="", encoding="utf-8-sig") as arquivo:
                registros = list(csv.DictReader(arquivo))
        self.assertEqual(len(registros), 1)
        self.assertEqual(registros[0]["mercado"], "B3")
        self.assertEqual(registros[0]["data"], "2026-10-02")
        self.assertEqual(registros[0]["nota_parcial"], "sim")


if __name__ == "__main__":
    unittest.main()
