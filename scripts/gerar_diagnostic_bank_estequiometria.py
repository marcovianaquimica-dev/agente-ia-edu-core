"""Gera e verifica os itens do NUCLEO DIAGNOSTIC BANK - Estequiometria.

    OBJETIVO -> GERADOR -> item candidato -> VERIFICADOR -> CONTRATO -> decisao
                                               |
                                      ARITMETICA EM PYTHON

O MESMO BANCO, OUTRO CONTEUDO
==============================
Nenhuma tabela nova. Question / QuestionVersion / QuestionOption,
`origin_type='GENERATED'`, a edicao propria do Nucleo, `AI_VERIFIED`. O que
muda e a VERIFICACAO.

O QUE A ARITMETICA CONFERE, E O LLM NAO PRECISA
================================================
O gerador declara os DADOS DA CONTA: a equacao, a especie de partida, a de
chegada, a quantidade e as unidades. `diagnostic_bank_estequiometria` refaz a
conta do zero - massa molar pela tabela da IUPAC, proporcao pelos coeficientes
- e so entao compara com a alternativa marcada.

O ERRO QUE ISSO PEGA E QUE DOIS LLMs NAO PEGARIAM
==================================================
Equacao nao balanceada. A regra de tres fecha perfeitamente sobre coeficientes
errados; o numero que sai parece certo; e um segundo modelo refaz a MESMA
conta sobre a MESMA equacao errada e concorda. `proporcao_molar` recusa antes
de calcular qualquer coisa.

O verificador-LLM continua existindo, mas para o que a aritmetica nao alcanca:
se o item mede estequiometria de verdade, se da para acertar por eliminacao, se
o enunciado e autocontido. Ele NAO recebe o gabarito nem a justificativa do
gerador - resolve sozinho.

Uso:
    PYTHONPATH=src .venv/bin/python scripts/gerar_diagnostic_bank_estequiometria.py --dry-run
    ...  --gerar
    ...  --gerar --persistir
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
import uuid as _uuid
from dataclasses import asdict
from datetime import datetime, timezone

_RAIZ = pathlib.Path(__file__).resolve().parent.parent
if str(_RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(_RAIZ / "src"))


def _carregar_env() -> None:
    arquivo = _RAIZ / ".env"
    if not arquivo.exists():
        return
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            k, _, v = linha.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


_carregar_env()

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession, async_sessionmaker, create_async_engine,
)

from agente_ia_edu.db.models import (  # noqa: E402
    AnswerKeyEntry, AnswerKeyRevision, BookletQuestion, CatalogNode, Exam,
    ExamApplication, ExamBooklet, Institution, PedagogicalClassification,
    Question, QuestionOption, QuestionVersion, SourceDocument,
)
from agente_ia_edu.db.models.catalog import ContentQuestionLink  # noqa: E402
from agente_ia_edu.providers.factory import build_text_provider  # noqa: E402
from agente_ia_edu.providers.models import TextGenerationRequest  # noqa: E402
from agente_ia_edu.services.diagnostic_bank_estequiometria import (  # noqa: E402
    BANK_TAG, CONTENT_CODE, HABILIDADES, LETRAS, ORIGIN_TYPE,
    ItemEstequiometria, conferir_calculo, conferir_estrutura,
)
from agente_ia_edu.services.formula_quimica import subscrever  # noqa: E402

GERADOR_VERSAO = "estequiometria-gerador@2026-10-04-v1"
VERIFICADOR_VERSAO = "estequiometria-verificador@2026-10-04-v1"
PROMPT_VERSAO = "v1"

CADERNO = "ESTEQUIOMETRIA-V1"

# 3 por habilidade x 4 habilidades = 12 gerados. A faixa pedida e 12-20
# AI_VERIFIED; com rejeicoes, 16 gerados da margem.
PLANO = [(skill, dificuldade)
         for skill in HABILIDADES
         for dificuldade in ("EASY", "MEDIUM", "MEDIUM", "HARD")]

PROMPT_GERADOR = """Você é um professor de Química do Ensino Médio brasileiro criando um item de MICRODIAGNÓSTICO.

OBJETIVO DIAGNÓSTICO: {objetivo}
HABILIDADE: {skill}
DIFICULDADE: {dificuldade}

O item serve para descobrir onde o aluno está em ESTEQUIOMETRIA. Não é prova,
não vale nota.

Regras obrigatórias:
- item ORIGINAL, seu. NÃO copie nem parafraseie questão de vestibular.
- curto: enunciado de no máximo 3 linhas.
- autocontido: todos os dados no texto, inclusive as massas molares que o
  aluno precisar.
