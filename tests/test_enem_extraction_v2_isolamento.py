"""Guarda estrutural: a V2 e prototipo e NAO pode entrar em producao sem decisao.

O usuario autorizou construir e avaliar a V2, e NAO autorizou substituir a V1.
"Nao faca substituicao silenciosa" e uma frase que precisa de uma trava, nao de
uma promessa. Estes testes quebram no instante em que a substituicao acontecer.

A verificacao e por ARVORE SINTATICA, nao por busca de texto. Buscar a string
no arquivo encontraria a propria docstring que explica a regra - erro ja
cometido antes nesta base de codigo, quando um guarda contra
``os.environ.setdefault`` se auto-detectou na frase que o descrevia.
"""

from __future__ import annotations

import ast
from pathlib import Path

RAIZ_SRC = Path(__file__).resolve().parents[1] / "src" / "agente_ia_edu"
PACOTE_V2 = "enem_extraction_v2"
V1 = RAIZ_SRC / "services" / "ingestion_parser.py"


def _modulos_importados(caminho: Path) -> set[str]:
    try:
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
        return set()
    nomes: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes.update(a.name for a in no.names)
        elif isinstance(no, ast.ImportFrom):
            if no.module:
                nomes.add(no.module)
            if no.level:  # import relativo
                nomes.update(a.name for a in no.names)
    return nomes


def test_nenhum_modulo_de_producao_importa_a_v2():
    culpados = []
    for caminho in RAIZ_SRC.rglob("*.py"):
        if PACOTE_V2 in caminho.parts:
            continue
        if any(PACOTE_V2 in nome for nome in _modulos_importados(caminho)):
            culpados.append(str(caminho.relative_to(RAIZ_SRC)))
    assert culpados == [], (
        "A V2 e prototipo e nao foi autorizada a substituir a V1. "
        f"Modulos de producao que passaram a importa-la: {culpados}"
    )


def test_a_v1_continua_existindo_e_intacta_nos_seus_pontos_de_falha():
    """A V1 e o baseline da comparacao. Se ela mudar, a comparacao morre."""
    origem = V1.read_text(encoding="utf-8")
    arvore = ast.parse(origem)
    classes = {n.name for n in ast.walk(arvore) if isinstance(n, ast.ClassDef)}
    assert "PdfParser" in classes
    atribuicoes = {
        no.targets[0].id: no.value
        for no in ast.walk(arvore)
        if isinstance(no, ast.Assign) and len(no.targets) == 1
        and isinstance(no.targets[0], ast.Name)
    }
    for nome in ("QUESTION_PATTERN", "OPTION_PATTERN", "ANSWER_KEY_PATTERN"):
        assert nome in atribuicoes, f"{nome} sumiu da V1"


def test_a_v2_nao_abre_banco_nem_rede_nem_escreve_arquivo():
    proibidos = {
        "sqlalchemy", "asyncpg", "psycopg", "requests", "httpx", "urllib",
        "urllib.request", "openai", "anthropic", "boto3",
    }
    culpados: dict[str, set[str]] = {}
    for caminho in (RAIZ_SRC / "services" / PACOTE_V2).rglob("*.py"):
        achados = {m for m in _modulos_importados(caminho)
                   if m.split(".")[0] in {p.split(".")[0] for p in proibidos}}
        if achados:
            culpados[caminho.name] = achados
    assert culpados == {}, f"a V2 deveria ser pura: {culpados}"


def test_a_v2_nao_chama_open_para_escrita():
    """Nenhum ``open(..., 'w')`` nem ``Path.write_*`` dentro do pacote."""
    culpados = []
    for caminho in (RAIZ_SRC / "services" / PACOTE_V2).rglob("*.py"):
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if not isinstance(no, ast.Call):
                continue
            alvo = no.func
            if isinstance(alvo, ast.Attribute) and alvo.attr.startswith("write_"):
                culpados.append(f"{caminho.name}:{no.lineno} {alvo.attr}")
            if isinstance(alvo, ast.Name) and alvo.id == "open":
                modo = next((a.value for a in no.args[1:2]
                             if isinstance(a, ast.Constant)), "r")
                if "w" in str(modo) or "a" in str(modo):
                    culpados.append(f"{caminho.name}:{no.lineno} open({modo})")
    assert culpados == [], f"a V2 nao deve escrever em disco: {culpados}"
