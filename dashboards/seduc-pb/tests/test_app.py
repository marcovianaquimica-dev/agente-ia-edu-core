import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import app
from auth import criar_usuario
from snapshot_db import create_schema, get_connection, insert_redacoes

REDACAO_EXEMPLO = {
    "id_redacao": "r1", "id_aluno": "a1", "nome_aluno": "Aluno Um",
    "escola_id": "e1", "escola_nome": "Escola Um",
    "gre_nome": "1a GRE - Joao Pessoa", "municipio_nome": "Joao Pessoa",
    "turma_id": "t1", "turma_nome": "Turma A",
    "nota_final": 800, "c1": 160, "c2": 160, "c3": 160, "c4": 160, "c5": 160,
    "faixa_classificacao": "Alto", "posicao_geral": 1,
    "data_correcao": "2026-09-30T10:00:00",
}

REDACAO_ESCOLA_2 = {
    "id_redacao": "r2", "id_aluno": "a2", "nome_aluno": "Aluno Dois",
    "escola_id": "e2", "escola_nome": "Escola Dois",
    "gre_nome": "3a GRE - Campina Grande", "municipio_nome": "Campina Grande",
    "turma_id": "t2", "turma_nome": "Turma B",
    # mesmas metricas agregadas da escola 1 de proposito - e o cenario do
    # finding #9: duas escolas com numeros identicos nao podem receber a
    # mesma sintese so porque o hash dos dados agregados bateria igual.
    "nota_final": 800, "c1": 160, "c2": 160, "c3": 160, "c4": 160, "c5": 160,
    "faixa_classificacao": "Alto", "posicao_geral": 1,
    "data_correcao": "2026-09-30T11:00:00",
}


PLANO_ACAO_TESTE = {
    "ponto_forte": "forte de teste",
    "ponto_atencao": "atencao de teste",
    "recomendacao": "recomendacao de teste",
}


@pytest.fixture
def snapshot_populado(tmp_path, monkeypatch):
    caminho = tmp_path / "snapshot.db"
    monkeypatch.setenv("SEDUC_DASHBOARD_SNAPSHOT_PATH", str(caminho))
    # Troca o provider real de IA por um stub - os testes nao podem
    # depender de rede nem da API da OpenAI de verdade.
    monkeypatch.setattr("app.gerar_plano_acao_via_openai", lambda dados: dict(PLANO_ACAO_TESTE))
    conn = get_connection(caminho)
    create_schema(conn)
    criar_usuario(conn, "gestor", "senha-teste")
    insert_redacoes(conn, [REDACAO_EXEMPLO])
    conn.close()
    return caminho


@pytest.fixture
def snapshot_duas_escolas(tmp_path, monkeypatch):
    caminho = tmp_path / "snapshot.db"
    monkeypatch.setenv("SEDUC_DASHBOARD_SNAPSHOT_PATH", str(caminho))
    chamadas = []

    def gerador_stub(dados: dict) -> dict:
        chamadas.append(dados)
        identificador = (
            dados.get("escola_nome")
            or dados.get("gre_nome")
            or dados.get("municipio_nome")
            or "rede"
        )
        return {
            "ponto_forte": f"forte para {identificador}",
            "ponto_atencao": f"atencao para {identificador}",
            "recomendacao": f"recomendacao para {identificador}",
        }

    monkeypatch.setattr("app.gerar_plano_acao_via_openai", gerador_stub)
    conn = get_connection(caminho)
    create_schema(conn)
    criar_usuario(conn, "gestor", "senha-teste")
    insert_redacoes(conn, [REDACAO_EXEMPLO, REDACAO_ESCOLA_2])
    conn.close()
    return caminho, chamadas


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
    assert "Redacoes corrigidas" in resposta.text
    assert '<div class="kpi-value">1</div>' in resposta.text
    assert "forte de teste" in resposta.text
    assert "atencao de teste" in resposta.text
    assert "recomendacao de teste" in resposta.text


def test_ranking_lista_a_redacao_cadastrada(snapshot_populado):
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/ranking")
    assert resposta.status_code == 200
    assert "Aluno Um" in resposta.text


def test_ranking_filtra_por_gre(snapshot_duas_escolas):
    caminho, _ = snapshot_duas_escolas
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/ranking", params={"gre_nome": "3a GRE - Campina Grande"})
    assert resposta.status_code == 200
    assert "Aluno Dois" in resposta.text
    assert "Aluno Um" not in resposta.text


