from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import validacao


class TestValidacao(unittest.TestCase):
    def test_compara_top_com_universo_e_desconta_custo(self):
        inicio = date(2026, 1, 5)
        sessoes = [inicio + timedelta(days=dia) for dia in range(21)]
        sessoes = [dia for dia in sessoes if dia.weekday() < 5]
        precos = {
            "AAA": {dia: 100.0 + 2 * indice for indice, dia in enumerate(sessoes)},
            "BBB": {dia: 100.0 for dia in sessoes},
            "DELISTED": {sessoes[0]: 10.0},
        }
        avaliacao = validacao.avaliar_horizonte(
            sessoes[0], "B3", [{"ticker": "AAA", "nota": 90}], precos, 5
        )
        esperado_aaa = precos["AAA"][sessoes[5]] / precos["AAA"][sessoes[0]] - 1 - 0.001
        esperado_bbb = precos["BBB"][sessoes[5]] / precos["BBB"][sessoes[0]] - 1 - 0.001
        self.assertAlmostEqual(avaliacao["media_carteira"], esperado_aaa)
        self.assertAlmostEqual(avaliacao["media_universo"], (esperado_aaa + esperado_bbb) / 2)
        self.assertAlmostEqual(avaliacao["excesso"], esperado_aaa - (esperado_aaa + esperado_bbb) / 2)
        self.assertEqual(avaliacao["universo_faltantes"], 1)
        resumo = validacao.resumo([avaliacao])
        self.assertAlmostEqual(resumo["media_acao"], esperado_aaa)
        self.assertAlmostEqual(resumo["mediana_acao"], esperado_aaa)

    def test_horizonte_sem_dados_futuros_fica_pendente(self):
        inicio = date(2026, 1, 5)
        precos = {"AAA": {inicio: 100.0}}
        resultado = validacao.avaliar_horizonte(
            inicio, "EUA", [{"ticker": "AAA", "nota": 90}], precos, 5
        )
        self.assertEqual(resultado["status"], "pendente")

    def test_calcula_acoes_selecionadas_que_superam_a_media_do_mercado(self):
        inicio = date(2026, 1, 5)
        sessoes = [inicio + timedelta(days=dia) for dia in range(15)]
        sessoes = [dia for dia in sessoes if dia.weekday() < 5]
        precos = {
            "AAA": {dia: 100 + 2 * indice for indice, dia in enumerate(sessoes)},
            "BBB": {dia: 100 for dia in sessoes},
        }
        avaliacao = validacao.avaliar_horizonte(
            sessoes[0], "B3", [{"ticker": "AAA"}, {"ticker": "BBB"}], precos, 5
        )
        self.assertAlmostEqual(avaliacao["acertos_acoes_vs_mercado"], 0.5)
        self.assertAlmostEqual(validacao.resumo([avaliacao])["acerto_acao_vs_mercado"], 0.5)

    def test_diagnostico_ruido_usa_janelas_nao_sobrepostas(self):
        resultados = [
            {"status": "ok", "data": date(2026, 1, 1), "_indice_base": 0, "_indice_alvo": 5, "excesso": -0.01},
            {"status": "ok", "data": date(2026, 1, 2), "_indice_base": 2, "_indice_alvo": 7, "excesso": 0.40},
            {"status": "ok", "data": date(2026, 1, 8), "_indice_base": 5, "_indice_alvo": 10, "excesso": 0.01},
            {"status": "ok", "data": date(2026, 1, 15), "_indice_base": 10, "_indice_alvo": 15, "excesso": 0.03},
        ]
        diagnostico = validacao.diagnostico_ruido(resultados)
        self.assertEqual(diagnostico["janelas"], 3)
        self.assertEqual(diagnostico["ruido"], True)
        self.assertLess(diagnostico["ic_inferior"], 0)
        self.assertGreater(diagnostico["ic_superior"], 0)

    def test_diagnostico_com_menos_de_duas_janelas_e_inconclusivo(self):
        resultado = {"status": "ok", "data": date(2026, 1, 1),
                     "_indice_base": 0, "_indice_alvo": 5, "excesso": 0.1}
        diagnostico = validacao.diagnostico_ruido([resultado])
        self.assertEqual(diagnostico["janelas"], 1)
        self.assertIsNone(diagnostico["ic_inferior"])
        self.assertTrue(diagnostico["ruido"])

    def test_relatorio_exibe_acertos_e_quantidade_de_janelas_para_cada_horizonte(self):
        inicio = date(2026, 1, 5)
        sessoes = [inicio + timedelta(days=dia) for dia in range(42)]
        sessoes = [dia for dia in sessoes if dia.weekday() < 5]
        precos = {
            "AAA": {dia: 100 + indice for indice, dia in enumerate(sessoes)},
            "BBB": {dia: 100 for dia in sessoes},
        }
        watchlists = {
            sessoes[0]: {"B3": [{"ticker": "AAA", "nota": 90, "data_referencia": sessoes[0]}], "EUA": []},
            sessoes[5]: {"B3": [{"ticker": "AAA", "nota": 90, "data_referencia": sessoes[5]}], "EUA": []},
        }
        relatorio = validacao.gerar_relatorio(watchlists, {"B3": precos, "EUA": {}})
        self.assertIn("ações selecionadas que superaram a média do mercado", relatorio)
        self.assertIn("2 janelas não sobrepostas", relatorio)
        self.assertIn("1 janela não sobreposta", relatorio)
        self.assertIn("compatível com ruído", relatorio)

    def test_le_watchlist_markdown_por_mercado(self):
        conteudo = (
            "## B3 — referência: 2026-01-02\n\n"
            "| Ticker | Nota | Volume relativo | Retorno 5 pregões | Notícias 24h | Motivo |\n"
            "|---|---:|---:|---:|---:|---|\n"
            "| AAA | 90.0 | 2.0x | +5.0% | 1 | motivo |\n\n"
            "## EUA — referência: 2026-01-05\n\n"
            "| Ticker | Nota | Volume relativo | Retorno 5 pregões | Notícias 24h | Motivo |\n"
            "|---|---:|---:|---:|---:|---|\n"
            "| BBB | 80.0 | 1.5x | +2.0% | 2 | motivo |\n"
        )
        with tempfile.TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            (pasta / "watchlist_2026-01-05.md").write_text(conteudo, encoding="utf-8")
            resultado = validacao.ler_watchlists(pasta)
        self.assertEqual(resultado[date(2026, 1, 5)]["B3"][0]["ticker"], "AAA")
        self.assertEqual(resultado[date(2026, 1, 5)]["B3"][0]["data_referencia"], date(2026, 1, 2))
        self.assertEqual(resultado[date(2026, 1, 5)]["EUA"][0]["ticker"], "BBB")


if __name__ == "__main__":
    unittest.main()
