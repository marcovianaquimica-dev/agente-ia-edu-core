from seed_dados_demo import gerar_redacoes_ficticias


def test_gerar_redacoes_ficticias_produz_a_quantidade_pedida():
    redacoes = gerar_redacoes_ficticias(50, seed=1)
    assert len(redacoes) == 50


def test_notas_e_faixas_sao_consistentes():
    redacoes = gerar_redacoes_ficticias(30, seed=2)
    for linha in redacoes:
        soma = linha["c1"] + linha["c2"] + linha["c3"] + linha["c4"] + linha["c5"]
        assert soma == linha["nota_final"]
        assert 0 <= linha["nota_final"] <= 1000


def test_distribui_entre_varias_gres_e_municipios():
    redacoes = gerar_redacoes_ficticias(200, seed=3)
    gres = {linha["gre_nome"] for linha in redacoes}
    municipios = {linha["municipio_nome"] for linha in redacoes}
    assert len(gres) >= 5
    assert len(municipios) >= 5


def test_ranking_populado_e_consistente():
    redacoes = gerar_redacoes_ficticias(40, seed=4)
    posicoes = sorted(linha["posicao_geral"] for linha in redacoes)
    assert posicoes[0] == 1
    assert posicoes[-1] <= 40
