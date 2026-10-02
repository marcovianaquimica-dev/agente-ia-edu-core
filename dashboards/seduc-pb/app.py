"""App web do dashboard SEDUC-PB - le exclusivamente do snapshot SQLite."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from auth import verificar_login
from consultas import (
    buscar_escolas,
    buscar_metricas_gerais,
    buscar_metricas_por_escola,
    buscar_ranking,
    buscar_redacoes_por_turma,
    buscar_turmas_por_escola,
)
from ia_synthesis import gerar_sintese_via_openai, obter_ou_gerar_sintese
from snapshot_db import get_connection

BASE_DIR = Path(__file__).resolve().parent
SECRET_KEY = os.environ.get("SEDUC_DASHBOARD_SECRET_KEY", "dev-secret-trocar-em-producao")

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def obter_snapshot():
    snapshot_path = Path(
        os.environ.get("SEDUC_DASHBOARD_SNAPSHOT_PATH", str(BASE_DIR / "snapshot.db"))
    )
    conn = get_connection(snapshot_path)
    try:
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
    sintese = obter_ou_gerar_sintese(conn, "geral", metricas, gerar_sintese_via_openai)
    return templates.TemplateResponse(
        request=request,
        name="visao_geral.html",
        context={"login": login, "metricas": metricas, "sintese": sintese},
    )


@app.get("/ranking", response_class=HTMLResponse)
def ranking(
    request: Request,
    login: str | None = Depends(usuario_logado),
    conn: sqlite3.Connection = Depends(obter_snapshot),
    escola_id: str | None = None,
    turma_id: str | None = None,
    ordenar_por: str = "nota_final",
):
    if login is None:
        return RedirectResponse("/login", status_code=303)
    linhas = buscar_ranking(conn, escola_id=escola_id, turma_id=turma_id, ordenar_por=ordenar_por)
    escolas = buscar_escolas(conn)
    return templates.TemplateResponse(
        request=request,
        name="ranking.html",
        context={"login": login, "linhas": linhas, "escolas": escolas, "ordenar_por": ordenar_por},
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
    metricas = buscar_metricas_por_escola(conn, escola_id)
    sintese = obter_ou_gerar_sintese(conn, f"escola:{escola_id}", metricas, gerar_sintese_via_openai)
    turmas = buscar_turmas_por_escola(conn, escola_id)
    return templates.TemplateResponse(
        request=request,
        name="escola.html",
        context={"login": login, "metricas": metricas, "turmas": turmas, "sintese": sintese},
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
    alunos = buscar_redacoes_por_turma(conn, turma_id)
    return templates.TemplateResponse(
        request=request, name="turma.html", context={"login": login, "alunos": alunos}
    )
