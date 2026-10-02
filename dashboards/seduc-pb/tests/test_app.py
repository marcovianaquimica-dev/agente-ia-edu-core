import pytest
from fastapi.testclient import TestClient

from app import app
from auth import criar_usuario
from snapshot_db import create_schema, get_connection, insert_redacoes

REDACAO_EXEMPLO = {
    "id_redacao": "r1", "id_aluno": "a1", "nome_aluno": "Aluno Um",
    "escola_id": "e1", "escola_nome": "Escola Um",
    "turma_id": "t1", "turma_nome": "Turma A",
    "nota_final": 800, "c1": 160, "c2": 160, "c3": 160, "c4": 160, "c5": 160,
    "faixa_classificacao": "Alto", "posicao_geral": 1,
    "data_correcao": "2026-09-30T10:00:00",
}


@pytest.fixture
def snapshot_populado(tmp_path, monkeypatch):
    caminho = tmp_path / "snapshot.db"
    monkeypatch.setenv("SEDUC_DASHBOARD_SNAPSHOT_PATH", str(caminho))
    # Troca o provider real de sintese por um stub - os testes nao podem
    # depender de rede nem da API da OpenAI de verdade.
    monkeypatch.setattr("app.gerar_sintese_via_openai", lambda dados: "sintese de teste")
    conn = get_connection(caminho)
    create_schema(conn)
    criar_usuario(conn, "gestor", "senha-teste")
    insert_redacoes(conn, [REDACAO_EXEMPLO])
    conn.close()
    return caminho


def test_rota_protegida_redireciona_para_login_sem_sessao(snapshot_populado):
    cliente = TestClient(app)
    resposta = cliente.get("/", follow_redirects=False)
    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/login"


def test_login_invalido_mostra_erro(snapshot_populado):
    cliente = TestClient(app)
    resposta = cliente.post("/login", data={"login": "gestor", "senha": "errada"})
    assert resposta.status_code == 401
    assert "invalidos" in resposta.text


def test_login_valido_permite_acessar_visao_geral(snapshot_populado):
    cliente = TestClient(app)
    resposta_login = cliente.post(
        "/login", data={"login": "gestor", "senha": "senha-teste"}, follow_redirects=False
    )
    assert resposta_login.status_code == 303
    resposta = cliente.get("/")
    assert resposta.status_code == 200
    assert "Total de redacoes corrigidas: 1" in resposta.text
    assert "sintese de teste" in resposta.text


def test_ranking_lista_a_redacao_cadastrada(snapshot_populado):
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/ranking")
    assert resposta.status_code == 200
    assert "Aluno Um" in resposta.text
