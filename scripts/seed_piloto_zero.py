"""PILOTO ZERO - o sandbox da Escola ABC com o Aluno Teste A.

O QUE ESTE SCRIPT FAZ
=====================
1. marca a Escola ABC como SANDBOX (metadata, sem migration);
2. garante 3a Serie / Turma 3a A dentro da Escola ABC;
3. cria o ALUNO TESTE A (Person/User/Student/StudentEnrollment/UserSchoolLink);
4. carrega os 14 itens AI_VERIFIED do Nucleo Diagnostic Bank, se ausentes;
5. cria a ATIVIDADE DE ESTEQUIOMETRIA da escola, atribuida a Turma 3a A pelo
   caminho real de ActivityAssignment.

Tudo idempotente. Reexecutar nao duplica nada.

O QUE ELE NAO FAZ
=================
Nao cria escola nova (a ABC ja existia, vinda de `seed_escola_abc.py`), nao
inventa questao, nao mexe em outra escola, nao cria sistema de autenticacao.
O acesso continua sendo o mesmo mecanismo DEV ja usado por todos os portais:
`TestExternalIdentityProvider`, identidade digitada e enviada como Bearer.

POR QUE 3a SERIE E NAO A 1a QUE JA EXISTIA
===========================================
A Escola ABC ja tinha Ensino Medio -> 1a Serie -> Turma A/B, com um "Aluno
ABC" dentro. Nao reaproveitei essa turma de proposito: o Aluno Teste A e um
aluno de SANDBOX, e misturar os dois na mesma turma faria o piloto aparecer
para quem abrisse a Turma A. A serie em si nao participa de nenhuma regra do
fluxo pedagogico - o que isola o piloto e a turma propria.

AS QUESTOES DA ATIVIDADE SAO AS QUE EXISTEM
============================================
A atividade de Estequiometria e montada com as questoes de
CHEMISTRY-PHYSICAL-STOICHIOMETRY REALMENTE disponiveis no banco. Se houver
uma, a atividade tem uma. Nao completo com questao inventada, e nao e isso
que o piloto testa: o aluno nem chega na atividade antes do diagnostico.

Uso:
    .venv/bin/python scripts/seed_piloto_zero.py
    .venv/bin/python scripts/seed_piloto_zero.py --conferir   (so relata)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
import uuid as _uuid
from datetime import date, datetime, timedelta, timezone

_RAIZ = pathlib.Path(__file__).resolve().parent.parent
if str(_RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(_RAIZ / "src"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession, async_sessionmaker, create_async_engine,
)

from agente_ia_edu.db.models import (  # noqa: E402
    AcademicYear, ActivityAssignment, AnswerKeyEntry, AnswerKeyRevision,
    BookletQuestion, CatalogNode, CatalogNodePrerequisite, Class,
    ContentQuestionLink, Exam, ExamApplication, ExamBooklet, GradeLevel,
    Institution, PedagogicalClassification, Person, Question, QuestionOption,
    QuestionVersion, School, Segment, SourceDocument, Student,
    StudentEnrollment, User, UserSchoolLink,
)
from agente_ia_edu.services.activity_assignment_store import (  # noqa: E402
    ActivityAssignmentStore,
)
from agente_ia_edu.services.curriculum_domain_map import (  # noqa: E402
    ORIGIN_OFFICIAL_ACTIVITY,
)
from agente_ia_edu.services.formula_quimica import subscrever  # noqa: E402
from agente_ia_edu.services.list_generator import ListConfiguration  # noqa: E402
from agente_ia_edu.services.question_list_store import (  # noqa: E402
    QuestionListStore, Requester,
)

# ------------------------------------------------------------------ chaves --
SCHOOL_ID = _uuid.UUID("a8c1e3d0-5f6b-4a7e-8c9d-1234567890ab")   # Escola ABC
ANO = 2026

SERIE_NOME = "3ª Série"
TURMA_EXTERNAL_ID = "PILOTO_3A"
TURMA_NOME = "Turma 3ª A (Piloto)"

ALUNO_ID = "aluno_teste_a"            # identidade DEV digitada no portal
ALUNO_NOME = "Aluno Teste A"
PROFESSOR_ID = "professor_abc"        # ja existe na Escola ABC

ESTEQ = "CHEMISTRY-PHYSICAL-STOICHIOMETRY"
BALANC = "CHEMISTRY-GENERAL-BALANCING"

BANK_TAG = "nucleo-diagnostic-bank-v1"
ITENS_JSON = _RAIZ / "scripts" / "data" / "diagnostic_bank_balanceamento_v2.json"
LETRAS = ("A", "B", "C", "D", "E")

# Tudo que este script cria carrega esta marca, e e por ela que o reset do
# piloto sabe o que pode apagar sem tocar no resto do banco.
SANDBOX = {"sandbox": True, "sandbox_type": "PILOT_ZERO"}


def url() -> str:
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    env = _RAIZ / ".env"
    vals = {}
    if env.exists():
        for linha in env.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, _, v = linha.partition("=")
                vals[k.strip()] = v.strip()
    u = vals.get("POSTGRES_USER", "agenteedu")
    p = vals.get("POSTGRES_PASSWORD", "agenteedu_dev")
    d = vals.get("POSTGRES_DB", "agente_ia_edu")
    return f"postgresql+psycopg://{u}:{p}@localhost:5433/{d}"


# ----------------------------------------------------------------- sandbox --

async def marcar_sandbox(s: AsyncSession) -> None:
    """A escola inteira nao e sandbox - o PILOTO dentro dela e.

    A Escola ABC ja tinha turma, professor e coordenador antes deste bloco.
    Marcar a escola como sandbox apagaria essa distincao. O que recebe a marca
    e a TURMA do piloto, o aluno e a atividade.
    """
    escola = await s.get(School, SCHOOL_ID)
    if escola is None:
        raise SystemExit(
            "Escola ABC nao existe. Rode antes: .venv/bin/python scripts/seed_escola_abc.py")
    md = dict(escola.metadata_ or {})
    if md.get("hosts_pilot_zero") is not True:
        md["hosts_pilot_zero"] = True
        escola.metadata_ = md
        print("[escola] marcada como anfitria do Piloto Zero")
    else:
        print("[escola] ja marcada")


# -------------------------------------------------------------- hierarquia --

async def garantir_turma(s: AsyncSession) -> Class:
    ano = await s.scalar(select(AcademicYear).where(
        AcademicYear.school_id == SCHOOL_ID, AcademicYear.year == ANO))
    if ano is None:
        raise SystemExit("ano letivo 2026 nao existe na Escola ABC; rode seed_escola_abc.py")

    segmento = await s.scalar(select(Segment).where(
        Segment.school_id == SCHOOL_ID, Segment.name == "Ensino Médio"))
    if segmento is None:
        raise SystemExit("segmento 'Ensino Médio' nao existe na Escola ABC")

    serie = await s.scalar(select(GradeLevel).where(
        GradeLevel.school_id == SCHOOL_ID, GradeLevel.segment_id == segmento.id,
        GradeLevel.name == SERIE_NOME))
    if serie is None:
        serie = GradeLevel(school_id=SCHOOL_ID, segment_id=segmento.id,
                           name=SERIE_NOME, ordinal=3)
        s.add(serie)
        await s.flush()
        print(f"[serie] criada: {SERIE_NOME}")
    else:
        print(f"[serie] ja existe: {SERIE_NOME}")

    turma = await s.scalar(select(Class).where(
        Class.school_id == SCHOOL_ID, Class.external_id == TURMA_EXTERNAL_ID))
    if turma is None:
        # `classes` nao tem coluna de metadata, entao a marca de sandbox da
        # turma e o proprio external_id PILOTO_*, que e o que o reset procura.
        turma = Class(school_id=SCHOOL_ID, academic_year_id=ano.id,
                      grade_level_id=serie.id, external_id=TURMA_EXTERNAL_ID,
                      name=TURMA_NOME)
        s.add(turma)
        await s.flush()
        print(f"[turma] criada: {TURMA_NOME}")
    else:
        print(f"[turma] ja existe: {TURMA_NOME}")
    return turma


async def garantir_aluno(s: AsyncSession, turma: Class) -> None:
    turma_id, turma_ext = turma.id, turma.external_id

    pessoa = await s.scalar(select(Person).where(
        Person.school_id == SCHOOL_ID, Person.external_id == ALUNO_ID))
    if pessoa is None:
        pessoa = Person(school_id=SCHOOL_ID, external_id=ALUNO_ID,
                        full_name=ALUNO_NOME, status="ACTIVE")
        s.add(pessoa)
        await s.flush()
        print(f"[person] criado: {ALUNO_NOME}")
    else:
        print("[person] ja existe")

    usuario = await s.scalar(select(User).where(
        User.school_id == SCHOOL_ID, User.external_identity_provider == "test",
        User.external_user_id == f"student:{ALUNO_ID}"))
    if usuario is None:
        usuario = User(school_id=SCHOOL_ID, person_id=pessoa.id,
                       external_identity_provider="test",
                       external_user_id=f"student:{ALUNO_ID}",
                       display_name=ALUNO_NOME, status="ACTIVE")
        s.add(usuario)
        await s.flush()
        print("[user] criado")
    else:
        print("[user] ja existe")

    aluno = await s.scalar(select(Student).where(
        Student.school_id == SCHOOL_ID, Student.external_id == ALUNO_ID))
    if aluno is None:
        aluno = Student(school_id=SCHOOL_ID, person_id=pessoa.id,
                        external_id=ALUNO_ID, student_code="PILOTO-0001",
                        status="ACTIVE")
        s.add(aluno)
        await s.flush()
        print("[student] criado")
    else:
        print("[student] ja existe")

    matricula = await s.scalar(select(StudentEnrollment).where(
        StudentEnrollment.student_id == aluno.id,
        StudentEnrollment.class_id == turma_id))
    if matricula is None:
        s.add(StudentEnrollment(
            school_id=SCHOOL_ID, student_id=aluno.id, class_id=turma_id,
            external_id=f"{ALUNO_ID}@{turma_ext}",
            enrolled_on=date(ANO, 2, 1), status="ACTIVE"))
        await s.flush()
        print("[enrollment] criada")
    else:
        print("[enrollment] ja existe")

    # O vinculo que a autorizacao de fato le. O escopo e CLASSROOM na turma do
    # piloto: e isto que impede o Aluno Teste A de ver (e de ser visto por)
    # qualquer outra turma.
    #
    # SEM o prefixo "student:". O provedor de identidade DEV o remove do
    # Bearer antes de montar o contexto, entao `requester.external_user_id`
    # chega como "aluno_teste_a". Um link gravado com o prefixo nunca casa, e
    # o sintoma e silencioso: a lista de atividades volta vazia, como se a
    # escola nao tivesse mandado nada. (Ha linhas assim no banco de dev,
    # vindas de seeds antigos, e elas tambem nao funcionam.)
    vinculo = await s.scalar(select(UserSchoolLink).where(
        UserSchoolLink.school_id == SCHOOL_ID,
        UserSchoolLink.external_user_id == ALUNO_ID))
    if vinculo is None:
        s.add(UserSchoolLink(
            id=_uuid.uuid4(), external_user_id=ALUNO_ID,
            school_id=SCHOOL_ID, user_id=usuario.id, role="STUDENT",
            scope_type="CLASSROOM", scope_external_id=turma_ext,
            class_id=turma_id, active=True, metadata_=dict(SANDBOX),
            created_at=datetime.now(timezone.utc)))
        print("[link] criado (STUDENT / CLASSROOM / PILOTO_3A)")
    else:
        vinculo.active = True
        vinculo.scope_external_id = turma_ext
        vinculo.class_id = turma_id
        vinculo.metadata_ = {**(vinculo.metadata_ or {}), **SANDBOX}
        print("[link] ja existe")


# ------------------------------------------------- Nucleo Diagnostic Bank --

async def _no(s: AsyncSession, codigo: str) -> CatalogNode | None:
    return await s.scalar(select(CatalogNode).where(CatalogNode.code == codigo))


async def garantir_diagnostic_bank(s: AsyncSession) -> int:
    """Carrega os 14 itens AI_VERIFIED, se ainda nao estiverem no banco.

    O banco diagnostico declara a PROPRIA edicao (instituicao Nucleo, exame
    NUCLEO_DIAGNOSTIC, caderno BALANCEAMENTO-V1, gabarito proprio). Sem
    caderno a questao e estruturalmente invisivel para
    `QuestionBankService.list_questions`, que faz inner join com
    BookletQuestion - ver docs/diagnostic-bank-v1.md s1.
    """
    ja = await s.scalar(select(Exam).where(Exam.code == "NUCLEO_DIAGNOSTIC"))
    if ja is not None:
        n = len((await s.execute(
            select(Question.id).where(Question.metadata_["bank"].as_string() == BANK_TAG)
        )).all())
        print(f"[diagnostic-bank] ja carregado: {n} itens")
        return n

    if not ITENS_JSON.exists():
        raise SystemExit(f"itens do Diagnostic Bank nao encontrados em {ITENS_JSON}")
    bruto = json.loads(ITENS_JSON.read_text(encoding="utf-8"))
    itens = [r["item"] for r in bruto if r["decisao"]["status"] == "AI_VERIFIED"]
    if not itens:
        raise SystemExit("nenhum item AI_VERIFIED no arquivo")

    balanc = await _no(s, BALANC)
    esteq = await _no(s, ESTEQ)
    if balanc is None or esteq is None:
        raise SystemExit(
            "nos curriculares ausentes; rode antes: "
            ".venv/bin/python scripts/seed_microacervo_quimica.py")
    arco = await s.scalar(select(CatalogNodePrerequisite).where(
        CatalogNodePrerequisite.content_node_id == esteq.id,
        CatalogNodePrerequisite.prerequisite_node_id == balanc.id))
    if arco is None:
        raise SystemExit("arco ESTEQUIOMETRIA requires BALANCEAMENTO ausente")

    inst = await s.scalar(select(Institution).where(Institution.code == "NUCLEO"))
    if inst is None:
        inst = Institution(code="NUCLEO", name="Nucleo Edu 360")
        s.add(inst)
        await s.flush()
    exame = Exam(institution_id=inst.id, code="NUCLEO_DIAGNOSTIC",
                 name="Nucleo Diagnostic Bank")
    s.add(exame)
    await s.flush()
    edicao = ExamApplication(exam_id=exame.id, year=ANO,
                             application_type="diagnostic", day=1)
    s.add(edicao)
    await s.flush()
    caderno = ExamBooklet(exam_application_id=edicao.id,
                          code="BALANCEAMENTO-V1", color="UNICO")
    s.add(caderno)
    await s.flush()
    doc = SourceDocument(exam_application_id=edicao.id, exam_booklet_id=caderno.id,
                         document_type="ANSWER_KEY",
                         source_url="nucleo://diagnostic-bank/balanceamento/v1",
                         acquired_at=datetime.now(timezone.utc),
                         content_hash=str(_uuid.uuid4()))
    s.add(doc)
    await s.flush()
    revisao = AnswerKeyRevision(source_document_id=doc.id, revision_number=1,
                                is_official=True)
    s.add(revisao)
    await s.flush()

    for numero, item in enumerate(itens, start=1):
        q = Question(validation_status="valid", origin_type="GENERATED",
                     status="PUBLISHED", visibility_scope="PUBLIC",
                     question_type="MULTIPLE_CHOICE",
                     created_by_external_identity=BANK_TAG,
                     metadata_={"bank": BANK_TAG,
                                "diagnostic_skill": item["diagnostic_skill"],
                                "diagnostic_objective": item["diagnostic_objective"],
                                "generation": {"actor_type": "AI",
                                               "version": item["generator_version"]},
                                "verification": {"actor_type": "AI"},
                                "equacoes": item["equacoes"]})
        s.add(q)
        await s.flush()
        # NOTACAO QUIMICA, uma so. Dos 14 itens, 4 nasceram com formula ASCII
        # (`H2 + O2`) e 10 com subscrito Unicode: o gerador nao foi instruido a
        # padronizar, e o aluno via as duas notacoes nas MESMAS tres perguntas.
        # `subscrever` distingue indice de coeficiente e e idempotente, entao os
        # 10 que ja estavam certos nao mudam.
        enunciado = subscrever(item["stem"])
        v = QuestionVersion(question_id=q.id, version_kind="official_original",
                            canonical_text=enunciado, statement=enunciado,
                            content_hash=str(_uuid.uuid4()), is_immutable=True,
                            recommended_difficulty=item["difficulty"])
        s.add(v)
        await s.flush()
        opcoes = {}
        for pos, letra in enumerate(LETRAS, start=1):
            o = QuestionOption(question_version_id=v.id, option_key=letra,
                               position=pos, text=subscrever(item["options"][letra]),
                               is_valid_option=(letra == item["correct_answer"]))
            s.add(o)
            await s.flush()
            opcoes[letra] = o
        bq = BookletQuestion(exam_booklet_id=caderno.id, question_version_id=v.id,
                             position=numero, official_number=numero, page_number=1)
        s.add(bq)
        await s.flush()
        s.add(AnswerKeyEntry(answer_key_revision_id=revisao.id,
                             booklet_question_id=bq.id,
                             official_answer_label=item["correct_answer"],
                             resolved_option_id=opcoes[item["correct_answer"]].id,
                             page_number=1))
        s.add(PedagogicalClassification(
            question_version_id=v.id, discipline="CURRICULUM_PROPOSAL",
            content=BALANC, subcontent=item["diagnostic_skill"],
            difficulty=item["difficulty"], reasoning_type="DIAGNOSTIC",
            prerequisites=[], keywords=[], competencies=[],
            skills=[item["diagnostic_skill"]], status="CLASSIFIED", source="ai",
            lifecycle="ACTIVE", provenance="AI_VERIFIED",
            model_version=item["generator_version"], prompt_version="v2",
            metadata_={"taxonomy_version": "curriculum-v2",
                       "primary_content_code": BALANC,
                       "visual_dependency": False,
                       "diagnostic_skill": item["diagnostic_skill"]}))
        s.add(ContentQuestionLink(content_node_id=balanc.id,
                                  question_version_id=v.id))
    print(f"[diagnostic-bank] {len(itens)} itens AI_VERIFIED carregados")
    return len(itens)



async def normalizar_formulas(s: AsyncSession) -> int:
    """Uma notacao quimica so, nos itens que o Nucleo mesmo gerou.

    POR QUE ISTO EXISTE SEPARADO DO CARREGAMENTO
    =============================================
    `garantir_diagnostic_bank` e idempotente e PULA quando os itens ja estao
    la - entao normalizar na escrita (o que ele passou a fazer) nao alcanca os
    14 que ja foram gravados com a notacao mista. Esta funcao alcanca.

    O QUE ELA PODE TOCAR, E SO ISSO
    ================================
    `metadata_->bank == nucleo-diagnostic-bank-v1`. Nunca questao importada:
    reescrever o enunciado de uma questao do ENEM seria falsificar a fonte.

    SOBRE A IMUTABILIDADE
    =====================
    `QuestionVersion.is_immutable` existe para impedir que o enunciado de uma
    prova mude por baixo de quem ja respondeu. Aqui a troca e `H2` -> `H₂`: nao
    muda a quimica, nao muda qual alternativa esta certa, nao muda o gabarito.
    E uma correcao de NOTACAO em item de autoria propria, e fica registrada no
    metadata para que ninguem precise descobrir isso lendo o diff.
    """
    linhas = (await s.execute(
        select(QuestionVersion, Question)
        .join(Question, Question.id == QuestionVersion.question_id)
        .where(Question.metadata_["bank"].as_string() == BANK_TAG)
    )).all()
    tocados = 0
    for v, q in linhas:
        antes = (v.statement, v.canonical_text)
        v.statement = subscrever(v.statement)
        v.canonical_text = subscrever(v.canonical_text)
        mudou = (v.statement, v.canonical_text) != antes

        opcoes = (await s.execute(select(QuestionOption).where(
            QuestionOption.question_version_id == v.id))).scalars().all()
        for o in opcoes:
            novo = subscrever(o.text)
            if novo != o.text:
                o.text = novo
                mudou = True

        if mudou:
            q.metadata_ = {**(q.metadata_ or {}), "notation_normalised": True}
            tocados += 1
    if tocados:
        print(f"[formulas] notacao normalizada em {tocados} iten(s)")
    else:
        print("[formulas] ja padronizadas")
    return tocados


# ------------------------------------------- a atividade real da escola ----

async def _questoes_de_estequiometria(s: AsyncSession) -> list[_uuid.UUID]:
    """As questoes de Estequiometria que de fato podem ser respondidas hoje.

    Os quatro filtros sao os mesmos que a selecao do Question Bank aplica -
    publicada, classificada em definitivo, com caderno (sem ele nao ha
    gabarito congelavel) e com alternativas.
    """
    linhas = (await s.execute(
        select(QuestionVersion.id)
        .join(Question, Question.id == QuestionVersion.question_id)
        .join(PedagogicalClassification,
              PedagogicalClassification.question_version_id == QuestionVersion.id)
        .join(BookletQuestion, BookletQuestion.question_version_id == QuestionVersion.id)
        .where(Question.status == "PUBLISHED",
               PedagogicalClassification.content == ESTEQ,
               PedagogicalClassification.status == "CLASSIFIED",
               PedagogicalClassification.lifecycle == "ACTIVE")
        .distinct()
    )).all()
    return [r[0] for r in linhas]


async def garantir_atividade(s: AsyncSession, turma: Class) -> str | None:
    turma_ext = turma.external_id

    ja = await s.scalar(select(ActivityAssignment).where(
        ActivityAssignment.target_type == "CLASS",
        ActivityAssignment.target_id == turma_ext,
        ActivityAssignment.status == "ACTIVE"))
    if ja is not None:
        print(f"[atividade] ja existe: {ja.id}")
        return str(ja.id)

    versoes = await _questoes_de_estequiometria(s)
    if not versoes:
        print("[atividade] BLOQUEADA: nenhuma questao de Estequiometria publicada "
              "e classificada em definitivo no banco")
        return None
    print(f"[atividade] {len(versoes)} questao(oes) de Estequiometria disponivel(is)")

    # O professor da Escola ABC e quem cria e distribui - o mesmo caminho do
    # portal do professor, nao um atalho de seed.
    prof = Requester(external_user_id=PROFESSOR_ID, school_id=str(SCHOOL_ID),
                     role="TEACHER", is_platform_admin=False)
    listas = QuestionListStore(s)
    resumo = await listas.create(
        configuration=ListConfiguration(
            title="Atividade de Estequiometria",
            instructions="Atividade da escola. Responda com calma.",
            answer_key_presentation="KEY_AT_END"),
        question_version_ids=versoes,
        requester=prof)
    lista_id = _uuid.UUID(str(resumo.id))
    await listas.finalize(lista_id, requester=prof)

    vista, _ = await ActivityAssignmentStore(s).create(
        lista_id, requester=prof, target_type="CLASS", target_id=turma_ext,
        due_at=(datetime.now(timezone.utc) + timedelta(days=3)),
        origin=ORIGIN_OFFICIAL_ACTIVITY,
        extra_metadata={**SANDBOX, "content_code": ESTEQ})
    print(f"[atividade] criada: {vista.id}")
    return str(vista.id)


# ----------------------------------------------------------------- relato --

async def conferir(s: AsyncSession) -> None:
    turma = await s.scalar(select(Class).where(
        Class.school_id == SCHOOL_ID, Class.external_id == TURMA_EXTERNAL_ID))
    aluno = await s.scalar(select(Student).where(
        Student.school_id == SCHOOL_ID, Student.external_id == ALUNO_ID))
    itens = len((await s.execute(
        select(Question.id).where(Question.metadata_["bank"].as_string() == BANK_TAG))).all())
    atividade = await s.scalar(select(ActivityAssignment).where(
        ActivityAssignment.target_type == "CLASS",
        ActivityAssignment.target_id == TURMA_EXTERNAL_ID,
        ActivityAssignment.status == "ACTIVE"))
    esteq = await _questoes_de_estequiometria(s)

    print("\n--- PILOTO ZERO ---")
    print(f"  turma .................. {'OK' if turma else 'FALTA'}  {TURMA_NOME}")
    print(f"  aluno .................. {'OK' if aluno else 'FALTA'}  {ALUNO_NOME}")
    print(f"  diagnostic bank ........ {itens} itens")
    print(f"  estequiometria ......... {len(esteq)} questao(oes) utilizavel(is)")
    print(f"  atividade da escola .... {'OK' if atividade else 'FALTA'}")
    print(f"\n  entre como:  {ALUNO_ID}   (portal do aluno, campo de identidade)")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conferir", action="store_true", help="so relata, nao escreve")
    args = ap.parse_args()

    engine = create_async_engine(url())
    fabrica = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with fabrica() as s:
            if args.conferir:
                await conferir(s)
                return
            await marcar_sandbox(s)
            turma = await garantir_turma(s)
            await s.commit()
            await garantir_aluno(s, turma)
            await s.commit()
            await garantir_diagnostic_bank(s)
            await s.commit()
            await normalizar_formulas(s)
            await s.commit()
            await garantir_atividade(s, turma)
            await s.commit()
            await conferir(s)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
