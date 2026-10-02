def test_fixture_cria_tabelas_e_dados_esperados(pg_com_dados_sinteticos):
    conn, ids = pg_com_dados_sinteticos
    total_escolas = conn.execute("SELECT count(*) FROM schools").fetchone()[0]
    total_correcoes = conn.execute("SELECT count(*) FROM essay_corrections").fetchone()[0]
    total_aprovadas = conn.execute(
        "SELECT count(*) FROM essay_corrections WHERE status = 'APPROVED'"
    ).fetchone()[0]
    assert total_escolas == 1
    assert total_correcoes == 3
    assert total_aprovadas == 2
