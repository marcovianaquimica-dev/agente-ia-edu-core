"""Varredura AI_VERIFIED sobre as questoes de Quimica do acervo.

    QUESTAO -> CLASSIFICADOR -> candidata -> VERIFICADOR -> contrato -> decisao

Responde a pergunta de combustivel do Piloto Zero: "zero questoes de
Balanceamento" e ausencia real no acervo, ou limitacao da busca lexical
anterior?

A DISTINCAO QUE ESTA VARREDURA EXISTE PARA FAZER
=================================================
    USA equacao balanceada como DADO      -> nao e Balanceamento
    AVALIA a habilidade de balancear      -> e Balanceamento

Uma questao que exibe `CaO + H2O -> Ca(OH)2` e pergunta sobre entalpia usa a
equacao; nao avalia balanceamento. A busca lexical anterior nao sabia separar
as duas coisas - por isso esta varredura e semantica.

AS DUAS ETAPAS SAO INDEPENDENTES
=================================
O verificador NAO recebe a justificativa do classificador, so o rotulo
candidato, a questao e a taxonomia. Duas etapas que leem o mesmo raciocinio
nao sao duas etapas.

Uso:
    .venv/bin/python scripts/varredura_ai_verified_quimica.py --dry-run
    .venv/bin/python scripts/varredura_ai_verified_quimica.py --executar [-n N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
import sys
from dataclasses import asdict

# O editable install do .venv aponta para o checkout PRINCIPAL. Sem isto, um
# script avulso rodado de um worktree importa o pacote da arvore errada - e
# passa, silenciosamente, testando outro codigo.
_RAIZ = pathlib.Path(__file__).resolve().parent.parent
_SRC = _RAIZ / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _carregar_env() -> None:
    """Nada neste projeto carrega .env automaticamente (ver tests/conftest.py).
    Sem isto o provedor de IA nasce desconfigurado num script avulso."""
    arquivo = _RAIZ / ".env"
    if not arquivo.exists():
        return
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        os.environ.setdefault(chave.strip(), valor.strip().strip("'\""))


_carregar_env()

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.providers.factory import build_text_provider
from agente_ia_edu.providers.models import TextGenerationRequest
from agente_ia_edu.services.classification_verification import (
    ClassificacaoCandidata,
    ContratoDeAprovacao,
    Veredito,
    decidir,
)

# v2: a v1 aprovou questoes de Matematica e Biologia como Quimica, porque as
# duas etapas so tinham codigos de Quimica para escolher e convergiram para a
# mesma resposta errada. A v2 oferece FORA_DO_ESCOPO e pergunta sobre figura.
CLASSIFICADOR_VERSAO = "quimica-classificador@2026-10-04-v2"
VERIFICADOR_VERSAO = "quimica-verificador@2026-10-04-v2"

# Recorte barato: so questoes com cara de quimica e mecanicamente completas.
# Reclassificar material obviamente irrelevante seria gastar a toa (s15).
MARCA_QUIMICA = (
    r"(rea[cç][aã]o|qu[ií]mic|mol\b|molar|solu[cç][aã]o aquosa|[aá]cido|"
    r"base forte|pH|oxida|redu[cç]|combust|H2O|CO2|NaOH|HCl|H2SO4|NH3|CaO|"
    r"equa[cç][aã]o)"
)

PROMPT_CLASSIFICADOR = """Você é um classificador curricular de questões de Química do Ensino Médio brasileiro.

Analise a questão e responda SOMENTE com JSON, sem cercas de código.

Taxonomia permitida (use exatamente estes códigos):
{taxonomia}

Se a questão NÃO for de Química, ou se nenhum código acima a representar,
responda primary = "FORA_DO_ESCOPO". É a resposta correta nesse caso, não uma
falha: forçar um código errado é pior que admitir que não cabe.

Formato:
{{"primary": "<código ou FORA_DO_ESCOPO>", "secondary": ["<código>", ...],
  "confidence": <0..1>, "avalia_balanceamento": <true|false>,
  "justificativa": "<2 frases>"}}

Regras:
- "primary" é o conteúdo que a questão REALMENTE avalia, não o que ela menciona.
- "secondary" só para conteúdos NECESSÁRIOS para resolver. Menção não basta.
- "avalia_balanceamento" só é true se o aluno precisar balancear uma equação,
  inferir coeficientes, completar uma equação ou aplicar conservação de átomos.
  Se a equação já vem balanceada e serve apenas de dado para outro cálculo,
  responda false.