- SEM figura, gráfico, tabela ou esquema. O aluno lê só texto.
- exatamente 5 alternativas (A a E) e UMA única correta.
- toda alternativa deve ser UM VALOR NUMÉRICO com unidade ("35,7 g", "2,0 mol").
  Nada de "mais de 30 g" nem de texto sem número.
- as 4 erradas devem vir de ERROS TÍPICOS, não de números aleatórios:
  inverter a proporção molar, ignorar o coeficiente, confundir mol com massa,
  usar a massa molar errada.
- os 5 valores devem ser claramente diferentes entre si (mais de 1% de
  diferença), senão o aluno não consegue escolher.
- a EQUAÇÃO DEVE ESTAR BALANCEADA. Isto será conferido por contagem de átomos.

Responda SOMENTE com JSON, sem cercas de código:
{{"stem": "<enunciado>",
  "options": {{"A": "...", "B": "...", "C": "...", "D": "...", "E": "..."}},
  "correct_answer": "<A-E>",
  "rationale": "<como se chega lá, 2 frases>",
  "equacao": "<a equação balanceada, com coeficientes explícitos>",
  "especie_de": "<fórmula da espécie cujo dado o enunciado FORNECE>",
  "especie_para": "<fórmula da espécie que o enunciado PERGUNTA>",
  "quantidade": <número que o enunciado fornece>,
  "unidade_entrada": "<g ou mol>",
  "unidade_saida": "<g ou mol>"}}

Os cinco últimos campos são os DADOS DA CONTA: um programa vai refazer o
cálculo a partir deles e comparar com a sua alternativa correta. Se não
baterem, o item é descartado. Use exatamente as fórmulas como aparecem na
equação.
"""

PROMPT_VERIFICADOR = """Você é um professor de Química revisando um item de diagnóstico.

RESOLVA a questão abaixo por conta própria. Você NÃO recebeu o gabarito.

{questao}

Responda SOMENTE com JSON, sem cercas de código:
{{"resposta": "<A-E que VOCÊ concluiu>",
  "resolucao": "<como chegou lá, 2 frases>",
  "unica_resposta": <true|false>,
  "outras_defensaveis": ["<letras que também poderiam ser defendidas>"],
  "falta_informacao": <true|false>,
  "depende_de_visual": <true|false>,
  "quimica_correta": <true|false>,
  "mede_estequiometria": <true|false>,
  "da_para_acertar_sem_calcular": <true|false>,
  "problemas": ["<...>"]}}

Sobre "mede_estequiometria": true só se o aluno precisar usar a proporção dos
coeficientes, a massa molar ou a conversão massa-mol para responder.

