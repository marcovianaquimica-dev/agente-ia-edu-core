"""Funil do MVP sobre os 12 cadernos ENEM 2020-2025.

PROTOTIPO EXPERIMENTAL. Nao abre banco, nao importa nada, nao chama IA,
nao baixa nada. So le os PDFs ja presentes em var/inep-pilot/.

    1.080 itens oficiais
      -> detectados
      -> estruturalmente completos
      -> aprovados pelo MVP_IMPORT_GATE
      -> REQUIRES_REVIEW (com motivo)
      -> ilegiveis

Uso:
    python tests/manual/enem_v2_funil_mvp.py [--json SAIDA.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
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

from agente_ia_edu.services.enem_extraction_v2 import extrair_caderno, gate  # noqa: E402
from agente_ia_edu.services.enem_extraction_v2.contracts import (  # noqa: E402
    TEXTO_ILEGIVEL,
)

MANIFESTO = RAIZ / "tests/manual/phase10_enem_manifest_2016_2025.json"
ALVO = [(a, d) for a in range(2020, 2026) for d in (1, 2)]
ESPERADAS = 90


def rodar(ano: int, dia: int, entrada: dict) -> dict:
    r = extrair_caderno(RAIZ / entrada["proof_pdf"], RAIZ / entrada["answer_key_pdf"],
                        ano=ano, dia=dia, caderno=entrada["booklet"],
                        esperadas=ESPERADAS)
    faixa = (1, 90) if dia == 1 else (91, 180)
    aprovadas, revisao, ilegiveis = [], [], 0
    motivos: Counter = Counter()
    detalhes: Counter = Counter()
    vistos = set()
    for q in r.questoes:
        d = gate.avaliar(q, faixa=faixa)
        if TEXTO_ILEGIVEL in q.problemas:
            ilegiveis += 1
        if d.aprovado:
            aprovadas.append(q)
        else:
            revisao.append((q, d))
            motivos.update(d.motivos)
            detalhes.update(d.detalhes)
        vistos.add(q.numero)
    return {
        "ano": ano, "dia": dia, "caderno": entrada["booklet"],
        "motor": r.motor_escolhido,
        "esperadas": ESPERADAS,
        "detectadas": len(vistos),
        "extraidas": len(r.questoes),
        "estruturalmente_completas": sum(1 for q in r.questoes if q.tem_cinco_opcoes),
        "aprovadas": len(aprovadas),
        "revisao": len(revisao),
        "ilegiveis": ilegiveis,
        "motivos": dict(motivos),
        "detalhes": dict(detalhes),
        "numeros_aprovados": sorted({q.numero for q in aprovadas}),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--json", default="")
    args = p.parse_args()
    manifesto = {(e["exam_year"], e["exam_day"]): e
                 for e in json.loads(MANIFESTO.read_text())["booklets"]}

    linhas = []
    for ano, dia in ALVO:
        print(f"... {ano} D{dia}", flush=True)
        linhas.append(rodar(ano, dia, manifesto[(ano, dia)]))

    if args.json:
        Path(args.json).write_text(json.dumps(linhas, ensure_ascii=False, indent=1))

    print()
    cab = (f"{'ano':>5}{'dia':>4}{'esper':>7}{'detect':>8}{'compl':>7}"
           f"{'APROVADAS':>11}{'revisao':>9}{'ilegiv':>8}  motor")
    print(cab)
    print("-" * len(cab))
    for linha in linhas:
        print(f"{linha['ano']:>5}{linha['dia']:>4}{linha['esperadas']:>7}"
              f"{linha['detectadas']:>8}{linha['estruturalmente_completas']:>7}"
              f"{linha['aprovadas']:>11}{linha['revisao']:>9}{linha['ilegiveis']:>8}"
              f"  {linha['motor']}")
    print("-" * len(cab))
    soma = {k: sum(x[k] for x in linhas) for k in
            ("esperadas", "detectadas", "estruturalmente_completas",
             "aprovadas", "revisao", "ilegiveis")}
    print(f"{'TOTAL':>9}{soma['esperadas']:>7}{soma['detectadas']:>8}"
          f"{soma['estruturalmente_completas']:>7}{soma['aprovadas']:>11}"
          f"{soma['revisao']:>9}{soma['ilegiveis']:>8}")

    print()
    print("POR ANO")
    print(f"{'ano':>6}{'esper':>7}{'APROVADAS':>11}{'revisao':>9}{'%aprov':>9}")
    for ano in range(2020, 2026):
        do_ano = [x for x in linhas if x["ano"] == ano]
        e = sum(x["esperadas"] for x in do_ano)
        a = sum(x["aprovadas"] for x in do_ano)
        rv = sum(x["revisao"] for x in do_ano)
        print(f"{ano:>6}{e:>7}{a:>11}{rv:>9}{100*a/e:>8.1f}%")

    print()
    print("MOTIVOS DE REVISAO (um item pode ter mais de um)")
    motivos: Counter = Counter()
    detalhes: Counter = Counter()
    for linha in linhas:
        motivos.update(linha["motivos"])
        detalhes.update(linha["detalhes"])
    for motivo, n in motivos.most_common():
        print(f"  {n:>5}  {motivo}")
    print()
    print("DETALHE DO MOTIVO 'PROBLEMA_ESTRUTURAL'")
    for detalhe, n in detalhes.most_common():
        if detalhe.isupper():
            print(f"  {n:>5}  {detalhe}")

    print()
    print(f"RESULTADO: {soma['aprovadas']} de {soma['esperadas']} entrariam "
          f"automaticamente ({100*soma['aprovadas']/soma['esperadas']:.1f}%), "
          f"{soma['revisao']} ficam em REQUIRES_REVIEW.")


if __name__ == "__main__":
    main()