QUESTÃO:
{questao}

ALTERNATIVAS:
{alternativas}
"""

PROMPT_VERIFICADOR = """Você é um verificador curricular independente de questões de Química.

Outro sistema propôs uma classificação. Você NÃO recebe o raciocínio dele.
Analise a questão por conta própria e decida.

Taxonomia permitida:
{taxonomia}

Se a questão NÃO for de Química, ou se nenhum código a representar, responda
primary = "FORA_DO_ESCOPO". Rejeitar a classificação proposta é esperado
quando ela não cabe.

Classificação proposta: primary={primary} secondary={secondary}

Responda SOMENTE com JSON, sem cercas de código:
{{"primary": "<código que VOCÊ concluiu>", "secondary": ["<código>", ...],
  "evidencias": ["<o que na questão sustenta sua conclusão>", ...],
  "integro": <true|false>, "problemas": ["<...>"],
  "depende_de_figura": <true|false>,
  "avalia_balanceamento": <true|false>}}

Sobre "depende_de_figura": true se o enunciado se referir a uma figura,
gráfico, tabela, esquema, imagem ou aparato ilustrado que NÃO esteja
transcrito no texto. O aluno receberia uma questão impossível de responder.

Sobre "integro": false quando o enunciado estiver truncado, as alternativas
não corresponderem ao enunciado, faltar dado essencial, ou a questão depender
de uma figura que não está no texto.

Sobre "avalia_balanceamento": true apenas se o aluno precisar efetivamente
balancear/inferir coeficientes. Equação já balanceada usada como dado é false.

QUESTÃO:
{questao}

