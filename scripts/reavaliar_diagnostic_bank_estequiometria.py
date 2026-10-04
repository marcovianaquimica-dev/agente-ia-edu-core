"""Reaplica o CONTRATO sobre itens ja gerados, sem chamar IA de novo.

POR QUE ISTO EXISTE
===================
Dos 16 primeiros itens, 4 foram rejeitados por um defeito MEU: o parser nao
reconhecia estado fisico, e respondia "O2 nao esta na equacao: ['KCl(s)',
'KClO3(s)', 'O2(g)']". Era quimica correta recusada por notacao - o
fail-closed estava certo em recusar o que nao entendia, mas quem estava
estreito demais era o parser.

Regerar custaria 32 chamadas de IA para reproduzir itens que ja existem. O
veredito do verificador-LLM esta salvo no artefato, e ele nao muda: o que
mudou foi a aritmetica. Entao o contrato e reaplicado sobre o que ja temos.

A INDEPENDENCIA CONTINUA VALENDO
=================================
O veredito reaproveitado e o mesmo que o verificador deu SEM ver o gabarito.
Nada aqui o consulta de novo, nada aqui o ajusta. Só a parte deterministica e
recalculada - e e justamente a parte que nao depende de modelo nenhum.

Uso:
    PYTHONPATH=src .venv/bin/python scripts/reavaliar_diagnostic_bank_estequiometria.py \\
        --entrada /tmp/estequiometria_v1.json \\
        --saida scripts/data/diagnostic_bank_estequiometria_v1.json
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
from dataclasses import asdict

_RAIZ = pathlib.Path(__file__).resolve().parent.parent
if str(_RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(_RAIZ / "src"))

from agente_ia_edu.services.diagnostic_bank_estequiometria import (  # noqa: E402
    LETRAS, ItemEstequiometria,
)

sys.path.insert(0, str(_RAIZ / "scripts"))
from gerar_diagnostic_bank_estequiometria import decidir_item  # noqa: E402


def redistribuir(aprovados: list[dict]) -> list[dict]:
    """Espalha a resposta correta pelas cinco letras, deterministicamente.

    A mesma licao do banco de Balanceamento, aplicada ANTES da carga desta vez:
    la a correcao ficou no pipeline e o artefato que virou banco era anterior a
    ela, com 9 de 14 gabaritos em "B".

    So reordena alternativas - a quimica, o enunciado e o valor correto nao
    mudam. O item continua sendo o que foi verificado.
    """
    saida = []
    for posicao, registro in enumerate(aprovados):
        item = dict(registro["item"])
        alvo = LETRAS[posicao % len(LETRAS)]
        atual = item["correct_answer"]
        if alvo != atual:
            opcoes = dict(item["options"])
            opcoes[alvo], opcoes[atual] = item["options"][atual], item["options"][alvo]
            item["options"] = opcoes
            item["correct_answer"] = alvo
        saida.append({**registro, "item": item})
    return saida


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entrada", default="/tmp/estequiometria_v1.json")
    ap.add_argument("--saida",
                    default=str(_RAIZ / "scripts" / "data"
                               / "diagnostic_bank_estequiometria_v1.json"))
    args = ap.parse_args()

    bruto = json.loads(pathlib.Path(args.entrada).read_text(encoding="utf-8"))
    print(f"entrada: {len(bruto)} itens")

    resultados = []
    mudou = 0
    for registro in bruto:
        item = ItemEstequiometria(**registro["item"])
        antes = registro["decisao"]["status"]
        # O veredito do verificador-LLM e reaproveitado tal como foi dado.
        decisao = decidir_item(item, registro["decisao"]["verificador"])
        if decisao["status"] != antes:
            mudou += 1
            print(f"  {item.diagnostic_skill:28} {antes} -> {decisao['status']}")
        resultados.append({"item": asdict(item), "decisao": decisao})

    aprovados = [r for r in resultados if r["decisao"]["status"] == "AI_VERIFIED"]
    print(f"\nAI_VERIFIED {len(aprovados)} de {len(resultados)} "
          f"({mudou} mudaram de veredito)")

    antes_dist = collections.Counter(r["item"]["correct_answer"] for r in aprovados)
    aprovados = redistribuir(aprovados)
    depois_dist = collections.Counter(r["item"]["correct_answer"] for r in aprovados)
    print(f"gabarito antes : {dict(sorted(antes_dist.items()))}")
    print(f"gabarito depois: {dict(sorted(depois_dist.items()))}")

    rejeitados = [r for r in resultados if r["decisao"]["status"] != "AI_VERIFIED"]
    saida = pathlib.Path(args.saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    # O arquivo guarda os DOIS: aprovados e rejeitados, com os motivos. Quem
    # ler depois precisa poder ver o que NAO entrou, e por que.
    saida.write_text(json.dumps(aprovados + rejeitados, ensure_ascii=False,
                                indent=2), encoding="utf-8")
    print(f"\nartefato: {saida}")
    por_skill = collections.Counter(r["item"]["diagnostic_skill"] for r in aprovados)
    for skill, n in sorted(por_skill.items()):
        print(f"  {skill:28} {n}")


if __name__ == "__main__":
    main()
