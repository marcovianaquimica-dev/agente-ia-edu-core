from gre_municipio import (
    NAO_CLASSIFICADA_GRE,
    NAO_CLASSIFICADA_MUNICIPIO,
    gre_de,
    municipio_de,
)


def test_escola_conhecida_retorna_gre_e_municipio_reais():
    assert gre_de("6f26cd3c-63d5-4509-a041-13714f75e53e") == "1a GRE - Joao Pessoa"
    assert municipio_de("6f26cd3c-63d5-4509-a041-13714f75e53e") == "Joao Pessoa"


def test_escola_desconhecida_retorna_fallback_nao_classificada():
    assert gre_de("id-qualquer-nao-mapeado") == NAO_CLASSIFICADA_GRE
    assert municipio_de("id-qualquer-nao-mapeado") == NAO_CLASSIFICADA_MUNICIPIO