ALTERNATIVAS:
{alternativas}
"""


def url() -> str:
    u = os.getenv("POSTGRES_USER", "agenteedu")
    p = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    d = os.getenv("POSTGRES_DB", "agente_ia_edu")
    return f"postgresql+psycopg://{u}:{p}@localhost:5433/{d}"


def limpo(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).strip()


def so_json(bruto: str) -> dict:
    t = (bruto or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.MULTILINE).strip()
    inicio, fim = t.find("{"), t.rfind("}")
    if inicio < 0 or fim <= inicio:
        raise ValueError(f"resposta sem JSON: {t[:120]!r}")
    return json.loads(t[inicio:fim + 1])


async def taxonomia_de_quimica(s: AsyncSession) -> dict[str, str]:
    linhas = (await s.execute(text(
        "select code, name from catalog_nodes where active and code like 'CHEMISTRY%' "
        "and node_type in ('CONTENT','SUBCONTENT') order by code"))).all()
    return {c: n for c, n in linhas}


async def candidatas(s: AsyncSession, limite: int | None) -> list[dict]:
    """Uma linha por questao DISTINTA, das duas origens (banco e extraidas)."""
    do_banco = (await s.execute(text(f"""
        select qv.id::text, 'BANCO' as origem,
               coalesce(qv.statement, qv.canonical_text) as txt,
               (select string_agg(o.option_key || ') ' || o.text, chr(10)
                                  order by o.position)
                  from question_options o where o.question_version_id = qv.id) as alts
        from question_versions qv
        where coalesce(qv.statement, qv.canonical_text) ~* :marca
          and (select count(*) from question_options o
                where o.question_version_id = qv.id) = 5
    """), {"marca": MARCA_QUIMICA})).all()

    extraidas = (await s.execute(text(f"""
        select distinct on (eq.fingerprint) eq.id::text, 'EXTRAIDA' as origem,
               coalesce(eq.reviewed_text, eq.normalized_text, eq.raw_text) as txt,
               (select string_agg(o.label || ') ' || o.text, chr(10)
                                  order by o.position)
                  from extracted_question_options o where o.question_id = eq.id) as alts
        from extracted_questions eq
        where coalesce(eq.reviewed_text, eq.normalized_text, eq.raw_text) ~* :marca
          and eq.review_status in ('VALIDATED','PUBLISHED','APPROVED')
          and (select count(*) from extracted_question_options o
                where o.question_id = eq.id) = 5
        order by eq.fingerprint
    """), {"marca": MARCA_QUIMICA})).all()

    vistos: set[str] = set()
    fora: list[dict] = []
    for qid, origem, txt, alts in list(do_banco) + list(extraidas):
        assinatura = limpo(txt)[:160].lower()
        if assinatura in vistos:
            continue
        vistos.add(assinatura)
        fora.append({"id": qid, "origem": origem,
                     "texto": limpo(txt)[:2600], "alternativas": limpo(alts or "")[:900]})
    return fora[:limite] if limite else fora


async def uma_questao(provider, taxonomia: dict[str, str], c: dict) -> dict:
    tax = "\n".join(f"  {k}  = {v}" for k, v in taxonomia.items())
    uso = {"in": 0, "out": 0, "chamadas": 0}

    async def chamar(prompt: str) -> dict:
        r = await provider.generate(TextGenerationRequest(prompt=prompt))
        uso["in"] += r.input_tokens or 0
        uso["out"] += r.output_tokens or 0
        uso["chamadas"] += 1
        return so_json(r.text)

    cls = await chamar(PROMPT_CLASSIFICADOR.format(
        taxonomia=tax, questao=c["texto"], alternativas=c["alternativas"]))
    # O verificador recebe o ROTULO, nunca a justificativa.
    ver = await chamar(PROMPT_VERIFICADOR.format(
        taxonomia=tax, primary=cls.get("primary"), secondary=cls.get("secondary"),
        questao=c["texto"], alternativas=c["alternativas"]))

    candidata = ClassificacaoCandidata(
        question_version_id=c["id"], primary=str(cls.get("primary") or ""),
        secondary=[str(x) for x in (cls.get("secondary") or [])],
        confidence=float(cls.get("confidence") or 0),
        classifier_version=CLASSIFICADOR_VERSAO)
    veredito = Veredito(
        primary=str(ver.get("primary") or ""),
        secondary=[str(x) for x in (ver.get("secondary") or [])],
        evidencias=[str(x) for x in (ver.get("evidencias") or [])],
        verifier_version=VERIFICADOR_VERSAO,
        integro=bool(ver.get("integro", True)),
        problemas=[str(x) for x in (ver.get("problemas") or [])],
        depende_de_figura=bool(ver.get("depende_de_figura", False)))

    decisao = decidir(candidata, veredito, taxonomia=set(taxonomia),
                      contrato=ContratoDeAprovacao.default())
    # "avalia balanceamento" tambem exige concordancia: uma etapa so nao basta.
    balanceamento = bool(cls.get("avalia_balanceamento")) and \
        bool(ver.get("avalia_balanceamento"))
    return {"candidata": c, "classificador": cls, "verificador": ver,
            "decisao": asdict(decisao), "avalia_balanceamento": balanceamento,
            "uso": uso}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--executar", action="store_true")
    ap.add_argument("-n", type=int, default=None)
    ap.add_argument("--saida", default="/tmp/varredura_quimica.json")
    args = ap.parse_args()

    engine = create_async_engine(url())
    fabrica = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with fabrica() as s:
            taxonomia = await taxonomia_de_quimica(s)
            lista = await candidatas(s, args.n)
            print(f"taxonomia de Quimica: {len(taxonomia)} conteudos")
            print(f"candidatas distintas: {len(lista)}")
            chars = sum(len(c["texto"]) + len(c["alternativas"]) for c in lista)
            print(f"caracteres a enviar: ~{chars:,}  (~{chars // 4:,} tokens)")
            print(f"chamadas previstas: {len(lista) * 2} (classificador + verificador)")
            if not args.executar:
                print("\n--dry-run: nada foi chamado.")
                return

        provider = build_text_provider()
        resultados = []
        total = {"in": 0, "out": 0, "chamadas": 0}
        for i, c in enumerate(lista, 1):
            try:
                r = await uma_questao(provider, taxonomia, c)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i}/{len(lista)}] {c['id'][:8]} ERRO: {exc}")
                continue
            resultados.append(r)
            for k in total:
                total[k] += r["uso"][k]
            d = r["decisao"]
            marca = "AI_VERIFIED " if not d["requires_review"] else "revisao    "
            bal = " BALANCEAMENTO" if r["avalia_balanceamento"] else ""
            print(f"  [{i}/{len(lista)}] {c['id'][:8]} {c['origem']:<9} "
                  f"{marca} {d['primary']}{bal}")

        with open(args.saida, "w", encoding="utf-8") as fh:
            json.dump(resultados, fh, ensure_ascii=False, indent=1)
        print(f"\nresultados: {args.saida}")
        print(f"chamadas: {total['chamadas']}  "
              f"tokens in={total['in']:,} out={total['out']:,}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
