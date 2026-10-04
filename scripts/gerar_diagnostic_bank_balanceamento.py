"""Gera e verifica os itens do NUCLEO DIAGNOSTIC BANK v1 - Balanceamento.

    OBJETIVO -> GERADOR -> item candidato -> VERIFICADOR -> CONTRATO -> decisao

O VERIFICADOR RESOLVE A QUESTAO
================================
Ele NAO recebe o gabarito nem a justificativa do gerador. Recebe enunciado e
alternativas, resolve por conta propria, e so entao comparamos. Perguntar "o
gabarito D esta certo?" convida a concordar; perguntar "qual e a resposta?"
obriga a calcular.

E A QUIMICA NAO DEPENDE DE NENHUM DOS DOIS
===========================================
O gerador declara, para cada equacao do item, se ela deveria estar
balanceada. `chemistry_balance` confere por contagem de atomos. Um item cuja
quimica nao bate com a propria declaracao e rejeitado, por mais convincente
que o texto seja - e esse e o erro que um segundo LLM tambem cometeria.

Uso:
    .venv/bin/python scripts/gerar_diagnostic_bank_balanceamento.py --dry-run
    .venv/bin/python scripts/gerar_diagnostic_bank_balanceamento.py --gerar
    .venv/bin/python scripts/gerar_diagnostic_bank_balanceamento.py --gerar --persistir
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
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
    CatalogNode, PedagogicalClassification, Question, QuestionOption,
    QuestionVersion,
)
from agente_ia_edu.db.models.catalog import ContentQuestionLink  # noqa: E402
from agente_ia_edu.providers.factory import build_text_provider  # noqa: E402
from agente_ia_edu.providers.models import TextGenerationRequest  # noqa: E402
from agente_ia_edu.services.diagnostic_bank import (  # noqa: E402
    BANK_TAG, CONTENT_CODE, HABILIDADES, LETRAS, ORIGIN_TYPE, ItemCandidato,
    conferir_estrutura, conferir_quimica,
)

GERADOR_VERSAO = "balanceamento-gerador@2026-10-04-v2"
VERIFICADOR_VERSAO = "balanceamento-verificador@2026-10-04-v2"
PROMPT_VERSAO = "v2"

# 2 itens por habilidade = 10. Dentro da faixa 8-12 pedida.
# 3 por habilidade = 15 gerados, para sobrar margem depois das rejeicoes.
PLANO = [(skill, dificuldade)
         for skill in HABILIDADES
         for dificuldade in ("EASY", "MEDIUM", "MEDIUM")]

PROMPT_GERADOR = """Você é um professor de Química do Ensino Médio brasileiro criando um item de MICRODIAGNÓSTICO.

OBJETIVO DIAGNÓSTICO: {objetivo}
HABILIDADE: {skill}
DIFICULDADE: {dificuldade}

O item serve para descobrir se o aluno tem base de BALANCEAMENTO DE EQUAÇÕES
antes de estudar Estequiometria. Não é prova, não vale nota.

Regras obrigatórias:
- item ORIGINAL, seu. NÃO copie nem parafraseie questão de vestibular.
- curto: enunciado de no máximo 3 linhas.
- autocontido: todos os dados no texto.
- SEM figura, gráfico, tabela ou esquema. O aluno lê só texto.
- exatamente 5 alternativas (A a E) e UMA única correta.
- as 4 erradas devem ser plausíveis, não absurdas.
- use equações químicas REAIS e quimicamente válidas.

Responda SOMENTE com JSON, sem cercas de código:
{{"stem": "<enunciado>",
  "options": {{"A": "...", "B": "...", "C": "...", "D": "...", "E": "..."}},
  "correct_answer": "<A-E>",
  "rationale": "<por que essa é a correta, 2 frases>",
  "equacoes": ["<cada equação que aparece no item, com coeficientes>"],
  "equacoes_balanceadas": [<true|false para cada equação, na mesma ordem>]}}

Sobre "equacoes" e "equacoes_balanceadas": liste as equações do item e diga,
para cada uma, se ela está balanceada. Isso será conferido por CONTAGEM DE
ÁTOMOS. Se você disser que está balanceada e não estiver, o item é descartado.

