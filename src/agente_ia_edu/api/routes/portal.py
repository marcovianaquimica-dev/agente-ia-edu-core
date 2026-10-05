"""PORTAL - a porta de entrada do ecossistema Nucleo Edu 360.

Um endpoint so, que responde as tres perguntas de quem acabou de entrar:

    quem sou      identidade e papel, do vinculo real
    onde estou    a instituicao, pelo nome
    o que tenho   os seis modulos, com o estado de cada um

NENHUM MOTOR NOVO. A autorizacao e a mesma `AuthorizationService` de sempre,
e os modulos contratados saem de `school_modules`, que ja existia. O que esta
rota acrescenta e o CATALOGO (`modulos_do_portal`) - a lista do que o produto
tem, que e informacao sobre o produto e nao sobre uma escola.

NENHUMA METRICA. A Home V1 nao mostra numero nenhum, e por isso esta resposta
nao carrega nenhum: um contador inventado para encher tela e divida que
aparece no dia da apresentacao.
"""

from fastapi import APIRouter, Depends

from ..dependencies import get_current_identity, get_session_factory
from ...identity import ExternalIdentityContext
from ...services.authorization import AuthorizationService
from ...services.modulos_do_portal import modulos_para

portal_router = APIRouter(prefix="/api/v1/portal", tags=["portal"])


@portal_router.get(
    "/overview",
    summary="Quem sou, onde estou e quais modulos do Nucleo Edu 360 tenho",
)
async def portal_overview(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        contexto = await AuthorizationService(session).resolve_context(identity)

        # O NOME da instituicao, nao so o id - e o que a pessoa reconhece.
        # Quem nao tem vinculo fica sem nome, e a Home diz isso em vez de
        # inventar uma escola.
        nome_escola = None
        nome_pessoa = None
        if contexto.school_id:
            from sqlalchemy import select

            from ...db.models import Person, School, User

            nome_escola = (await session.execute(
                select(School.name).where(School.id == contexto.school_id)
            )).scalar_one_or_none()

            # O NOME DA PESSOA, quando ha cadastro.
            #
            # Sem isto a Home do Assessor dizia "Ola, aluno_teste_a" - o
            # identificador tecnico no lugar do nome, numa tela que seria
            # projetada. O nome estava em `persons.full_name` o tempo todo.
            #
            # NULO quando nao ha cadastro: montar um nome a partir do login
            # seria inventar sobre a pessoa.
            nome_pessoa = (await session.execute(
                select(Person.full_name)
                .join(User, User.person_id == Person.id)
                .where(User.external_user_id == (
                    contexto.external_identity_id or contexto.user_id))
            )).scalars().first()

        return {
            "user": {
                "external_id": contexto.external_identity_id or contexto.user_id,
                "name": nome_pessoa,
                "role": contexto.role,
                "is_platform_admin": bool(contexto.is_platform_admin),
            },
            "institution": {
                "id": str(contexto.school_id) if contexto.school_id else None,
                "name": nome_escola,
            },
            "modules": modulos_para(
                role=contexto.role,
                contratados=contexto.modules,
                is_platform_admin=bool(contexto.is_platform_admin),
            ),
        }
