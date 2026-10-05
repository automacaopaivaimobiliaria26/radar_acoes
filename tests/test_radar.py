from __future__ import annotations

import io
import zipfile
import unittest
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
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
        self.assertEqual(set(selecionadas), {"AAA", "BBB", "CCC"})

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
            dados = b3.baixar_pregao(date(2026, 10, 2))

        self.assertEqual(dados, [["2026-10-02", "PETR4", 123.45, 1000, 1234.5]])

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
        with patch.object(atencao.urllib.request, "urlopen", return_value=RespostaFalsa(xml)):
            total = atencao.consulta_rss(candidata, agora)
        self.assertEqual(total, 1)

    def test_atencao_usa_sete_observacoes_anteriores(self):
        hoje = date(2026, 10, 4)
        historico = [
            {"data": (hoje - timedelta(days=dias)).isoformat(), "mercado": "B3", "ticker": "ABC", "noticias_24h": str(dias)}
            for dias in range(1, 8)
        ]
        razao, media, parcial = atencao.calcular_razao(historico, "B3", "ABC", hoje, 14)
        self.assertEqual(media, 4.0)
        self.assertEqual(razao, 3.5)
        self.assertFalse(parcial)

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


if __name__ == "__main__":
    unittest.main()
