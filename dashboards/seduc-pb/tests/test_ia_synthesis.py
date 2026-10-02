import pytest

from ia_synthesis import _validar_plano_acao, obter_ou_gerar_plano_acao
from snapshot_db import create_schema, get_connection


def test_plano_de_acao_so_regenera_quando_hash_muda(tmp_path):
    conn = get_connection(tmp_path / "snapshot.db")
    create_schema(conn)
    chamadas = []

    def gerador_falso(dados: dict) -> dict:
        chamadas.append(dados)
        return {
            "ponto_forte": f"forte para {dados['total']}",
            "ponto_atencao": "atencao",
            "recomendacao": "recomendacao",
        }

    plano_1 = obter_ou_gerar_plano_acao(conn, "geral", {"total": 10}, gerador_falso)
    plano_2 = obter_ou_gerar_plano_acao(conn, "geral", {"total": 10}, gerador_falso)
    plano_3 = obter_ou_gerar_plano_acao(conn, "geral", {"total": 20}, gerador_falso)

    assert plano_1 == {
        "ponto_forte": "forte para 10", "ponto_atencao": "atencao", "recomendacao": "recomendacao",
    }
    assert plano_2 == plano_1
    assert plano_3["ponto_forte"] == "forte para 20"
    assert len(chamadas) == 2


def test_cortes_diferentes_tem_planos_de_acao_independentes(tmp_path):
    conn = get_connection(tmp_path / "snapshot.db")
    create_schema(conn)

    def gerador(dados: dict) -> dict:
        return {"ponto_forte": "x", "ponto_atencao": "y", "recomendacao": "z"}

    obter_ou_gerar_plano_acao(conn, "geral", {"total": 10}, gerador)
    obter_ou_gerar_plano_acao(conn, "escola:e1", {"total": 10}, gerador)

    total_linhas = conn.execute("SELECT count(*) FROM sinteses").fetchone()[0]
    assert total_linhas == 2


def test_validar_plano_acao_aceita_as_3_chaves_esperadas():
    bruto = {"ponto_forte": "a", "ponto_atencao": "b", "recomendacao": "c", "extra": "ignorado"}
    assert _validar_plano_acao(bruto) == {"ponto_forte": "a", "ponto_atencao": "b", "recomendacao": "c"}


def test_validar_plano_acao_rejeita_resposta_sem_as_3_chaves():
    with pytest.raises(ValueError):
        _validar_plano_acao({"ponto_forte": "a", "ponto_atencao": "b"})
