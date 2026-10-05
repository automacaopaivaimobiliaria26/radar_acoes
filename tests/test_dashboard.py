"""Testes locais do dashboard sem rede externa ou Docker."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dashboard


AMOSTRA = """# Radar de pesquisa. Nao e recomendacao de investimento.

## B3 — referência: 2026-10-02

| Ticker | Nota | Volume relativo | Retorno 5 pregões | Notícias 24h | Motivo |
|---|---:|---:|---:|---:|---|
| JSLG3 | 99.4 (parcial) | 16.87x | +28.48% | 3 | volume 16.87x a média de 20 pregões; retorno de 5 pregões +28.5%; retorno de 20 pregões +42.2%; 3 manchetes em 24h; atenção parcial |

## EUA — referência: 2026-10-02

| Ticker | Nota | Volume relativo | Retorno 5 pregões | Notícias 24h | Motivo |
|---|---:|---:|---:|---:|---|
| AAOI | 94.7 | 1.70x | +13.99% | — | volume 1.70x a média de 20 pregões; retorno de 5 pregões +14.0%; retorno de 20 pregões +15.2%; notícias indisponíveis |
"""


class DashboardTestes(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saida = Path(self.temp.name)
        (self.saida / "watchlist_2026-10-02.md").write_text(AMOSTRA, encoding="utf-8")
        (self.saida / "watchlist_data-invalida.md").write_text("ignorar", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_parseia_tabelas_dos_dois_mercados(self) -> None:
        resultado = dashboard.ler_watchlist(self.saida / "watchlist_2026-10-02.md")
        b3 = resultado["mercados"]["B3"]["acoes"][0]
        eua = resultado["mercados"]["EUA"]["acoes"][0]
        self.assertEqual(b3["ticker"], "JSLG3")
        self.assertEqual(b3["nota"], 99.4)
        self.assertTrue(b3["parcial"])
        self.assertAlmostEqual(b3["retorno_5"], 0.2848)
        self.assertAlmostEqual(b3["retorno_20"], 0.422)
        self.assertEqual(b3["noticias"], 3)
        self.assertEqual(eua["ticker"], "AAOI")
        self.assertIsNone(eua["noticias"])

    def test_enriquece_watchlist_com_cadastro_e_prioriza_valores_reais(self) -> None:
        cadastro = self.saida / "referencias"
        cadastro.mkdir()
        (cadastro / "empresas_b3.csv").write_text(
            '"Descrição resumida";Ticker;Bolsa;"Nome Empresa"\n'
            'Serviços de logística;JSLG3;B3;JSL S.A.\n', encoding="utf-8-sig"
        )
        (cadastro / "empresas_nyse_nasdaq.csv").write_text(
            "Bolsa,Descrição resumida,Ticker,Nome da Empresa\n"
            "NASDAQ,Equipment and technology,AAOI,Applied Optoelectronics Inc.\n", encoding="utf-8"
        )
        watchlist = self.saida / "watchlist_enriquecida.md"
        watchlist.write_text(
            "## B3 — referência: 2026-10-02\n\n"
            "| Ticker | Empresa | Bolsa | Descrição | Nota | Volume relativo | Retorno 5 pregões | Notícias 24h | Motivo |\n"
            "|---|---|---|---|---:|---:|---:|---:|---|\n"
            "| JSLG3.SA | JSL Participações | Não informada | Descrição indisponível | 90 | 2.5x | +5.0% | 1 | retorno de 20 pregões +8.0% |\n\n"
            "## EUA — referência: 2026-10-02\n\n"
            "| Ticker | Empresa | Bolsa | Descrição | Nota | Volume relativo | Retorno 5 pregões | Notícias 24h | Motivo |\n"
            "|---|---|---|---|---:|---:|---:|---:|---|\n"
            "| AAOI | AAOI | — | - | 80 | 1.5x | -2.0% | 0 | retorno de 20 pregões +1.0% |\n",
            encoding="utf-8",
        )
        with patch.object(dashboard, "PASTA_CADASTRO", cadastro):
            resultado = dashboard.ler_watchlist(watchlist)

        b3 = resultado["mercados"]["B3"]["acoes"][0]
        eua = resultado["mercados"]["EUA"]["acoes"][0]
        self.assertEqual(b3["empresa"], "JSL Participações")
        self.assertEqual(b3["bolsa"], "B3")
        self.assertEqual(b3["descricao"], "Serviços de logística")
        self.assertEqual(eua["empresa"], "Applied Optoelectronics Inc.")
        self.assertEqual(eua["bolsa"], "NASDAQ")
        self.assertEqual(eua["descricao"], "Equipment and technology")

    def test_lista_datas_validas_da_mais_recente_para_mais_antiga(self) -> None:
        (self.saida / "watchlist_2026-10-03.md").write_text(AMOSTRA, encoding="utf-8")
        with patch.object(dashboard, "PASTA_SAIDA", self.saida):
            self.assertEqual(dashboard.listar_datas(), ["2026-10-03", "2026-10-02"])

    def test_api_lista_datas_e_devolve_watchlist(self) -> None:
        with patch.object(dashboard, "PASTA_SAIDA", self.saida):
            status, corpo, _tipo = dashboard.resolver_get("/radar/api/datas", "/radar")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(corpo), ["2026-10-02"])
            status, corpo, _tipo = dashboard.resolver_get("/api/watchlist?data=2026-10-02", "/radar")
        self.assertEqual(status, 200)
        dados = json.loads(corpo)
        self.assertEqual(dados["mercados"]["B3"]["acoes"][0]["ticker"], "JSLG3")
        self.assertEqual(dados["mercados"]["EUA"]["acoes"][0]["ticker"], "AAOI")

    def test_api_rejeita_data_invalida_e_arquivo_inexistente(self) -> None:
        with patch.object(dashboard, "PASTA_SAIDA", self.saida):
            for caminho, esperado in (("/api/watchlist?data=../../etc/passwd", 400), ("/api/watchlist?data=2026-10-03", 404)):
                with self.subTest(caminho=caminho):
                    status, _corpo, _tipo = dashboard.resolver_get(caminho, "/radar")
                    self.assertEqual(status, esperado)


if __name__ == "__main__":
    unittest.main()
