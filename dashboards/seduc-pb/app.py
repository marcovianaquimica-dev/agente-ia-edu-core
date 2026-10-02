"""App web do dashboard SEDUC-PB - le exclusivamente do snapshot SQLite."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from auth import verificar_login
from consultas import (
    buscar_escolas,
    buscar_gres,
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
from ia_synthesis import gerar_sintese_via_openai, obter_ou_gerar_sintese
from snapshot_db import create_schema, get_connection

BASE_DIR = Path(__file__).resolve().parent

SINTESE_INDISPONIVEL = "Sintese executiva indisponivel no momento."

_secret_key_env = os.environ.get("SEDUC_DASHBOARD_SECRET_KEY")
if _secret_key_env:
    SECRET_KEY = _secret_key_env
elif os.environ.get("SEDUC_DASHBOARD_DEV") == "1":
    SECRET_KEY = "dev-secret-trocar-em-producao"
else:
    raise RuntimeError(
        "SEDUC_DASHBOARD_SECRET_KEY nao esta definida. O app recusa subir "
        "sem uma chave de sessao real, para nao deixar o login forjavel. "
        "Defina SEDUC_DASHBOARD_SECRET_KEY com uma chave aleatoria de "
        "producao, ou SEDUC_DASHBOARD_DEV=1 para rodar localmente em modo "
        "de desenvolvimento (nunca em producao)."
    )

app = FastAPI()
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    max_age=60 * 60 * 8,
    same_site="lax",
)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def obter_snapshot():
    snapshot_path = Path(
        os.environ.get("SEDUC_DASHBOARD_SNAPSHOT_PATH", str(BASE_DIR / "snapshot.db"))
    )
    conn = get_connection(snapshot_path)
    try:
        # Idempotente (CREATE TABLE IF NOT EXISTS) - evita um 500 cru quando
        # o arquivo de snapshot existe mas nunca foi exportado (sem tabelas).
        create_schema(conn)
        yield conn
    finally:
        conn.close()


def usuario_logado(request: Request) -> str | None:
    return request.session.get("login")


@app.get("/login", response_class=HTMLResponse)
def tela_login(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={"erro": None})


@app.post("/login")
def processar_login(
    request: Request,
    login: str = Form(...),
    senha: str = Form(...),
    conn: sqlite3.Connection = Depends(obter_snapshot),
):
    if verificar_login(conn, login, senha):
        request.session["login"] = login
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"erro": "Login ou senha invalidos"},
        status_code=401,
    )


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def visao_geral(
    request: Request,
    login: str | None = Depends(usuario_logado),
    conn: sqlite3.Connection = Depends(obter_snapshot),
):
    if login is None:
        return RedirectResponse("/login", status_code=303)
    metricas = buscar_metricas_gerais(conn)
    try:
        sintese = obter_ou_gerar_sintese(conn, "geral", metricas, gerar_sintese_via_openai)
    except Exception:
        sintese = SINTESE_INDISPONIVEL
    escolas = buscar_escolas(conn)
    return templates.TemplateResponse(
        request=request,
        name="visao_geral.html",
        context={"login": login, "metricas": metricas, "sintese": sintese, "escolas": escolas},
    )


@app.get("/ranking", response_class=HTMLResponse)
def ranking(
    request: Request,
    login: str | None = Depends(usuario_logado),
    conn: sqlite3.Connection = Depends(obter_snapshot),
    escola_id: str | None = None,
    turma_id: str | None = None,
    gre_nome: str | None = None,
    municipio_nome: str | None = None,
    ordenar_por: str = "nota_final",
):
    if login is None:
        return RedirectResponse("/login", status_code=303)
    linhas = buscar_ranking(
        conn,
        escola_id=escola_id,
        turma_id=turma_id,
        gre_nome=gre_nome,
        municipio_nome=municipio_nome,
        ordenar_por=ordenar_por,
    )
    escolas = buscar_escolas(conn)
    turmas = buscar_turmas_por_escola(conn, escola_id) if escola_id else []
    gres = buscar_gres(conn)
    municipios = buscar_municipios(conn)
    return templates.TemplateResponse(
        request=request,
        name="ranking.html",
        context={
            "login": login,
            "linhas": linhas,
            "escolas": escolas,
            "turmas": turmas,
            "gres": gres,
            "municipios": municipios,
            "ordenar_por": ordenar_por,
            "escola_id": escola_id,
            "turma_id": turma_id,
            "gre_nome": gre_nome,
            "municipio_nome": municipio_nome,
        },
    )


@app.get("/gres", response_class=HTMLResponse)
def gres(
    request: Request,
    login: str | None = Depends(usuario_logado),
    conn: sqlite3.Connection = Depends(obter_snapshot),
):
    if login is None:
        return RedirectResponse("/login", status_code=303)
    linhas = buscar_gres(conn)
    return templates.TemplateResponse(
        request=request, name="gres.html", context={"login": login, "linhas": linhas}
    )


@app.get("/municipios", response_class=HTMLResponse)
def municipios(
    request: Request,
    login: str | None = Depends(usuario_logado),
    conn: sqlite3.Connection = Depends(obter_snapshot),
):
    if login is None:
        return RedirectResponse("/login", status_code=303)
    linhas = buscar_municipios(conn)
    return templates.TemplateResponse(
        request=request, name="municipios.html", context={"login": login, "linhas": linhas}
    )


@app.get("/escola/{escola_id}", response_class=HTMLResponse)
def escola(
    escola_id: str,
    request: Request,
    login: str | None = Depends(usuario_logado),
    conn: sqlite3.Connection = Depends(obter_snapshot),
):
    if login is None:
        return RedirectResponse("/login", status_code=303)
    if not escola_existe(conn, escola_id):
        raise HTTPException(status_code=404, detail="Escola nao encontrada")
    metricas = buscar_metricas_por_escola(conn, escola_id)
    escola_nome = buscar_nome_escola(conn, escola_id)
    turmas = buscar_turmas_por_escola(conn, escola_id)
    dados_sintese = {**metricas, "escola_nome": escola_nome}
    try:
        sintese = obter_ou_gerar_sintese(
            conn, f"escola:{escola_id}", dados_sintese, gerar_sintese_via_openai
        )
    except Exception:
        sintese = SINTESE_INDISPONIVEL
    return templates.TemplateResponse(
        request=request,
        name="escola.html",
        context={
            "login": login,
            "metricas": metricas,
            "turmas": turmas,
            "sintese": sintese,
            "escola_nome": escola_nome,
        },
    )


@app.get("/turma/{turma_id}", response_class=HTMLResponse)
def turma(
    turma_id: str,
    request: Request,
    login: str | None = Depends(usuario_logado),
    conn: sqlite3.Connection = Depends(obter_snapshot),
):
    if login is None:
        return RedirectResponse("/login", status_code=303)
    if not turma_existe(conn, turma_id):
        raise HTTPException(status_code=404, detail="Turma nao encontrada")
    alunos = buscar_redacoes_por_turma(conn, turma_id)
    return templates.TemplateResponse(
        request=request, name="turma.html", context={"login": login, "alunos": alunos}
    )