def test_tela_gres_lista_as_gres_agregadas(snapshot_duas_escolas):
    caminho, _ = snapshot_duas_escolas
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/gres")
    assert resposta.status_code == 200
    assert "1a GRE - Joao Pessoa" in resposta.text
    assert "3a GRE - Campina Grande" in resposta.text


def test_tela_municipios_lista_os_municipios_agregados(snapshot_duas_escolas):
    caminho, _ = snapshot_duas_escolas
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/municipios")
    assert resposta.status_code == 200
    assert "Joao Pessoa" in resposta.text
    assert "Campina Grande" in resposta.text


def test_gres_e_municipios_redirecionam_para_login_sem_sessao(snapshot_populado):
    cliente = TestClient(app)
    for rota in ("/gres", "/municipios"):
        resposta = cliente.get(rota, follow_redirects=False)
        assert resposta.status_code == 303
        assert resposta.headers["location"] == "/login"


# --- finding #1: sem secret key real nem modo dev, o app nao deve subir ---


def test_app_recusa_subir_sem_secret_key_e_sem_dev_flag():
    diretorio_app = Path(__file__).resolve().parent.parent
    ambiente = {
        chave: valor
        for chave, valor in os.environ.items()
        if chave not in ("SEDUC_DASHBOARD_SECRET_KEY", "SEDUC_DASHBOARD_DEV")
    }
    resultado = subprocess.run(
        [sys.executable, "-c", "import app"],
        cwd=str(diretorio_app),
        env=ambiente,
        capture_output=True,
        text=True,
    )
    assert resultado.returncode != 0
    assert "SEDUC_DASHBOARD_SECRET_KEY" in resultado.stderr


def test_app_sobe_com_dev_flag_mesmo_sem_secret_key():
    diretorio_app = Path(__file__).resolve().parent.parent
    ambiente = {
        chave: valor for chave, valor in os.environ.items() if chave != "SEDUC_DASHBOARD_SECRET_KEY"
    }
    ambiente["SEDUC_DASHBOARD_DEV"] = "1"
    resultado = subprocess.run(
        [sys.executable, "-c", "import app"],
        cwd=str(diretorio_app),
        env=ambiente,
        capture_output=True,
        text=True,
    )
    assert resultado.returncode == 0, resultado.stderr


def test_app_sobe_com_secret_key_real_mesmo_sem_dev_flag():
    diretorio_app = Path(__file__).resolve().parent.parent
    ambiente = {
        chave: valor for chave, valor in os.environ.items() if chave != "SEDUC_DASHBOARD_DEV"
    }
    ambiente["SEDUC_DASHBOARD_SECRET_KEY"] = "uma-chave-de-producao-qualquer"
    resultado = subprocess.run(
        [sys.executable, "-c", "import app"],
        cwd=str(diretorio_app),
        env=ambiente,
        capture_output=True,
        text=True,
    )
    assert resultado.returncode == 0, resultado.stderr


# --- finding #2: falha na IA nao pode derrubar a tela ---


def _gerador_com_falha(dados: dict) -> dict:
    raise RuntimeError("simulando falha da API (sem rede, quota, etc)")


def test_visao_geral_mostra_fallback_quando_ia_falha(snapshot_populado, monkeypatch):
    monkeypatch.setattr("app.gerar_plano_acao_via_openai", _gerador_com_falha)
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/")
    assert resposta.status_code == 200
    assert "indisponivel" in resposta.text.lower()


def test_escola_mostra_fallback_quando_ia_falha(snapshot_populado, monkeypatch):
    monkeypatch.setattr("app.gerar_plano_acao_via_openai", _gerador_com_falha)
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/escola/e1")
    assert resposta.status_code == 200
    assert "indisponivel" in resposta.text.lower()


def test_turma_mostra_fallback_quando_ia_falha(snapshot_populado, monkeypatch):
    monkeypatch.setattr("app.gerar_plano_acao_via_openai", _gerador_com_falha)
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/turma/t1")
    assert resposta.status_code == 200
    assert "indisponivel" in resposta.text.lower()


# --- finding #3: a tela de escola precisa ser alcancavel pela navegacao ---


def test_visao_geral_lista_escolas_com_link(snapshot_populado):
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/")
    assert resposta.status_code == 200
    assert 'href="/escola/e1"' in resposta.text


def test_ranking_linka_nome_da_escola_para_a_tela_da_escola(snapshot_populado):
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/ranking")
    assert resposta.status_code == 200
    assert 'href="/escola/e1"' in resposta.text


# --- finding #5: filtro de escola/turma no ranking ---


