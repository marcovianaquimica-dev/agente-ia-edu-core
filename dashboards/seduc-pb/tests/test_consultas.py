import pytest

from consultas import (
    buscar_escola_da_turma,
    buscar_escolas,
    buscar_gre_e_municipio_da_escola,
    buscar_gres,
    buscar_medias_competencia_geral,
    buscar_medias_competencia_por_escola,
    buscar_medias_competencia_por_gre,
    buscar_medias_competencia_por_municipio,
    buscar_medias_competencia_por_turma,
    buscar_metricas_gerais,
    buscar_metricas_por_escola,
    buscar_municipios,
    buscar_nome_escola,
    buscar_ranking,
    buscar_redacoes_por_turma,
    buscar_turmas_por_escola,
    escola_existe,
    turma_existe,
)
from snapshot_db import create_schema, get_connection, insert_redacoes

REDACOES = [
    {
        "id_redacao": "r1", "id_aluno": "a1", "nome_aluno": "Aluno Um",
        "escola_id": "e1", "escola_nome": "Escola Um",
        "gre_nome": "1a GRE - Joao Pessoa", "municipio_nome": "Joao Pessoa",
        "turma_id": "t1", "turma_nome": "Turma A",
        "nota_final": 900, "c1": 180, "c2": 180, "c3": 180, "c4": 180, "c5": 180,
        "faixa_classificacao": "Muito alto", "posicao_geral": 1,
        "data_correcao": "2026-09-30T10:00:00",
    },
    {
        "id_redacao": "r2", "id_aluno": "a2", "nome_aluno": "Aluno Dois",
        "escola_id": "e1", "escola_nome": "Escola Um",
        "gre_nome": "1a GRE - Joao Pessoa", "municipio_nome": "Joao Pessoa",
        "turma_id": "t1", "turma_nome": "Turma A",
        "nota_final": 700, "c1": 140, "c2": 140, "c3": 140, "c4": 140, "c5": 140,
        "faixa_classificacao": "Adequado", "posicao_geral": 2,
        "data_correcao": "2026-09-30T10:05:00",
    },
    {
        "id_redacao": "r3", "id_aluno": "a3", "nome_aluno": "Aluno Tres",
        "escola_id": "e2", "escola_nome": "Escola Dois",
        "gre_nome": "3a GRE - Campina Grande", "municipio_nome": "Campina Grande",
        "turma_id": None, "turma_nome": None,
        "nota_final": 500, "c1": 100, "c2": 100, "c3": 100, "c4": 100, "c5": 100,
        "faixa_classificacao": "Baixo", "posicao_geral": 3,
        "data_correcao": "2026-09-30T10:10:00",
    },
]


@pytest.fixture
def conn(tmp_path):
    conexao = get_connection(tmp_path / "snapshot.db")
    create_schema(conexao)
    insert_redacoes(conexao, REDACOES)
    return conexao


def test_buscar_metricas_gerais(conn):
    metricas = buscar_metricas_gerais(conn)
    assert metricas["total"] == 3
    assert metricas["media"] == pytest.approx(700.0, abs=0.1)
    assert metricas["mediana"] == 700
    assert metricas["distribuicao_faixas"] == {
        "Muito alto": 1, "Adequado": 1, "Baixo": 1,
    }
    assert metricas["distribuicao_faixas_pct"] == {
        "Muito alto": pytest.approx(33.3, abs=0.1),
        "Adequado": pytest.approx(33.3, abs=0.1),
        "Baixo": pytest.approx(33.3, abs=0.1),
    }


def test_mediana_e_sempre_float_seja_par_ou_impar_a_quantidade(conn):
    # Quantidade impar (3 redacoes): mediana vem direto do sqlite (int).
    metricas_impar = buscar_metricas_gerais(conn)
    assert isinstance(metricas_impar["mediana"], float)
    assert metricas_impar["mediana"] == 700.0

    # Quantidade par (2 redacoes de uma mesma escola): mediana vem de uma
    # media de dois valores (ja era float antes da normalizacao).
    metricas_par = buscar_metricas_por_escola(conn, "e1")
    assert isinstance(metricas_par["mediana"], float)
    assert metricas_par["mediana"] == pytest.approx(800.0, abs=0.1)


def test_buscar_metricas_por_escola(conn):
    metricas = buscar_metricas_por_escola(conn, "e1")
    assert metricas["total"] == 2
    assert metricas["media"] == pytest.approx(800.0, abs=0.1)


def test_buscar_escolas(conn):
    escolas = buscar_escolas(conn)
    assert escolas == [
        {"escola_id": "e2", "escola_nome": "Escola Dois"},
        {"escola_id": "e1", "escola_nome": "Escola Um"},
    ]


def test_buscar_turmas_por_escola(conn):
    turmas = buscar_turmas_por_escola(conn, "e1")
    assert turmas == [{"turma_id": "t1", "turma_nome": "Turma A", "media_nota": 800.0}]


