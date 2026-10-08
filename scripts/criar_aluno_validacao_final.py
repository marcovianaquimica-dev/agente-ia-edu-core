"""Cria o aluno RESERVADO a validacao manual do dono - e so ele.

ELE NAO E UM ALUNO DE QA
=========================
Nenhum teste automatizado e nenhum script de QA pode usa-lo. A diferenca
nao e de intencao: `tests/test_alunos_reservados.py` varre `tests/` e
`scripts/` e falha se a identidade aparecer em qualquer arquivo que nao
seja este.

POR QUE O ANTERIOR PRECISOU SER SUBSTITUIDO
============================================
`aluno_teste_jornada` cumpria este papel. Em 2026-10-07 ele recebeu uma
escrita derivada - uma chamada a `/domain/rebuild` durante uma auditoria - e
deixou de ser uma referencia limpa. O historico dele foi preservado; o que
mudou foi a marca deste aqui, que agora diz em campo proprio o que nao
fazer, em vez de depender de alguem lembrar.

NAO reseta ninguem, nao apaga nada, nao cria escola nem turma.

A forma seguida e a do Aluno Teste A JA EXISTENTE no banco (auditada antes
de escrever isto), e nao a do `scripts/seed_piloto_zero.py`: o seed grava
`User.external_user_id = "student:<id>"`, com prefixo, e a linha que
funciona no banco esta SEM prefixo. O seed tem um bug conhecido e nao foi
tocado aqui.

Idempotente: reexecutar nao duplica nada.

    .venv/bin/python scripts/criar_aluno_validacao_final.py
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import uuid as _uuid
from datetime import date, datetime, timezone

# Caminho relativo ao proprio script - ele vive no repositorio agora, e nao
# num rascunho de sessao.
_RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RAIZ / "src"))

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/agente_ia_edu")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession, async_sessionmaker, create_async_engine,
)

from agente_ia_edu.db.models import (  # noqa: E402
    Class, Person, School, Student, StudentEnrollment, User, UserSchoolLink,
)

# Tudo conferido no banco antes de escrever.
ESCOLA_ID = _uuid.UUID("a8c1e3d0-5f6b-4a7e-8c9d-1234567890ab")   # Escola ABC
TURMA_EXT = "PILOTO_3A"                                          # Turma 3a A (Piloto)

ALUNO_ID = "aluno_validacao_final"
ALUNO_NOME = "Aluno Validação Final"
CODIGO = "PILOTO-0010"        # PILOTO-0001 e o Aluno Teste A
ANO = 2026

# A MARCA, E O QUE ELA PRECISA DIZER DESTA VEZ.
#
# `aluno_teste_jornada` era o reservado para a validacao manual do dono, e a
# unica coisa que o marcava era a mesma chave de proposito que os alunos de
# QA carregam - e por isso ele se parecia com um deles. Em 2026-10-07 ele recebeu uma escrita
# derivada (um /domain/rebuild) durante uma auditoria, justamente porque nada
# na marca dizia "nao escreva aqui".
#
# `reserved_for` diz para quem ele e; `do_not_use_in_qa` diz, em campo
# proprio e legivel por teste, o que nao fazer com ele. E a chave de
# proposito de QA nao entra: ele nao e um aluno de QA, e carrega-la e
# exatamente o que confundiu antes.
MARCA = {"sandbox": True, "sandbox_type": "PILOT_ZERO",
         "reserved_for": "OWNER_MANUAL_VALIDATION",
         "do_not_use_in_qa": True,
         "created_on": "2026-10-07"}


async def main() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = async_sessionmaker(engine, class_=AsyncSession,
                                expire_on_commit=False)
    criados: list[str] = []
    async with factory() as s:
        escola = await s.get(School, ESCOLA_ID)
        assert escola is not None, "a Escola ABC nao esta no banco"
        turma = await s.scalar(select(Class).where(
            Class.school_id == ESCOLA_ID, Class.external_id == TURMA_EXT))
        assert turma is not None, f"a turma {TURMA_EXT} nao existe"
        print(f"escola  {escola.name}")
        print(f"turma   {turma.name}  ({turma.external_id})")

        pessoa = await s.scalar(select(Person).where(
            Person.school_id == ESCOLA_ID, Person.external_id == ALUNO_ID))
        if pessoa is None:
            pessoa = Person(school_id=ESCOLA_ID, external_id=ALUNO_ID,
                            full_name=ALUNO_NOME, status="ACTIVE")
            s.add(pessoa)
            await s.flush()
            criados.append(f"persons            {pessoa.id}")
        else:
            print("[person] ja existia")

        # SEM o prefixo "student:" - ver o cabecalho.
        usuario = await s.scalar(select(User).where(
            User.school_id == ESCOLA_ID,
            User.external_user_id == ALUNO_ID))
        if usuario is None:
            usuario = User(school_id=ESCOLA_ID, person_id=pessoa.id,
                           external_identity_provider="test",
                           external_user_id=ALUNO_ID,
                           display_name=ALUNO_NOME, status="ACTIVE")
            s.add(usuario)
            await s.flush()
            criados.append(f"users              {usuario.id}")
        else:
            print("[user] ja existia")

        aluno = await s.scalar(select(Student).where(
            Student.school_id == ESCOLA_ID, Student.external_id == ALUNO_ID))
        if aluno is None:
            aluno = Student(school_id=ESCOLA_ID, person_id=pessoa.id,
                            external_id=ALUNO_ID, student_code=CODIGO,
                            status="ACTIVE")
            s.add(aluno)
            await s.flush()
            criados.append(f"students           {aluno.id}")
        else:
            print("[student] ja existia")

        matricula = await s.scalar(select(StudentEnrollment).where(
            StudentEnrollment.student_id == aluno.id,
            StudentEnrollment.class_id == turma.id))
        if matricula is None:
            matricula = StudentEnrollment(
                school_id=ESCOLA_ID, student_id=aluno.id, class_id=turma.id,
                external_id=f"{ALUNO_ID}@{TURMA_EXT}",
                enrolled_on=date(ANO, 2, 1), status="ACTIVE")
            s.add(matricula)
            await s.flush()
            criados.append(f"student_enrollments {matricula.id}")
        else:
            print("[enrollment] ja existia")

        vinculo = await s.scalar(select(UserSchoolLink).where(
            UserSchoolLink.school_id == ESCOLA_ID,
            UserSchoolLink.external_user_id == ALUNO_ID))
        if vinculo is None:
            vinculo = UserSchoolLink(
                id=_uuid.uuid4(), external_user_id=ALUNO_ID,
                school_id=ESCOLA_ID, user_id=usuario.id, role="STUDENT",
                scope_type="CLASSROOM", scope_external_id=TURMA_EXT,
                class_id=turma.id, active=True, metadata_=dict(MARCA),
                created_at=datetime.now(timezone.utc))
            s.add(vinculo)
            await s.flush()
            criados.append(f"user_school_links  {vinculo.id}")
        else:
            print("[link] ja existia")

        await s.commit()

    print()
    print("--- REGISTROS CRIADOS ---")
    for linha in criados:
        print("  " + linha)
    if not criados:
        print("  nenhum (tudo ja existia)")
    await engine.dispose()


asyncio.run(main())
