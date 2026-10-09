"""Amostra ALEATORIA SIMPLES entre as questoes que o MVP_IMPORT_GATE aprova.

PROTOTIPO EXPERIMENTAL. Nao abre banco, nao importa nada, nao chama IA.

Diferencas em relacao a amostra estratificada anterior, pedidas pelo usuario:

  1. aleatoria simples, sobre a populacao do que o MVP realmente importaria.
     A amostra estratificada anterior foi enriquecida com casos dificeis e
     NAO pode estimar precisao populacional;
  2. cegamento reforcado: a planilha nao mostra ano, dia, versao nem origem.
     O caderno aparece com um codigo embaralhado (CADERNO_A..CADERNO_L) e os
     PDFs sao entregues com o mesmo codigo, para que o adjudicador consiga
     localizar o item sem saber de que ano ele e.

O numero do item (1-180) permanece visivel porque sem ele nao ha como achar
a questao na pagina - e informacao necessaria para avaliar, nao pista de
qualidade.

NENHUM JULGAMENTO E PREENCHIDO.

Uso:
    python tests/manual/enem_mvp_amostra_aleatoria.py --saida <dir> [-n 60]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
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

MANIFESTO = RAIZ / "tests/manual/phase10_enem_manifest_2016_2025.json"
ALVO = [(a, d) for a in range(2020, 2026) for d in (1, 2)]
SEMENTE = 20261003
PADRAO_TAMANHO = 60

CRITERIOS = (
    ("A_numero", "O numero do item esta correto?"),
    ("B_enunciado", "O enunciado esta completo, sem faltar trecho?"),
    ("C_alternativas", "As cinco alternativas estao completas?"),
    ("D_ordem", "A ordem A-B-C-D-E esta correta?"),
    ("E_sem_lixo", "Esta livre de texto estranho agregado?"),
    ("F_visual", "O elemento visual necessario foi identificado?"),
    ("G_gabarito", "O gabarito esta corretamente associado?"),
)

_CONTROLE = re.compile(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]")


def para_planilha(texto: str, limite: int = 2000) -> str:
    return _CONTROLE.sub("¤", texto or "")[:limite]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--saida", required=True, help="diretorio de entrega")
    p.add_argument("-n", type=int, default=PADRAO_TAMANHO)
    p.add_argument("--semente", type=int, default=SEMENTE,
                   help="sorteio independente; trocar invalida a comparacao direta")
    args = p.parse_args()
    destino = Path(args.saida)
    destino.mkdir(parents=True, exist_ok=True)

    manifesto = {(e["exam_year"], e["exam_day"]): e
                 for e in json.loads(MANIFESTO.read_text())["booklets"]}

    aleatorio = random.Random(args.semente)

    # codigo embaralhado de caderno, para cegar o ano
    codigos = [f"CADERNO_{chr(ord('A') + i)}" for i in range(len(ALVO))]
    embaralhados = list(ALVO)
    aleatorio.shuffle(embaralhados)
    codigo_de = {chave: codigos[i] for i, chave in enumerate(embaralhados)}

    populacao = []
    for ano, dia in ALVO:
        e = manifesto[(ano, dia)]
        print(f"... {ano} D{dia}", flush=True)
        r = extrair_caderno(RAIZ / e["proof_pdf"], RAIZ / e["answer_key_pdf"],
                            ano=ano, dia=dia, caderno=e["booklet"])
        faixa = (1, 90) if dia == 1 else (91, 180)
        for q in r.questoes:
            if not gate.avaliar(q, faixa=faixa).aprovado:
                continue
            populacao.append({
                "ano": ano, "dia": dia, "numero": q.numero,
                "codigo": codigo_de[(ano, dia)], "pagina": q.pagina_inicio,
                "enunciado": q.enunciado, "gabarito": q.gabarito or "",
                "alternativas": q.alternativas_texto or "",
                "ativos": len(q.ativos),
            })

    n = min(args.n, len(populacao))
    amostra = aleatorio.sample(populacao, n)
    aleatorio.shuffle(amostra)

    # PDFs com o mesmo codigo embaralhado
    pasta_pdf = destino / "CADERNOS"
    pasta_pdf.mkdir(exist_ok=True)
    usados = {linha["codigo"] for linha in amostra}
    for (ano, dia), codigo in codigo_de.items():
        if codigo not in usados:
            continue
        shutil.copy2(RAIZ / manifesto[(ano, dia)]["proof_pdf"],
                     pasta_pdf / f"{codigo}_PROVA.pdf")
        shutil.copy2(RAIZ / manifesto[(ano, dia)]["answer_key_pdf"],
                     pasta_pdf / f"{codigo}_GABARITO.pdf")

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    livro = Workbook()
    folha = livro.active
    folha.title = "INSTRUCOES"
    texto = [
        ("ADJUDICACAO CEGA - AMOSTRA ALEATORIA SIMPLES", True),
        ("", False),
        ("ARQUIVO RESTRITO. Contem texto de prova oficial do INEP.", True),
        ("", False),
        ("O que esta sendo medido:", True),
        ("A precisao das questoes que o sistema importaria automaticamente.", False),
        (f"Populacao: {len(populacao)} questoes aprovadas pelo portao.", False),
        (f"Amostra: {n} sorteadas ao acaso, sem estratificar.", False),
        ("", False),
        ("O que fazer:", True),
        ("1. Abra o PDF indicado na coluna 'caderno', na pasta CADERNOS/.", False),
        ("2. Va a pagina indicada e ache o item pelo numero.", False),
        ("3. Compare o impresso com a reconstrucao da planilha.", False),
        ("4. Preencha A a G com SIM, NAO ou NA.", False),
        ("5. Use OBSERVACAO para o que nao couber em SIM/NAO.", False),
        ("", False),
        ("Cegamento:", True),
        ("- A planilha nao diz o ano da prova nem a versao do extrator.", False),
        ("- Os cadernos tem codigo embaralhado de proposito.", False),
        ("- Julgue o que esta escrito, nao o que deveria estar.", False),
        ("- Em duvida, escreva a duvida em OBSERVACAO em vez de chutar.", False),
        ("", False),
        ("Os sete criterios:", True),
    ]
    for chave, pergunta in CRITERIOS:
        texto.append((f"   {chave}: {pergunta}", False))
    texto += [("", False),
              (f"Amostragem deterministica, semente {args.semente}.", False)]
    for i, (linha, negrito) in enumerate(texto, start=1):
        folha.cell(row=i, column=1, value=linha).font = Font(bold=negrito)
    folha.column_dimensions["A"].width = 95

    folha = livro.create_sheet("AMOSTRA")
    cabecalho = (["id", "caderno", "pagina", "numero_item",
                  "enunciado_reconstruido", "alternativas_reconstruidas",
                  "gabarito_associado", "elemento_visual_detectado"]
                 + [c for c, _ in CRITERIOS] + ["OBSERVACAO"])
    folha.append(cabecalho)
    fundo = PatternFill("solid", fgColor="FFF2CC")
    for coluna in range(1, len(cabecalho) + 1):
        celula = folha.cell(row=1, column=coluna)
        celula.font = Font(bold=True)
        celula.alignment = Alignment(wrap_text=True, vertical="top")
        if cabecalho[coluna - 1] in {c for c, _ in CRITERIOS} | {"OBSERVACAO"}:
            celula.fill = fundo

    mapa = []
    for i, linha in enumerate(amostra, start=1):
        ident = f"R{i:03d}"
        folha.append([ident, linha["codigo"], linha["pagina"], linha["numero"],
                      para_planilha(linha["enunciado"]),
                      para_planilha(linha["alternativas"]),
                      linha["gabarito"] or "(nenhum)",
                      "sim" if linha["ativos"] else "nao"]
                     + [""] * (len(CRITERIOS) + 1))
        mapa.append({"id": ident, "ano": linha["ano"], "dia": linha["dia"],
                     "numero": linha["numero"], "codigo": linha["codigo"]})

    for coluna, largura in (("A", 7), ("B", 12), ("C", 8), ("D", 12),
                            ("E", 62), ("F", 62), ("G", 10), ("H", 12)):
        folha.column_dimensions[coluna].width = largura
    for coluna in "IJKLMNOP":
        folha.column_dimensions[coluna].width = 13
    for linha_idx in range(2, n + 2):
        for coluna in range(1, len(cabecalho) + 1):
            folha.cell(row=linha_idx, column=coluna).alignment = Alignment(
                wrap_text=True, vertical="top")

    planilha = destino / "ADJUDICACAO_MVP_ALEATORIA_RESTRITO.xlsx"
    livro.save(planilha)
    chave = destino / "CHAVE_NAO_ABRIR_ADJUDICACAO_MVP.json"
    chave.write_text(json.dumps(
        {"semente": args.semente, "populacao": len(populacao), "n": n,
         "codigo_de_caderno": {f"{a} D{d}": c for (a, d), c in codigo_de.items()},
         "mapa": mapa}, ensure_ascii=False, indent=1))

    print()
    print(f"populacao (aprovadas pelo portao): {len(populacao)}")
    print(f"amostra aleatoria simples        : {n}")
    print(f"planilha : {planilha}")
    print(f"cadernos : {pasta_pdf}  ({len(list(pasta_pdf.iterdir()))} PDFs)")
    print(f"chave    : {chave}   <- NAO abrir antes de preencher")
    import math
    meia = 1.96 * math.sqrt(0.25 / n) * 100
    print(f"\nprecisao esperada: +/- {meia:.0f} pontos percentuais "
          f"(95%, pior caso p=0,5)")


if __name__ == "__main__":
    main()
