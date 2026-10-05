"""Os modulos do ecossistema Nucleo Edu 360, e quem pode entrar em cada um.

POR QUE ISTO E UM CATALOGO NO CODIGO, E NAO UMA TABELA
=======================================================
A infraestrutura de CONTRATACAO ja existe: `school_modules` guarda o que cada
escola tem habilitado, e `AuthorizationService.resolve_context` devolve isso
em `context.modules`. Nada disso precisa ser recriado.

O que nao existia era o CATALOGO - a lista do que o produto tem, com titulo,
descricao, rota e papeis. Isso e informacao sobre o PRODUTO, nao sobre uma
escola: muda quando o Nucleo lanca um modulo, nao quando um cliente assina.
Codigo versionado e o lugar certo, e evita uma migration para a V1.

    school_modules   o que a ESCOLA contratou        (banco, ja existia)
    MODULOS          o que o PRODUTO tem             (aqui)
    roles            quem pode entrar                (aqui)

QUANDO UM MODULO FUTURO FICAR PRONTO
=====================================
Troca-se `status` de EM_BREVE para DISPONIVEL e preenche-se `route`. A Home
nao muda. Se o modulo tambem passar a ser contratavel, a chave entra na
CHECK de `school_modules` por migration - mas so entao, e por um motivo real.

O FRONTEND NAO E A AUTORIDADE
==============================
`can_access` existe para a Home nao oferecer uma porta que vai bater na cara
da pessoa. As rotas de cada modulo continuam com os seus proprios guards: um
card escondido nao protege nada, e nunca foi essa a intencao.
"""

from __future__ import annotations

DISPONIVEL = "DISPONIVEL"
EM_BREVE = "EM_BREVE"

# Papeis que o projeto reconhece (ver UserSchoolLink).
_ALUNO = ("STUDENT",)
_EQUIPE = ("TEACHER", "COORDINATOR", "DIRECTOR")
_TODOS_DA_ESCOLA = _ALUNO + _EQUIPE


MODULOS: list[dict] = [
    {
        "key": "REDACAO_IA",
        "title": "Redação",
        "description": "Correção e acompanhamento da produção textual.",
        "status": DISPONIVEL,
        "route": "/redacao",
        "roles": _TODOS_DA_ESCOLA,
        "icon": "redacao",
    },
    {
        "key": "AGENTE_IA_EDU",
        "title": "Assessor Pedagógico",
        "description": "Diagnóstico, aprendizagem guiada e acompanhamento "
                       "adaptativo.",
        "status": DISPONIVEL,
        "route": "/student/aluno.html",
        "roles": _TODOS_DA_ESCOLA,
        "icon": "assessor",
    },
    # -- o que ainda nao existe --------------------------------------------
    #
    # As descricoes sao deliberadamente curtas e genericas. Nenhuma delas
    # promete dashboard, relatorio, grafico ou indicador: nada disso foi
    # construido, e escrever na Home o que nao existe e o jeito mais rapido
    # de transformar uma demonstracao numa divida.
    {
        "key": "SAEB",
        "title": "SAEB",
        "description": "Preparação e acompanhamento para a avaliação externa.",
        "status": EM_BREVE,
        "route": None,
        "roles": _EQUIPE,
        "icon": "saeb",
    },
    {
        "key": "ACADEMICO",
        "title": "Sistema Acadêmico",
        "description": "Secretaria, matrículas e a vida escolar do aluno.",
        "status": EM_BREVE,
        "route": None,
        "roles": _EQUIPE,
        "icon": "academico",
    },
    {
        "key": "FORMACAO",
        "title": "Formação de Professores",
        "description": "Trilhas de formação continuada para a equipe docente.",
        "status": EM_BREVE,
        "route": None,
        "roles": _EQUIPE,
        "icon": "formacao",
    },
    {
        "key": "SIMULADOS",
        "title": "Simulados",
        "description": "Aplicação de simulados e leitura dos resultados.",
        "status": EM_BREVE,
        "route": None,
        "roles": _TODOS_DA_ESCOLA,
        "icon": "simulados",
    },
]


def modulos_para(*, role: str | None, contratados, is_platform_admin: bool) -> list[dict]:
    """O ecossistema inteiro, com o que esta acessivel PARA ESTA PESSOA.

    Os seis sempre aparecem - e isso e o recado da Home: o Nucleo Edu 360 nao
    e um produto, e uma plataforma. O que muda e o estado de cada um.

        can_access   da para entrar agora
        contracted   a escola tem este modulo habilitado
        status       o modulo existe (DISPONIVEL) ou ainda nao (EM_BREVE)

    "Em breve" e "sua escola nao contratou" sao coisas DIFERENTES, e a Home
    precisa poder dizer cada uma: confundi-las faria o Portal mentir sobre o
    produto para quem esta avaliando comprar.
    """
    habilitados = set(contratados or ())
    papel = (role or "").upper()
    saida = []
    for m in MODULOS:
        existe = m["status"] == DISPONIVEL
        contratado = existe and m["key"] in habilitados
        pode_o_papel = is_platform_admin or papel in m["roles"]
        saida.append({
            "key": m["key"],
            "title": m["title"],
            "description": m["description"],
            "status": m["status"],
            "icon": m["icon"],
            "contracted": contratado,
            # Rota so existe para quem pode usar: um href que leva a 403 e
            # pior que nenhum href.
            "route": m["route"] if (existe and contratado and pode_o_papel) else None,
            "can_access": bool(existe and contratado and pode_o_papel),
        })
    # Os disponiveis primeiro: quem entra precisa ver o que PODE usar antes do
    # que ainda nao existe.
    saida.sort(key=lambda m: m["status"] != DISPONIVEL)
    return saida