IMPORTANTE: NÃO inclua equações com lacuna ("___", "__", "?", "x"). Se o
enunciado tem um coeficiente faltando, liste a equação JÁ COMPLETA, com o
coeficiente da resposta correta, e marque true. Uma equação com lacuna não
pode ser conferida por contagem.
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
  "mede_balanceamento": <true|false>,
  "da_para_acertar_sem_saber_balanceamento": <true|false>,
  "problemas": ["<...>"]}}

Sobre "mede_balanceamento": true só se o aluno precisar balancear, conferir
coeficientes ou aplicar conservação de átomos para responder.

Sobre "da_para_acertar_sem_saber_balanceamento": true se der para acertar por
eliminação, pelo formato das alternativas ou por conhecimento alheio ao tema.
"""


def so_json(bruto: str) -> dict:
    t = re.sub(r"^```(?:json)?|```$", "", (bruto or "").strip(),
               flags=re.MULTILINE).strip()
    i, f = t.find("{"), t.rfind("}")
    if i < 0 or f <= i:
        raise ValueError(f"resposta sem JSON: {t[:120]!r}")
    return json.loads(t[i:f + 1])


def url() -> str:
    u = os.getenv("POSTGRES_USER", "agenteedu")
    p = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    d = os.getenv("POSTGRES_DB", "agente_ia_edu")
    return f"postgresql+psycopg://{u}:{p}@localhost:5433/{d}"


def texto_da_questao(item: ItemCandidato) -> str:
    alts = "\n".join(f"{k}) {item.options[k]}" for k in LETRAS if k in item.options)
    return f"{item.stem}\n\n{alts}"


async def gerar_um(provider, skill: str, dificuldade: str, uso: dict) -> ItemCandidato:
    r = await provider.generate(TextGenerationRequest(
        prompt=PROMPT_GERADOR.format(objetivo=HABILIDADES[skill], skill=skill,
                                     dificuldade=dificuldade)))
    uso["in"] += r.input_tokens or 0
    uso["out"] += r.output_tokens or 0
    uso["chamadas"] += 1
    d = so_json(r.text)
    return ItemCandidato(
        diagnostic_skill=skill, diagnostic_objective=HABILIDADES[skill],
        difficulty=dificuldade, stem=str(d.get("stem") or ""),
        options={str(k): str(v) for k, v in (d.get("options") or {}).items()},
        correct_answer=str(d.get("correct_answer") or ""),
        rationale=str(d.get("rationale") or ""),
        equacoes=[str(e) for e in (d.get("equacoes") or [])],
        equacoes_balanceadas=[bool(b) for b in (d.get("equacoes_balanceadas") or [])],
        generator_version=GERADOR_VERSAO)


async def verificar_um(provider, item: ItemCandidato, uso: dict) -> dict:
    r = await provider.generate(TextGenerationRequest(
        prompt=PROMPT_VERIFICADOR.format(questao=texto_da_questao(item))))
    uso["in"] += r.input_tokens or 0
    uso["out"] += r.output_tokens or 0
    uso["chamadas"] += 1
    return so_json(r.text)


def decidir_item(item: ItemCandidato, ver: dict) -> dict:
    """O contrato do item. Estrutural, fail-closed, sem limiar de confianca."""
    motivos: list[str] = []

    motivos.extend(conferir_estrutura(item))

    quimica = conferir_quimica(item)
    if not quimica.conferiu:
        motivos.extend(f"quimica: {p}" for p in quimica.problemas)

    # O verificador resolveu a questao. Se chegou a outra letra, ou o item
    # esta errado ou e ambiguo - nos dois casos nao se aprova.
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
    if not ver.get("mede_balanceamento", False):
        motivos.append("o item nao mede balanceamento")
    if ver.get("da_para_acertar_sem_saber_balanceamento"):
        motivos.append("da para acertar sem saber balanceamento")
    for p in (ver.get("problemas") or []):
        motivos.append(f"verificador: {p}")

    aprovado = not motivos
    return {
        "status": "AI_VERIFIED" if aprovado else "REQUIRES_REVIEW",
        "motivos": motivos or ["estrutura ok, quimica conferida por contagem de "
                               "atomos, verificador resolveu e chegou na mesma "
                               "resposta, mede balanceamento"],
        "verificacao_quimica": asdict(quimica),
        "verificador": ver,
    }


async def persistir(s: AsyncSession, item: ItemCandidato, decisao: dict) -> str:
    """Grava como Question/QuestionVersion/QuestionOption comuns.

    Nenhuma tabela nova: `origin_type='GENERATED'` ja existia no CHECK e o
    contrato diagnostico cabe em `metadata`.
    """
    agora = datetime.now(timezone.utc).isoformat()
    proveniencia = {
        "bank": BANK_TAG,
        "diagnostic_skill": item.diagnostic_skill,
        "diagnostic_objective": item.diagnostic_objective,
        "generation": {"actor_type": "AI", "version": item.generator_version,
                       "prompt_version": PROMPT_VERSAO, "at": agora},
        "verification": {"actor_type": "AI", "version": VERIFICADOR_VERSAO,
                         "prompt_version": PROMPT_VERSAO, "at": agora,
                         "deterministic_chemistry": decisao["verificacao_quimica"],
                         "solved_independently": decisao["verificador"].get("resposta")},
        "status": decisao["status"],
        "motivos": decisao["motivos"],
        "rationale": item.rationale,
        "equacoes": item.equacoes,
    }

    q = Question(validation_status="valid", origin_type=ORIGIN_TYPE,
                 status="PUBLISHED", visibility_scope="PUBLIC",
                 question_type="MULTIPLE_CHOICE",
                 created_by_external_identity=BANK_TAG,
                 metadata_=proveniencia)
    s.add(q); await s.flush()
    v = QuestionVersion(question_id=q.id, version_kind="official_original",
                        canonical_text=texto_da_questao(item),
                        statement=item.stem,
                        content_hash=str(_uuid.uuid4()), is_immutable=True,
                        recommended_difficulty=item.difficulty)
    s.add(v); await s.flush()
    for pos, letra in enumerate(LETRAS, start=1):
        s.add(QuestionOption(question_version_id=v.id, option_key=letra,
                             position=pos, text=item.options[letra],
                             is_valid_option=(letra == item.correct_answer)))
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
    ap.add_argument("--saida", default="/tmp/diagnostic_bank_balanceamento.json")
    args = ap.parse_args()

    print(f"habilidades: {len(HABILIDADES)}")
    print(f"itens planejados: {len(PLANO)}  (2 por habilidade)")
    print(f"chamadas previstas: {len(PLANO) * 2} (gerador + verificador)")
    print(f"tokens estimados: ~{len(PLANO) * 2 * 700:,} entrada")
    if not args.gerar:
        print("\n--dry-run: nada foi chamado.")
        return

    provider = build_text_provider()
    uso = {"in": 0, "out": 0, "chamadas": 0}
    resultados = []
    for i, (skill, dificuldade) in enumerate(PLANO, 1):
        try:
            item = await gerar_um(provider, skill, dificuldade, uso)
            ver = await verificar_um(provider, item, uso)
            decisao = decidir_item(item, ver)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i}/{len(PLANO)}] {skill:<28} ERRO: {exc}")
            continue
        resultados.append({"item": asdict(item), "decisao": decisao})
        marca = "AI_VERIFIED" if decisao["status"] == "AI_VERIFIED" else "revisao    "
        print(f"  [{i}/{len(PLANO)}] {skill:<28} {dificuldade:<7} {marca} "
              f"resp={item.correct_answer}")
        if decisao["status"] != "AI_VERIFIED":
            print(f"        -> {decisao['motivos'][0][:110]}")

    with open(args.saida, "w", encoding="utf-8") as fh:
        json.dump(resultados, fh, ensure_ascii=False, indent=1)
    ok = [r for r in resultados if r["decisao"]["status"] == "AI_VERIFIED"]
    print(f"\ngerados={len(resultados)}  AI_VERIFIED={len(ok)}  "
          f"revisao={len(resultados) - len(ok)}")
    print(f"chamadas={uso['chamadas']} tokens in={uso['in']:,} out={uso['out']:,}")
    print(f"resultados: {args.saida}")

    if not args.persistir:
        print("\n(sem --persistir: nada foi gravado no banco)")
        return

    engine = create_async_engine(url())
    fabrica = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with fabrica() as s:
            gravados = []
            for r in ok:
                item = ItemCandidato(**r["item"])
                gravados.append(await persistir(s, item, r["decisao"]))
            await s.commit()
        print(f"\npersistidos: {len(gravados)} itens AI_VERIFIED")
        for g in gravados:
            print(f"  {g}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