def test_ranking_mantem_escola_selecionada_apos_filtrar(snapshot_populado):
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/ranking?escola_id=e1")
    assert resposta.status_code == 200
    assert '<option value="e1" selected' in resposta.text


def test_ranking_popula_select_de_turma_quando_escola_selecionada(snapshot_populado):
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/ranking?escola_id=e1")
    assert resposta.status_code == 200
    assert '<select name="turma_id">' in resposta.text
    assert 'value="t1"' in resposta.text
    assert "Turma A" in resposta.text


def test_ranking_mantem_turma_selecionada_apos_filtrar(snapshot_populado):
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/ranking?escola_id=e1&turma_id=t1")
    assert resposta.status_code == 200
    assert '<option value="t1" selected' in resposta.text


# --- finding #6: id arbitrario na URL nao pode processar nem chamar a IA ---


def test_escola_inexistente_retorna_404(snapshot_populado):
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/escola/NAO-EXISTE")
    assert resposta.status_code == 404


def test_turma_inexistente_retorna_404(snapshot_populado):
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/turma/NAO-EXISTE")
    assert resposta.status_code == 404


def test_escola_inexistente_nao_chama_a_ia_nem_grava_plano(snapshot_populado, monkeypatch):
    chamadas = []

    def gerador(dados: dict) -> dict:
        chamadas.append(dados)
        return dict(PLANO_ACAO_TESTE)

    monkeypatch.setattr("app.gerar_plano_acao_via_openai", gerador)
    cliente = TestClient(app)
    # follow_redirects=False: um POST /login bem-sucedido redireciona para
    # "/", que por si so chamaria o plano de acao "geral" - isso
    # contaminaria a contagem de chamadas que este teste quer isolar na
    # tela de escola.
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"}, follow_redirects=False)
    resposta = cliente.get("/escola/NAO-EXISTE")
    assert resposta.status_code == 404
    assert chamadas == []

    conn = get_connection(snapshot_populado)
    total_sinteses = conn.execute("SELECT count(*) FROM sinteses").fetchone()[0]
    conn.close()
    assert total_sinteses == 0


# --- finding #7: media por turma e percentual por faixa aparecem nas telas ---


def test_escola_mostra_media_por_turma(snapshot_populado):
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/escola/e1")
    assert resposta.status_code == 200
    assert "800.0" in resposta.text


def test_visao_geral_mostra_percentual_por_faixa(snapshot_populado):
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/")
    assert resposta.status_code == 200
    assert "100.0%" in resposta.text


# --- finding #8: snapshot sem schema nao pode dar um 500 cru ---


def test_obter_snapshot_cria_schema_automaticamente_se_ausente(tmp_path, monkeypatch):
    caminho = tmp_path / "snapshot_nunca_exportado.db"
    monkeypatch.setenv("SEDUC_DASHBOARD_SNAPSHOT_PATH", str(caminho))
    cliente = TestClient(app)
    # antes do fix isso seria um sqlite3.OperationalError: no such table
    # (500 cru); depois do fix, create_schema garante a tabela e o login
    # simplesmente falha porque nao existe usuario nenhum (401, tratado).
    resposta = cliente.post("/login", data={"login": "ninguem", "senha": "x"})
    assert resposta.status_code == 401


# --- finding #9: o plano de acao da escola leva o nome dela, nao so numeros ---


def test_escolas_com_metricas_identicas_recebem_plano_de_acao_diferente(snapshot_duas_escolas):
    caminho, chamadas = snapshot_duas_escolas
    cliente = TestClient(app)
    # follow_redirects=False pelo mesmo motivo do teste acima: isolar as
    # chamadas de IA nas duas telas de escola, sem a chamada "geral" que o
    # redirect do login para "/" dispararia.
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"}, follow_redirects=False)

    resposta_e1 = cliente.get("/escola/e1")
    resposta_e2 = cliente.get("/escola/e2")

    assert resposta_e1.status_code == 200
    assert resposta_e2.status_code == 200
    assert "forte para Escola Um" in resposta_e1.text
    assert "forte para Escola Dois" in resposta_e2.text
    assert {chamada.get("escola_nome") for chamada in chamadas} == {"Escola Um", "Escola Dois"}


# --- graficos (Chart.js): o servidor so precisa embutir os dados certos no
# HTML - a renderizacao em si roda no navegador, fora do alcance do pytest.


def test_visao_geral_embute_dados_do_grafico_de_distribuicao(snapshot_populado):
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/")
    assert resposta.status_code == 200
    assert 'id="grafico-distribuicao"' in resposta.text
    assert '"labels": ["Alto"]' in resposta.text or '"Alto"' in resposta.text
    assert '"quantidade": [1]' in resposta.text


