"""Comparacao V1 x V2 sobre os 12 cadernos ENEM 2020-2025.

PROTOTIPO EXPERIMENTAL. Nao abre banco, nao escreve em producao, nao chama IA,
nao baixa nada. Le os PDFs ja presentes em ``var/inep-pilot/``.

A V1 e exercitada pelo seu proprio codigo (``PdfParser`` + ``apply_answer_key``
+ ``QuestionBankImporter._validate``), sem nenhuma alteracao. A V2 passa pelo
MESMO portao de importacao, para que "importaveis" signifique a mesma coisa
nos dois lados.

Uso:
    python tests/manual/enem_v2_compare.py [--json SAIDA.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
for _cand in (Path.cwd(), _AQUI.parents[1]):
    if (_cand / "src" / "agente_ia_edu").is_dir():
        sys.path.insert(0, str(_cand / "src"))
        RAIZ = _cand
        break
else:  # pragma: no cover
    RAIZ = _AQUI.parents[1]
    sys.path.insert(0, str(RAIZ / "src"))

from agente_ia_edu.services.enem_extraction_v2 import extrair_caderno  # noqa: E402
from agente_ia_edu.services.ingestion_parser import PdfParser  # noqa: E402
from agente_ia_edu.services.question_bank_importer import (  # noqa: E402
    QuestionBankImporter,
)

MANIFESTO = RAIZ / "tests/manual/phase10_enem_manifest_2016_2025.json"
ALVO = [(a, d) for a in range(2020, 2026) for d in (1, 2)]
ESPERADAS = 90


@dataclass
class DocDuble:
    document_hash: str = "x" * 64
    storage_uri: str = "var/inep-pilot/presente.pdf"
    metadata_: dict = field(default_factory=dict)


@dataclass
class ItemDuble:
    question_number: int | None
    statement_text: str
    alternatives_text: str | None
    correct_answer: str | None
    metadata_: dict


def passa_no_portao(numero, enunciado, alternativas, gabarito, revisao) -> str | None:
    item = ItemDuble(numero, enunciado, alternativas, gabarito,
                     {"requires_review": revisao})
    return QuestionBankImporter._validate(item, DocDuble())[0]


def rodar_v1(prova: Path, gabarito: Path) -> dict:
    analisado = PdfParser.parse_file(prova)
    chave = PdfParser.parse_answer_key(gabarito)
    PdfParser.apply_answer_key(analisado, chave)
    motivos: dict[str, int] = {}
    importaveis = 0
    for q in analisado.questions:
        motivo = passa_no_portao(q.question_number, q.statement_text,
                                 q.alternatives_text, q.correct_answer,
                                 q.requires_review)
        if motivo is None:
            importaveis += 1
        else:
            motivos[motivo] = motivos.get(motivo, 0) + 1
    cinco = sum(1 for q in analisado.questions
                if q.alternatives_text
                and len(q.alternatives_text.splitlines()) == 5)
    return {
        "detectadas": len({q.question_number for q in analisado.questions}),
        "extraidas": len(analisado.questions),
        "com_alternativas": cinco,
        "com_gabarito": sum(1 for q in analisado.questions if q.correct_answer),
        "com_ativo": sum(1 for q in analisado.questions
                         if any(a.question_number == q.question_number
                                for a in analisado.assets)),
        "ativos_detectados": len(analisado.assets),
        "requires_review": sum(1 for q in analisado.questions if q.requires_review),
        "importaveis": importaveis,
        "motivos": motivos,
        "numeros": sorted({q.question_number for q in analisado.questions}),
    }


def rodar_v2(prova: Path, gabarito: Path, ano: int, dia: int, caderno: str) -> tuple:
    r = extrair_caderno(prova, gabarito, ano=ano, dia=dia, caderno=caderno,
                        esperadas=ESPERADAS)
    motivos: dict[str, int] = {}
    importaveis = 0
    for q in r.questoes:
        motivo = passa_no_portao(q.numero, q.enunciado, q.alternativas_texto,
                                 q.gabarito, bool(q.problemas))
        if motivo is None:
            importaveis += 1
        else:
            motivos[motivo] = motivos.get(motivo, 0) + 1
    resumo = {
        "motor": r.motor_escolhido,
        "motivo_escolha": r.motivo_escolha,
        "detectadas": r.detectadas,
        "extraidas": len(r.questoes),
        "com_alternativas": r.com_cinco_opcoes,
        "com_gabarito": r.com_gabarito,
        "com_ativo": r.com_ativo,
        "ativos_detectados": sum(len(q.ativos) for q in r.questoes) + len(r.ativos_orfaos),
        "ativos_orfaos": len(r.ativos_orfaos),
        "requires_review": sum(1 for q in r.questoes if q.problemas),
        "importaveis": importaveis,
        "motivos": motivos,
        "gabarito_entradas": r.gabarito_entradas,
        "gabarito_ambiguos": r.gabarito_ambiguos,
        "gabarito_orfaos": len(r.gabarito_orfaos),
        "atravessam_pagina": sum(1 for q in r.questoes if q.atravessa_pagina),
        "avaliacao_motores": r.avaliacao_motores,
        "numeros": sorted({q.numero for q in r.questoes}),
        "avisos": r.avisos,
    }
    return resumo, r


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--json", default="")
    args = p.parse_args()

    manifesto = {(e["exam_year"], e["exam_day"]): e
                 for e in json.loads(MANIFESTO.read_text())["booklets"]}
    linhas = []
    for ano, dia in ALVO:
        e = manifesto[(ano, dia)]
        prova, gabarito = RAIZ / e["proof_pdf"], RAIZ / e["answer_key_pdf"]
        print(f"... {ano} D{dia}", flush=True)
        v1 = rodar_v1(prova, gabarito)
        v2, _ = rodar_v2(prova, gabarito, ano, dia, e["booklet"])
        linhas.append({"ano": ano, "dia": dia, "caderno": e["booklet"],
                       "esperadas": ESPERADAS, "v1": v1, "v2": v2})

    if args.json:
        Path(args.json).write_text(json.dumps(linhas, ensure_ascii=False, indent=1))
        print(f"\ngravado em {args.json}")

    cols = ("detectadas", "extraidas", "com_alternativas", "com_gabarito",
            "com_ativo", "requires_review", "importaveis")
    print()
    cab = f"{'ano':>5}{'dia':>4}{'esp':>5} | " + " | ".join(
        f"{c[:9]:>9}" for c in cols) + " | motor"
    print(cab)
    print("-" * len(cab))
    for versao in ("v1", "v2"):
        print(f"--- {versao.upper()} ---")
        for linha in linhas:
            d = linha[versao]
            print(f"{linha['ano']:>5}{linha['dia']:>4}{ESPERADAS:>5} | " +
                  " | ".join(f"{d[c]:>9}" for c in cols) +
                  f" | {d.get('motor', 'pypdf')}")
        print(f"{'TOTAL':>14} | " + " | ".join(
            f"{sum(l[versao][c] for l in linhas):>9}" for c in cols))
    print()
    print(f"{'ano':>5}{'dia':>4} | {'V1 import':>10}{'V2 import':>11}{'delta':>8}")
    for linha in linhas:
        a, b = linha["v1"]["importaveis"], linha["v2"]["importaveis"]
        print(f"{linha['ano']:>5}{linha['dia']:>4} | {a:>10}{b:>11}{b - a:>+8}")
    ta = sum(l["v1"]["importaveis"] for l in linhas)
    tb = sum(l["v2"]["importaveis"] for l in linhas)
    print(f"{'TOTAL':>9} | {ta:>10}{tb:>11}{tb - ta:>+8}   de {ESPERADAS*len(linhas)}")


if __name__ == "__main__":
    main()
