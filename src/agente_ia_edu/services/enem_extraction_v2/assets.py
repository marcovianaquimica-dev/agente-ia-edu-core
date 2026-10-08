"""Deteccao e associacao de elementos visuais (C5). PROTOTIPO EXPERIMENTAL.

O que a V1 faz, e por que nao serve:

    if not images and visual_reference.search(page_texts[page - 1]):
        assets.append(ParsedAsset("PAGE_REGION", ...))

Dois fatos medidos nos 12 cadernos:

  1. ``pypdf`` ``page.images`` devolve **ZERO** em todas as 384 paginas. O
     caminho de imagem real nunca executa.
  2. Portanto 100% dos ativos registrados sao ``PAGE_REGION``, disparados por
     a palavra *figura/grafico/tabela/esquema* aparecer em qualquer lugar da
     pagina. 126 paginas disparam e 367 itens sao retidos para revisao.

Nos mesmos arquivos o PyMuPDF encontra **809 imagens raster** e **64.115
objetos de desenho**.

E 64.115 desenhos NAO sao 64.115 figuras. Medido:

    area < 1 pt^2 ............ 34.269 (53,4%)
    area < 4 pt^2 ............  8.826 (13,8%)
    mais fino que 3pt num lado  49.344 (77,0%)

Um trato de hachura, uma regua de tabela e um ponto de serifa sao todos
"desenhos". Por isso a V2 **nao conta objetos**: ela rasteriza a ocupacao
grafica da pagina numa grade grossa, acha componentes conexos e so entao
aplica um piso de tamanho - sobre o AGLOMERADO, nunca sobre a primitiva.
Assim mil tracinhos de um grafico viram um ativo, e mil tracinhos espalhados
pela pagina nao viram nenhum.

Nada disto afirma relevancia pedagogica. Produz CANDIDATO com geometria,
metodo e confianca; quem decide se o elemento e necessario ao item e a
adjudicacao humana.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .contracts import (
    ASSOC_CONTIDO,
    ASSOC_MESMA_PAGINA,
    ASSOC_NENHUMA,
    ASSOC_SOBREPOSTO,
    ATIVO_RASTER,
    ATIVO_VETORIAL,
    AssetCandidate,
    Caixa,
)

# --- parametros, cada um com o fato que o justifica ----------------------

# Lado da celula da grade de ocupacao, em pontos. Define o maior vao que
# ainda une dois tracos no mesmo ativo. 6 pt ~ 2 mm, abaixo da altura de uma
# linha de corpo de texto do caderno (~9-10 pt): tracos de uma mesma figura
# se unem, linhas de texto vizinhas nao.
CELULA_PT = 6.0

# Piso de area do AGLOMERADO. 2.000 pt^2 ~ 45x45 pt ~ 1,6 x 1,6 cm impressos.
# Abaixo disso nao ha figura legivel em prova impressa.
AREA_MINIMA_AGLOMERADO = 2000.0
LADO_MINIMO_AGLOMERADO = 25.0

# Aglomerado que cobre quase a pagina inteira e fundo/moldura, nao figura.
FRACAO_MAXIMA_DA_PAGINA = 0.80

# Imagem raster minuscula e icone/filete, nao ilustracao.
AREA_MINIMA_RASTER = 400.0

PADRAO_ANCORA = re.compile(r"^[ \t]*Quest[ãa]o[ \t]+(\d{1,3})\b", re.IGNORECASE)


@dataclass(frozen=True)
class RegiaoDoItem:
    numero: int
    pagina: int
    partes: tuple[Caixa, ...]

    def contem_centro(self, caixa: Caixa) -> bool:
        cx, cy = (caixa.x0 + caixa.x1) / 2, (caixa.y0 + caixa.y1) / 2
        return any(p.x0 <= cx <= p.x1 and p.y0 <= cy <= p.y1 for p in self.partes)

    def area_intersecao(self, caixa: Caixa) -> float:
        return sum(p.area_intersecao(caixa) for p in self.partes)


def _componentes(celulas: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    """Componentes conexos por vizinhanca de 4, iterativo (sem recursao)."""
    restantes = set(celulas)
    saida = []
    while restantes:
        semente = restantes.pop()
        grupo = {semente}
        pilha = [semente]
        while pilha:
            cx, cy = pilha.pop()
            for vz in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if vz in restantes:
                    restantes.discard(vz)
                    grupo.add(vz)
                    pilha.append(vz)
        saida.append(grupo)
    return saida


def ativos_da_pagina(pagina, numero_pagina: int) -> list[AssetCandidate]:
    """Candidatos a ativo numa pagina PyMuPDF."""
    saida: list[AssetCandidate] = []
    area_pagina = pagina.rect.width * pagina.rect.height

    # 1. imagens raster, com a caixa de onde foram desenhadas
    for info in pagina.get_images(full=True):
        xref = info[0]
        try:
            caixas = pagina.get_image_rects(xref)
        except Exception:
            caixas = []
        for retangulo in caixas:
            caixa = Caixa(retangulo.x0, retangulo.y0, retangulo.x1, retangulo.y1)
            if caixa.area < AREA_MINIMA_RASTER:
                continue
            if caixa.area > area_pagina * FRACAO_MAXIMA_DA_PAGINA:
                continue
            saida.append(AssetCandidate(
                pagina=numero_pagina, caixa=caixa, tipo=ATIVO_RASTER,
                metodo_deteccao="pymupdf.get_images+get_image_rects",
                n_primitivas=1, identificador=f"xref:{xref}"))

    # 2. ocupacao vetorial numa grade, depois componentes conexos
    celulas: dict[tuple[int, int], int] = {}
    for desenho in pagina.get_drawings():
        r = desenho["rect"]
        if r.width <= 0 and r.height <= 0:
            continue
        if r.width * r.height > area_pagina * FRACAO_MAXIMA_DA_PAGINA:
            continue  # moldura/fundo
        for gx in range(int(r.x0 // CELULA_PT), int(r.x1 // CELULA_PT) + 1):
            for gy in range(int(r.y0 // CELULA_PT), int(r.y1 // CELULA_PT) + 1):
                celulas[(gx, gy)] = celulas.get((gx, gy), 0) + 1

    for grupo in _componentes(set(celulas)):
        xs = [c[0] for c in grupo]
        ys = [c[1] for c in grupo]
        caixa = Caixa(min(xs) * CELULA_PT, min(ys) * CELULA_PT,
                      (max(xs) + 1) * CELULA_PT, (max(ys) + 1) * CELULA_PT)
        if caixa.area < AREA_MINIMA_AGLOMERADO:
            continue
        if caixa.largura < LADO_MINIMO_AGLOMERADO or caixa.altura < LADO_MINIMO_AGLOMERADO:
            continue
        if caixa.area > area_pagina * FRACAO_MAXIMA_DA_PAGINA:
            continue
        saida.append(AssetCandidate(
            pagina=numero_pagina, caixa=caixa, tipo=ATIVO_VETORIAL,
            metodo_deteccao=f"pymupdf.get_drawings+grade{CELULA_PT:g}pt",
            n_primitivas=sum(celulas[c] for c in grupo),
            identificador=f"p{numero_pagina}:{int(caixa.x0)},{int(caixa.y0)}"))
    return saida


def regioes_da_pagina(pagina, numero_pagina: int) -> list[RegiaoDoItem]:
    """Regiao geometrica de cada item, respeitando as duas colunas."""
    largura, altura = pagina.rect.width, pagina.rect.height
    meio = largura / 2
    ancoras = []
    for bloco in pagina.get_text("blocks"):
        x0, y0, x1, y1, texto = bloco[0], bloco[1], bloco[2], bloco[3], bloco[4]
        achado = PADRAO_ANCORA.match((texto or "").lstrip())
        if achado:
            ancoras.append((int(achado.group(1)), 0 if x0 < meio else 1,
                            Caixa(x0, y0, x1, y1)))
    if not ancoras:
        return []
    # ordem de leitura: coluna, depois topo para baixo
    ancoras.sort(key=lambda a: (a[1], a[2].y0))
    colunas = {0: (0.0, meio), 1: (meio, largura)}
    regioes = []
    for indice, (numero, coluna, caixa) in enumerate(ancoras):
        seguinte = ancoras[indice + 1] if indice + 1 < len(ancoras) else None
        cx0, cx1 = colunas[coluna]
        if seguinte is not None and seguinte[1] == coluna:
            partes = (Caixa(cx0, caixa.y0, cx1, seguinte[2].y0),)
        else:
            partes = (Caixa(cx0, caixa.y0, cx1, altura),)
            if seguinte is not None:
                sx0, sx1 = colunas[seguinte[1]]
                partes = partes + (Caixa(sx0, 0.0, sx1, seguinte[2].y0),)
        regioes.append(RegiaoDoItem(numero, numero_pagina, partes))
    return regioes


def associar(ativos: list[AssetCandidate],
             regioes: list[RegiaoDoItem]) -> list[AssetCandidate]:
    """Liga cada ativo a um item por geometria, com confianca declarada."""
    saida = []
    for ativo in ativos:
        na_pagina = [r for r in regioes if r.pagina == ativo.pagina]
        escolhida, metodo, confianca = None, ASSOC_NENHUMA, 0.0
        for regiao in na_pagina:
            if regiao.contem_centro(ativo.caixa):
                escolhida, metodo, confianca = regiao, ASSOC_CONTIDO, 0.90
                break
        if escolhida is None:
            melhor, fracao = None, 0.0
            for regiao in na_pagina:
                area = regiao.area_intersecao(ativo.caixa)
                f = area / ativo.caixa.area if ativo.caixa.area else 0.0
                if f > fracao:
                    melhor, fracao = regiao, f
            if melhor is not None and fracao >= 0.5:
                escolhida, metodo, confianca = melhor, ASSOC_SOBREPOSTO, 0.60
            elif len(na_pagina) == 1:
                escolhida, metodo, confianca = na_pagina[0], ASSOC_MESMA_PAGINA, 0.30
        saida.append(AssetCandidate(
            pagina=ativo.pagina, caixa=ativo.caixa, tipo=ativo.tipo,
            metodo_deteccao=ativo.metodo_deteccao, n_primitivas=ativo.n_primitivas,
            questao=escolhida.numero if escolhida else None,
            metodo_associacao=metodo, confianca_associacao=confianca,
            identificador=ativo.identificador))
    return saida


# --- dependencia visual nao resolvida (politica do MVP) ------------------
#
# Uma figura ocupa espaco. Se a regiao geometrica do item tem uma faixa alta
# sem nenhuma linha de texto, alguma coisa esta desenhada ali. E propriedade
# do PDF, nao palavra no enunciado.
#
# Medido nos 12 cadernos, 985 itens com regiao geometrica:
#   espacamento entre linhas (p25) ...... 12 a 19 pt
#   mediana do maior buraco ............. 65 a 81 pt
#   itens com ativo associado ........... 476 de 985 (48,3%)
#
# A regra so dispara quando o buraco e grande E o detector de ativos NAO
# associou nada: o buraco sozinho nao serve, e o detector sozinho tambem nao.
#
#   buraco >=  60 pt -> 128 itens marcados (13,0%)
#   buraco >= 100 pt ->  68 itens marcados ( 6,9%)
#
# Escolhido 60 pt = cerca de 5x o espacamento de linha medido. A escolha NAO
# foi feita pela captura dos casos adjudicados: 60 e 100 capturam os mesmos
# 2 dos 3. Foi feita pela preferencia declarada do usuario - duvida relevante
# vai para revisao, nao para o banco.
#
# LIMITACAO MEDIDA E NAO ESCONDIDA: um dos tres casos adjudicados
# (2022 D2 item 135) tem buraco de 46 pt, abaixo da mediana, e NAO e
# alcancavel por este sinal. Ele e pego por outra regra
# (ULTIMA_ALTERNATIVA_ANOMALA), por coincidencia, nao por desenho.
BURACO_MINIMO_DE_FIGURA = 60.0
CONFIANCA_MINIMA_PARA_RESOLVER = 0.6


def maior_buraco_de_texto(pagina, regiao: RegiaoDoItem) -> float:
    """Maior faixa vertical sem linha de texto dentro da regiao do item."""
    maior = 0.0
    blocos = [b for b in pagina.get_text("blocks") if (b[4] or "").strip()]
    for parte in regiao.partes:
        linhas = []
        for x0, y0, x1, y1, *_ in blocos:
            if not (parte.x0 <= (x0 + x1) / 2 <= parte.x1):
                continue
            if y1 < parte.y0 or y0 > parte.y1:
                continue
            linhas.append((max(y0, parte.y0), min(y1, parte.y1)))
        linhas.sort()
        cursor = parte.y0
        for a, b in linhas:
            maior = max(maior, a - cursor)
            cursor = max(cursor, b)
        maior = max(maior, parte.y1 - cursor)
    return maior


def dependencia_visual_nao_resolvida(buraco: float,
                                     ativos: list[AssetCandidate]) -> bool:
    """Ha indicio de figura e o sistema nao a associou com seguranca."""
    if buraco < BURACO_MINIMO_DE_FIGURA:
        return False
    return not any(a.confianca_associacao >= CONFIANCA_MINIMA_PARA_RESOLVER
                   for a in ativos)
