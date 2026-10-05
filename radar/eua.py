"""Interface do módulo EUA existente no diretório do projeto."""

from eua import baixar_lote, coletar, extrair_coluna, ler_lista, main, simbolos_comuns

__all__ = ["baixar_lote", "coletar", "extrair_coluna", "ler_lista", "simbolos_comuns"]

if __name__ == "__main__":
    raise SystemExit(main())