def test_visao_geral_embute_dados_do_grafico_de_competencia(snapshot_populado):
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/")
    assert resposta.status_code == 200
    assert 'id="grafico-competencia"' in resposta.text
    assert '"valores": [160.0, 160.0, 160.0, 160.0, 160.0]' in resposta.text


def test_escola_embute_grafico_de_comparacao_com_4_series(snapshot_duas_escolas):
    caminho, _ = snapshot_duas_escolas
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/escola/e1")
    assert resposta.status_code == 200
    assert 'id="grafico-comparacao-escola"' in resposta.text
    nomes_series = {"Escola Um", "Joao Pessoa", "1a GRE - Joao Pessoa", "Estado (Paraiba)"}
    for nome in nomes_series:
        assert nome in resposta.text


def test_turma_embute_grafico_de_comparacao_com_3_series(snapshot_duas_escolas):
    caminho, _ = snapshot_duas_escolas
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/turma/t1")
    assert resposta.status_code == 200
    assert 'id="grafico-comparacao-turma"' in resposta.text
    assert '"nome": "Turma"' in resposta.text
    assert '"nome": "Escola Um"' in resposta.text
    assert '"nome": "Estado (Paraiba)"' in resposta.text


def test_gres_embute_grafico_de_barras(snapshot_duas_escolas):
    caminho, _ = snapshot_duas_escolas
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/gres")
    assert resposta.status_code == 200
    assert 'id="grafico-gres"' in resposta.text
    assert "1a GRE - Joao Pessoa" in resposta.text
    assert "3a GRE - Campina Grande" in resposta.text


def test_municipios_embute_grafico_de_barras(snapshot_duas_escolas):
    caminho, _ = snapshot_duas_escolas
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/municipios")
    assert resposta.status_code == 200
    assert 'id="grafico-municipios"' in resposta.text
    assert "Joao Pessoa" in resposta.text
    assert "Campina Grande" in resposta.text


# --- plano de acao: Turma (novo) e telas de detalhe de GRE/Municipio (novas) ---


def test_turma_mostra_plano_de_acao(snapshot_populado):
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/turma/t1")
    assert resposta.status_code == 200
    assert "Plano de acao" in resposta.text
    assert "forte de teste" in resposta.text


def test_gres_lista_linka_para_tela_de_detalhe(snapshot_duas_escolas):
    caminho, _ = snapshot_duas_escolas
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/gres")
    assert resposta.status_code == 200
    assert 'href="/gres/1a%20GRE%20-%20Joao%20Pessoa"' in resposta.text


def test_municipios_lista_linka_para_tela_de_detalhe(snapshot_duas_escolas):
    caminho, _ = snapshot_duas_escolas
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/municipios")
    assert resposta.status_code == 200
    assert 'href="/municipios/Joao%20Pessoa"' in resposta.text


def test_gre_detalhe_mostra_metricas_grafico_e_plano_de_acao(snapshot_duas_escolas):
    caminho, _ = snapshot_duas_escolas
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/gres/1a GRE - Joao Pessoa")
    assert resposta.status_code == 200
    assert "1a GRE - Joao Pessoa" in resposta.text
    assert 'id="grafico-comparacao-gre"' in resposta.text
    assert "Estado (Paraiba)" in resposta.text
    assert "forte para 1a GRE - Joao Pessoa" in resposta.text


def test_municipio_detalhe_mostra_metricas_grafico_e_plano_de_acao(snapshot_duas_escolas):
    caminho, _ = snapshot_duas_escolas
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/municipios/Joao Pessoa")
    assert resposta.status_code == 200
    assert "Joao Pessoa" in resposta.text
    assert 'id="grafico-comparacao-municipio"' in resposta.text
    assert "forte para Joao Pessoa" in resposta.text


def test_gre_detalhe_inexistente_retorna_404(snapshot_populado):
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/gres/NAO-EXISTE")
    assert resposta.status_code == 404


def test_municipio_detalhe_inexistente_retorna_404(snapshot_populado):
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/municipios/NAO-EXISTE")
    assert resposta.status_code == 404


def test_gres_e_municipios_detalhe_redirecionam_para_login_sem_sessao(snapshot_populado):
    cliente = TestClient(app)
    for rota in ("/gres/1a GRE - Joao Pessoa", "/municipios/Joao Pessoa"):
        resposta = cliente.get(rota, follow_redirects=False)
        assert resposta.status_code == 303
        assert resposta.headers["location"] == "/login"
