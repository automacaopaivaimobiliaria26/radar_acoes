"""Interface do coletor RSS existente no diretório do projeto."""

from atencao import calcular_razao, consulta_rss, ler_candidatas, main, normalizar, salvar_historico

__all__ = ["calcular_razao", "consulta_rss", "ler_candidatas", "normalizar", "salvar_historico"]

if __name__ == "__main__":
    raise SystemExit(main())