Sobre "da_para_acertar_sem_calcular": true se der para acertar por eliminação,
pela ordem de grandeza ou pelo formato das alternativas.
"""


def so_json(bruto: str) -> dict:
    texto = (bruto or "").strip()
    if texto.startswith("```"):
        texto = texto.split("```")[1]
        if texto.startswith("json"):
            texto = texto[4:]
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fim < 0:
        raise ValueError(f"resposta sem JSON: {bruto[:200]!r}")
    return json.loads(texto[inicio:fim + 1])


def url() -> str:
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    u = os.getenv("POSTGRES_USER", "agenteedu")
    p = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    d = os.getenv("POSTGRES_DB", "agente_ia_edu")
    return f"postgresql+psycopg://{u}:{p}@localhost:5433/{d}"


def texto_da_questao(item: ItemEstequiometria) -> str:
    alternativas = "\n".join(f"{k}) {item.options[k]}" for k in LETRAS)
    return f"{item.stem}\n\n{alternativas}"


async def gerar_um(provider, skill: str, dificuldade: str, uso: dict) -> ItemEstequiometria:
    pedido = TextGenerationRequest(
        prompt=PROMPT_GERADOR.format(objetivo=HABILIDADES[skill], skill=skill,
                                     dificuldade=dificuldade))
    resposta = await provider.generate(pedido)
    uso["gerador"] = uso.get("gerador", 0) + 1
    dados = so_json(resposta.text)
    return ItemEstequiometria(
        diagnostic_skill=skill,
        diagnostic_objective=HABILIDADES[skill],
        difficulty=dificuldade,
        # Uma notacao quimica so, desde a origem - a licao do banco anterior,
        # onde 4 de 14 itens nasceram em ASCII e o aluno via as duas na mesma
        # sessao de tres perguntas.
        stem=subscrever(dados["stem"]),
        options={k: subscrever(str(v)) for k, v in dados["options"].items()},
        correct_answer=str(dados["correct_answer"]).strip().upper(),
        rationale=dados.get("rationale", ""),
        equacao=dados.get("equacao", ""),
        especie_de=str(dados.get("especie_de", "")).strip(),
        especie_para=str(dados.get("especie_para", "")).strip(),
        quantidade=float(dados["quantidade"]) if dados.get("quantidade") is not None else None,
        unidade_entrada=str(dados.get("unidade_entrada", "")).strip(),
        unidade_saida=str(dados.get("unidade_saida", "")).strip(),
        generator_version=GERADOR_VERSAO)


async def verificar_um(provider, item: ItemEstequiometria, uso: dict) -> dict:
    """O verificador recebe ENUNCIADO E ALTERNATIVAS. Nada mais.

    Nem gabarito, nem justificativa, nem os dados da conta: perguntar "o
    gabarito D esta certo?" convida a concordar; perguntar "qual e a resposta?"
    obriga a calcular.
    """
    pedido = TextGenerationRequest(
        prompt=PROMPT_VERIFICADOR.format(questao=texto_da_questao(item)))
    resposta = await provider.generate(pedido)
    uso["verificador"] = uso.get("verificador", 0) + 1
    return so_json(resposta.text)


def decidir_item(item: ItemEstequiometria, ver: dict) -> dict:
    """O contrato. Estrutural, fail-closed, SEM limiar de confianca."""
    motivos: list[str] = []
    motivos.extend(conferir_estrutura(item))

    calculo = conferir_calculo(item)
    if not calculo.conferiu:
        motivos.extend(f"calculo: {p}" for p in calculo.problemas)

    if str(ver.get("resposta") or "").strip().upper() != item.correct_answer.upper():
        motivos.append(
            f"o verificador resolveu e chegou em {ver.get('resposta')!r}, "
            f"o gerador afirma {item.correct_answer!r}")
    if not ver.get("unica_resposta", False):
        motivos.append("o verificador nao viu uma unica resposta correta")
    if ver.get("outras_defensaveis"):
        motivos.append(f"outras alternativas defensaveis: {ver['outras_defensaveis']}")
    if ver.get("falta_informacao"):
        motivos.append("falta informacao para responder")
    if ver.get("depende_de_visual"):
        motivos.append("depende de recurso visual")
    if not ver.get("quimica_correta", False):
        motivos.append("o verificador apontou quimica incorreta")
    if not ver.get("mede_estequiometria", False):
        motivos.append("o item nao mede estequiometria")
    if ver.get("da_para_acertar_sem_calcular"):
        motivos.append("da para acertar sem calcular")
    for p in (ver.get("problemas") or []):
        motivos.append(f"verificador: {p}")

    aprovado = not motivos
    return {
        "status": "AI_VERIFIED" if aprovado else "REQUIRES_REVIEW",
        "motivos": motivos or ["estrutura ok, calculo refeito em Python e "
                               "batendo com o gabarito, verificador resolveu "
                               "sozinho e chegou na mesma resposta"],
        "verificacao_calculo": asdict(calculo),
        "verificador": ver,
    }


# ------------------------------------------------------------- persistir ---

async def _edicao_propria(s: AsyncSession):
    """O caderno do Nucleo para Estequiometria.

    Questao sem caderno e estruturalmente invisivel para
    `QuestionBankService.list_questions`, que faz inner join com
    BookletQuestion. Mesmo arranjo do banco de Balanceamento, outro caderno.
    """
    inst = await s.scalar(select(Institution).where(Institution.code == "NUCLEO"))
    if inst is None:
        inst = Institution(code="NUCLEO", name="Nucleo Edu 360")
        s.add(inst); await s.flush()
    exame = await s.scalar(select(Exam).where(Exam.code == "NUCLEO_DIAGNOSTIC"))
    if exame is None:
        exame = Exam(institution_id=inst.id, code="NUCLEO_DIAGNOSTIC",
                     name="Nucleo Diagnostic Bank")
        s.add(exame); await s.flush()
    edicao = await s.scalar(select(ExamApplication).where(
        ExamApplication.exam_id == exame.id, ExamApplication.year == 2026))
    if edicao is None:
        edicao = ExamApplication(exam_id=exame.id, year=2026,
                                 application_type="diagnostic", day=1)
        s.add(edicao); await s.flush()
    caderno = await s.scalar(select(ExamBooklet).where(ExamBooklet.code == CADERNO))
    if caderno is None:
        caderno = ExamBooklet(exam_application_id=edicao.id, code=CADERNO,
                              color="UNICO")
        s.add(caderno); await s.flush()
    revisao = None
    doc = await s.scalar(select(SourceDocument).where(
        SourceDocument.exam_booklet_id == caderno.id))
    if doc is None:
        doc = SourceDocument(exam_application_id=edicao.id,
                             exam_booklet_id=caderno.id, document_type="ANSWER_KEY",
                             source_url="nucleo://diagnostic-bank/estequiometria/v1",
                             acquired_at=datetime.now(timezone.utc),
                             content_hash=str(_uuid.uuid4()))
        s.add(doc); await s.flush()
    else:
        revisao = await s.scalar(select(AnswerKeyRevision).where(
            AnswerKeyRevision.source_document_id == doc.id))
    if revisao is None:
        revisao = AnswerKeyRevision(source_document_id=doc.id, revision_number=1,
                                    is_official=True)
        s.add(revisao); await s.flush()
    return caderno, revisao


async def persistir(s: AsyncSession, item: ItemEstequiometria, decisao: dict,
                    caderno, revisao, numero: int) -> str:
    agora = datetime.now(timezone.utc).isoformat()
    proveniencia = {
        "bank": BANK_TAG,
        "diagnostic_skill": item.diagnostic_skill,
        "diagnostic_objective": item.diagnostic_objective,
        "generation": {"actor_type": "AI", "version": item.generator_version,
                       "prompt_version": PROMPT_VERSAO, "at": agora,
                       "provider": os.getenv("AI_PROVIDER", "openai")},
        "verification": {"actor_type": "AI", "version": VERIFICADOR_VERSAO,
                         "prompt_version": PROMPT_VERSAO, "at": agora,
                         "deterministic_calculation": decisao["verificacao_calculo"],
                         "solved_independently": decisao["verificador"].get("resposta")},
        "status": decisao["status"],
        "motivos": decisao["motivos"],
        "rationale": item.rationale,
        "equacao": item.equacao,
        "dados_da_conta": {"de": item.especie_de, "para": item.especie_para,
                           "quantidade": item.quantidade,
                           "unidade_entrada": item.unidade_entrada,
                           "unidade_saida": item.unidade_saida},
    }

    q = Question(validation_status="valid", origin_type=ORIGIN_TYPE,
                 status="PUBLISHED", visibility_scope="PUBLIC",
                 question_type="MULTIPLE_CHOICE",
                 created_by_external_identity=BANK_TAG,
                 metadata_=proveniencia)
    s.add(q); await s.flush()
    v = QuestionVersion(question_id=q.id, version_kind="official_original",
                        canonical_text=texto_da_questao(item), statement=item.stem,
                        content_hash=str(_uuid.uuid4()), is_immutable=True,
                        recommended_difficulty=item.difficulty)
    s.add(v); await s.flush()
    opcoes = {}
    for pos, letra in enumerate(LETRAS, start=1):
        o = QuestionOption(question_version_id=v.id, option_key=letra, position=pos,
                           text=item.options[letra],
                           is_valid_option=(letra == item.correct_answer))
        s.add(o); await s.flush(); opcoes[letra] = o

    bq = BookletQuestion(exam_booklet_id=caderno.id, question_version_id=v.id,
                         position=numero, official_number=numero, page_number=1)
    s.add(bq); await s.flush()
    s.add(AnswerKeyEntry(answer_key_revision_id=revisao.id, booklet_question_id=bq.id,
                         official_answer_label=item.correct_answer,
                         resolved_option_id=opcoes[item.correct_answer].id,
                         page_number=1))
    s.add(PedagogicalClassification(
        question_version_id=v.id, discipline="CURRICULUM_PROPOSAL",
        content=CONTENT_CODE, subcontent=item.diagnostic_skill,
        difficulty=item.difficulty, reasoning_type="DIAGNOSTIC",
        prerequisites=[], keywords=[], competencies=[], skills=[item.diagnostic_skill],
        status="CLASSIFIED", source="ai", lifecycle="ACTIVE",
        provenance="AI_VERIFIED",
        model_version=item.generator_version, prompt_version=PROMPT_VERSAO,
        provider_name=os.getenv("AI_PROVIDER", "openai"),
        metadata_={"taxonomy_version": "curriculum-v2",
                   "primary_content_code": CONTENT_CODE,
                   "visual_dependency": False,
                   "diagnostic_skill": item.diagnostic_skill}))
    no = await s.scalar(select(CatalogNode).where(CatalogNode.code == CONTENT_CODE))
    if no is not None:
        s.add(ContentQuestionLink(content_node_id=no.id, question_version_id=v.id))
    await s.flush()
    return str(v.id)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--gerar", action="store_true")
    ap.add_argument("--persistir", action="store_true")
    ap.add_argument("--saida", default="/tmp/estequiometria_v1.json")
    ap.add_argument("--skills", default="",
                    help="habilidades a gerar, separadas por virgula "
                         "(vazio = todas). Serve para completar o banco sem "
                         "regerar o que ja passou.")
    ap.add_argument("--por-skill", type=int, default=0,
                    help="quantos itens por habilidade (0 = o plano padrao)")
    ap.add_argument("--carregar", default="",
                    help="persiste um artefato JA gerado e reavaliado, sem "
                         "chamar IA. E por aqui que o banco e carregado: o "
                         "arquivo em scripts/data/ e a fonte, e foi ele que "
                         "o teste de vies mediu.")
    args = ap.parse_args()

    global PLANO
    if args.skills:
        alvos = [s.strip() for s in args.skills.split(",") if s.strip()]
        desconhecidas = [s for s in alvos if s not in HABILIDADES]
        if desconhecidas:
            raise SystemExit(f"habilidade desconhecida: {desconhecidas}")
        n = args.por_skill or 2
        PLANO = [(s, d) for s in alvos
                 for d in (["EASY", "MEDIUM", "MEDIUM", "HARD"] * 3)[:n]]

    if args.carregar:
        registros = json.loads(
            pathlib.Path(args.carregar).read_text(encoding="utf-8"))
        aprovados = [r for r in registros
                     if r["decisao"]["status"] == "AI_VERIFIED"]
        print(f"carregando {len(aprovados)} itens AI_VERIFIED de {args.carregar}")
        engine = create_async_engine(url())
        fabrica = async_sessionmaker(engine, class_=AsyncSession,
                                     expire_on_commit=False)
        try:
            async with fabrica() as s:
                ja = await s.scalar(select(ExamBooklet).where(
                    ExamBooklet.code == CADERNO))
                if ja is not None:
                    print("ja carregado: o caderno de Estequiometria existe.")
                    return
                caderno, revisao = await _edicao_propria(s)
                await s.flush()
                for n, r in enumerate(aprovados, start=1):
                    await persistir(s, ItemEstequiometria(**r["item"]),
                                    r["decisao"], caderno, revisao, n)
                await s.commit()
            print(f"persistidos {len(aprovados)} itens.")
        finally:
            await engine.dispose()
        return

    print(f"plano: {len(PLANO)} itens")
    for skill, dif in PLANO:
        print(f"  {skill:28} {dif}")
    print(f"\nchamadas de IA: {len(PLANO)} gerador + {len(PLANO)} verificador "
          f"= {len(PLANO) * 2}")
    if args.dry_run or not args.gerar:
        print("\n--dry-run: nada foi chamado.")
        return

    provider = build_text_provider()
    uso: dict = {}
    resultados = []
    for i, (skill, dif) in enumerate(PLANO, 1):
        try:
            item = await gerar_um(provider, skill, dif, uso)
            ver = await verificar_um(provider, item, uso)
            decisao = decidir_item(item, ver)
        except Exception as exc:  # noqa: BLE001 - item perdido nao derruba o lote
            print(f"  {i:2}/{len(PLANO)} {skill:28} ERRO: {exc}")
            continue
        marca = "OK " if decisao["status"] == "AI_VERIFIED" else "rev"
        calc = decisao["verificacao_calculo"]["detalhes"].get("valor_calculado")
        print(f"  {i:2}/{len(PLANO)} {skill:28} {dif:6} {marca} "
              f"{'calc=' + format(calc, '.4g') if calc is not None else ''}")
        if decisao["status"] != "AI_VERIFIED":
            for m in decisao["motivos"][:3]:
                print(f"        - {m}")
        resultados.append({"item": asdict(item), "decisao": decisao})

    pathlib.Path(args.saida).write_text(
        json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8")
    aprovados = [r for r in resultados if r["decisao"]["status"] == "AI_VERIFIED"]
    print(f"\ngerados {len(resultados)} | AI_VERIFIED {len(aprovados)} | "
          f"REQUIRES_REVIEW {len(resultados) - len(aprovados)}")
    print(f"chamadas: {uso}")
    print(f"artefato: {args.saida}")

    if not args.persistir:
        print("\nsem --persistir: nada foi gravado no banco.")
        return
    if not aprovados:
        print("\nnenhum item aprovado: nada a persistir.")
        return

    engine = create_async_engine(url())
    fabrica = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with fabrica() as s:
            caderno, revisao = await _edicao_propria(s)
            await s.flush()
            for n, r in enumerate(aprovados, start=1):
                await persistir(s, ItemEstequiometria(**r["item"]), r["decisao"],
                                caderno, revisao, n)
            await s.commit()
        print(f"persistidos {len(aprovados)} itens.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
