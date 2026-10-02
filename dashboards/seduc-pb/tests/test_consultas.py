import pytest

from consultas import (
    buscar_escolas,
    buscar_metricas_gerais,
    buscar_metricas_por_escola,
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
        "turma_id": "t1", "turma_nome": "Turma A",
        "nota_final": 900, "c1": 180, "c2": 180, "c3": 180, "c4": 180, "c5": 180,
        "faixa_classificacao": "Muito alto", "posicao_geral": 1,
        "data_correcao": "2026-09-30T10:00:00",
    },
    {
        "id_redacao": "r2", "id_aluno": "a2", "nome_aluno": "Aluno Dois",
        "escola_id": "e1", "escola_nome": "Escola Um",
        "turma_id": "t1", "turma_nome": "Turma A",
        "nota_final": 700, "c1": 140, "c2": 140, "c3": 140, "c4": 140, "c5": 140,
        "faixa_classificacao": "Adequado", "posicao_geral": 2,
        "data_correcao": "2026-09-30T10:05:00",
    },
    {
        "id_redacao": "r3", "id_aluno": "a3", "nome_aluno": "Aluno Tres",
        "escola_id": "e2", "escola_nome": "Escola Dois",
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
