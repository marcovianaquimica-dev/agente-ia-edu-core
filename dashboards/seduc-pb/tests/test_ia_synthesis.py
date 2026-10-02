from ia_synthesis import obter_ou_gerar_sintese
from snapshot_db import create_schema, get_connection


def test_sintese_so_regenera_quando_hash_muda(tmp_path):
    conn = get_connection(tmp_path / "snapshot.db")
    create_schema(conn)
    chamadas = []

    def gerador_falso(dados: dict) -> str:
        chamadas.append(dados)
        return f"sintese para {dados['total']}"

    texto_1 = obter_ou_gerar_sintese(conn, "geral", {"total": 10}, gerador_falso)
    texto_2 = obter_ou_gerar_sintese(conn, "geral", {"total": 10}, gerador_falso)
    texto_3 = obter_ou_gerar_sintese(conn, "geral", {"total": 20}, gerador_falso)

    assert texto_1 == "sintese para 10"
    assert texto_2 == "sintese para 10"
    assert texto_3 == "sintese para 20"
    assert len(chamadas) == 2