def test_buscar_ranking_filtra_por_escola_e_ordena(conn):
    linhas = buscar_ranking(conn, escola_id="e1", ordenar_por="nota_final")
    assert [linha["nome_aluno"] for linha in linhas] == ["Aluno Um", "Aluno Dois"]


def test_buscar_ranking_inclui_escola_id_para_permitir_link_na_tela(conn):
    linhas = buscar_ranking(conn)
    assert all("escola_id" in linha for linha in linhas)
    primeira = next(linha for linha in linhas if linha["nome_aluno"] == "Aluno Um")
    assert primeira["escola_id"] == "e1"


def test_buscar_ranking_filtra_por_gre(conn):
    linhas = buscar_ranking(conn, gre_nome="3a GRE - Campina Grande")
    assert [linha["nome_aluno"] for linha in linhas] == ["Aluno Tres"]


def test_buscar_ranking_filtra_por_municipio(conn):
    linhas = buscar_ranking(conn, municipio_nome="Joao Pessoa")
    assert [linha["nome_aluno"] for linha in linhas] == ["Aluno Um", "Aluno Dois"]


def test_buscar_gres_agrega_por_gre_ordenado_por_media_desc(conn):
    linhas = buscar_gres(conn)
    assert [linha["nome"] for linha in linhas] == [
        "1a GRE - Joao Pessoa", "3a GRE - Campina Grande",
    ]
    joao_pessoa = linhas[0]
    assert joao_pessoa["total"] == 2
    assert joao_pessoa["media"] == pytest.approx(800.0, abs=0.1)
    assert joao_pessoa["media_c1"] == pytest.approx(160.0, abs=0.1)
    assert joao_pessoa["percentual_800"] == pytest.approx(50.0, abs=0.1)


def test_buscar_municipios_agrega_por_municipio(conn):
    linhas = buscar_municipios(conn)
    assert [linha["nome"] for linha in linhas] == ["Joao Pessoa", "Campina Grande"]
    campina_grande = linhas[1]
    assert campina_grande["total"] == 1
    assert campina_grande["media"] == pytest.approx(500.0, abs=0.1)
    assert campina_grande["percentual_800"] == pytest.approx(0.0, abs=0.1)


def test_buscar_redacoes_por_turma(conn):
    alunos = buscar_redacoes_por_turma(conn, "t1")
    assert [a["nome_aluno"] for a in alunos] == ["Aluno Um", "Aluno Dois"]


def test_escola_existe(conn):
    assert escola_existe(conn, "e1") is True
    assert escola_existe(conn, "nao-existe") is False


def test_turma_existe(conn):
    assert turma_existe(conn, "t1") is True
    assert turma_existe(conn, "nao-existe") is False


def test_buscar_nome_escola(conn):
    assert buscar_nome_escola(conn, "e1") == "Escola Um"
    assert buscar_nome_escola(conn, "nao-existe") is None


def test_buscar_medias_competencia_geral(conn):
    medias = buscar_medias_competencia_geral(conn)
    assert medias == {
        "c1": pytest.approx(140.0, abs=0.1),
        "c2": pytest.approx(140.0, abs=0.1),
        "c3": pytest.approx(140.0, abs=0.1),
        "c4": pytest.approx(140.0, abs=0.1),
        "c5": pytest.approx(140.0, abs=0.1),
    }


def test_buscar_medias_competencia_por_escola(conn):
    medias = buscar_medias_competencia_por_escola(conn, "e1")
    assert medias["c1"] == pytest.approx(160.0, abs=0.1)
    assert medias["c5"] == pytest.approx(160.0, abs=0.1)


def test_buscar_medias_competencia_por_turma(conn):
    medias = buscar_medias_competencia_por_turma(conn, "t1")
    assert medias["c1"] == pytest.approx(160.0, abs=0.1)


def test_buscar_medias_competencia_por_gre_e_municipio(conn):
    medias_gre = buscar_medias_competencia_por_gre(conn, "1a GRE - Joao Pessoa")
    medias_municipio = buscar_medias_competencia_por_municipio(conn, "Joao Pessoa")
    assert medias_gre["c1"] == pytest.approx(160.0, abs=0.1)
    assert medias_municipio["c1"] == pytest.approx(160.0, abs=0.1)


def test_buscar_medias_competencia_sem_dados_retorna_zeros(conn):
    medias = buscar_medias_competencia_por_escola(conn, "escola-sem-redacao")
    assert medias == {"c1": 0, "c2": 0, "c3": 0, "c4": 0, "c5": 0}


def test_buscar_gre_e_municipio_da_escola(conn):
    assert buscar_gre_e_municipio_da_escola(conn, "e1") == ("1a GRE - Joao Pessoa", "Joao Pessoa")


def test_buscar_escola_da_turma(conn):
    assert buscar_escola_da_turma(conn, "t1") == ("e1", "Escola Um")
