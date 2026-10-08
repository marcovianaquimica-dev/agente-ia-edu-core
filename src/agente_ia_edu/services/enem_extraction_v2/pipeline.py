"""Orquestracao da EXTRACAO ENEM V2. PROTOTIPO EXPERIMENTAL.

NENHUM MODULO DE PRODUCAO IMPORTA ESTE PACOTE, e ha um teste que falha se
isso mudar (``tests/test_enem_extraction_v2_isolamento.py``). A V1
(``services/ingestion_parser.py``) permanece intacta e e o baseline.

Nao abre banco, nao escreve arquivo, nao faz rede, nao chama IA.

Ordem de prioridade fixada pelo usuario e seguida aqui:
FIDELIDADE > COBERTURA > RASTREABILIDADE. Em toda decisao ambigua a V2
prefere marcar o item como incompleto a reconstrui-lo de forma duvidosa.
"""

from __future__ import annotations

from pathlib import Path

from . import answer_key, assets, fragmentation, options
from .contracts import (
    DEPENDENCIA_VISUAL_NAO_RESOLVIDA,
    ENUNCIADO_VAZIO,
    ALTERNATIVAS_INCOMPLETAS,
    FRAGMENTACAO_DE_PALAVRA_SUSPEITA,
    GABARITO_AUSENTE,
    GABARITO_AMBIGUO,
    MOTOR_PYMUPDF,
    MOTOR_PYPDF,
    NUMERO_DUPLICADO,
    TEXTO_ILEGIVEL,
    ULTIMA_ALTERNATIVA_ANOMALA,
    QuestionCandidate,
    ResultadoCaderno,
)
from .segmentation import segmentar
from .text_layer import escolher_motor, legibilidade, ler, texto_ilegivel


def _limpar(texto: str) -> str:
    return " ".join(texto.replace("\f", " ").split())


def avaliar_motor(caminho: Path, motor: str) -> dict:
    """Mede um motor sem decidir nada. Usado pelo portao de escolha.

    Mede COBERTURA DE CABECALHO **e** RECUPERACAO DE ALTERNATIVA. O portao da
    fase 10.6 media so legibilidade e cabecalho, e por isso aprovou 2022 e
    2023 como "native text passed all quality gates" - cadernos em que zero
    itens sao reconstruiveis. Um portao que aprova 0 de 90 mede a coisa errada.
    """
    camada = ler(caminho, motor)
    segmentos = segmentar(camada)
    completas = 0
    for segmento in segmentos:
        opcoes, _ = options.extrair(segmento.corpo)
        if len(opcoes) != 5:
            continue
        if any(texto_ilegivel(o.texto) for o in opcoes):
            continue  # cinco marcadores sobre lixo de codificacao nao contam
        completas += 1
    return {
        "motor": motor,
        "cabecalhos": len(segmentos),
        "cabecalhos_distintos": len({s.numero for s in segmentos}),
        "completas_estruturais": completas,
        "legibilidade": legibilidade(camada),
        "chars": len(camada.documento),
    }


def extrair_caderno(prova: Path, gabarito: Path, *, ano: int, dia: int,
                    caderno: str, esperadas: int = 90,
                    motor_forcado: str | None = None) -> ResultadoCaderno:
    avaliacoes = {m: avaliar_motor(prova, m) for m in (MOTOR_PYPDF, MOTOR_PYMUPDF)}
    if motor_forcado:
        motor, motivo = motor_forcado, "motor forcado pelo chamador"
    else:
        motor, motivo = escolher_motor(avaliacoes)

    camada = ler(prova, motor)
    segmentos = segmentar(camada)
    chave = answer_key.ler(gabarito)
    # vocabulario do PROPRIO caderno. Medido: por caderno da o mesmo
    # resultado que o corpus inteiro (33 itens marcados nos dois casos).
    vocabulario = fragmentation.vocabulario_limpo(camada.documento)

    resultado = ResultadoCaderno(
        ano=ano, dia=dia, caderno=caderno, motor_escolhido=motor,
        motivo_escolha=motivo, esperadas=esperadas,
        gabarito_entradas=len(chave), gabarito_ambiguos=list(chave.ambiguos),
        avaliacao_motores=avaliacoes,
    )

    # --- ativos e regioes, sempre por PyMuPDF (pypdf mede zero imagens) ---
    import pymupdf

    documento = pymupdf.open(prova)
    try:
        todos_ativos = []
        todas_regioes = []
        buraco_por_questao: dict[int, float] = {}
        for indice, pagina in enumerate(documento, start=1):
            brutos = assets.ativos_da_pagina(pagina, indice)
            regioes = assets.regioes_da_pagina(pagina, indice)
            todas_regioes.extend(regioes)
            todos_ativos.extend(assets.associar(brutos, regioes))
            for regiao in regioes:
                buraco = assets.maior_buraco_de_texto(pagina, regiao)
                buraco_por_questao[regiao.numero] = max(
                    buraco_por_questao.get(regiao.numero, 0.0), buraco)
    finally:
        documento.close()

    por_questao: dict[int, list] = {}
    for ativo in todos_ativos:
        if ativo.questao is None:
            resultado.ativos_orfaos.append(ativo)
        else:
            por_questao.setdefault(ativo.questao, []).append(ativo)

    # --- itens ---
    numeros_vistos: dict[int, int] = {}
    for segmento in segmentos:
        opcoes, forma = options.extrair(segmento.corpo)
        corte = options.inicio_das_alternativas(segmento.corpo, opcoes)
        enunciado = _limpar(segmento.corpo[:corte] if corte is not None else segmento.corpo)

        problemas: list[str] = []
        if not enunciado:
            problemas.append(ENUNCIADO_VAZIO)
        if len(opcoes) != 5:
            problemas.append(ALTERNATIVAS_INCOMPLETAS)
        if texto_ilegivel(enunciado) or any(texto_ilegivel(o.texto) for o in opcoes):
            problemas.append(TEXTO_ILEGIVEL)
        if options.ultima_alternativa_anomala(opcoes):
            problemas.append(ULTIMA_ALTERNATIVA_ANOMALA)
        seus_ativos = list(por_questao.get(segmento.numero, []))
        if assets.dependencia_visual_nao_resolvida(
                buraco_por_questao.get(segmento.numero, 0.0), seus_ativos):
            problemas.append(DEPENDENCIA_VISUAL_NAO_RESOLVIDA)
        if fragmentation.fragmentacao_sistematica(
                [enunciado] + [o.texto for o in opcoes], vocabulario):
            problemas.append(FRAGMENTACAO_DE_PALAVRA_SUSPEITA)

        resposta = chave.respostas.get(segmento.numero)
        por_lingua = chave.respostas_por_lingua.get(segmento.numero, {})
        if resposta is None and por_lingua:
            problemas.append(GABARITO_AMBIGUO)
        elif resposta is None:
            problemas.append(GABARITO_AUSENTE)

        numeros_vistos[segmento.numero] = numeros_vistos.get(segmento.numero, 0) + 1
        if numeros_vistos[segmento.numero] > 1:
            problemas.append(NUMERO_DUPLICADO)

        resultado.questoes.append(QuestionCandidate(
            numero=segmento.numero, enunciado=enunciado, opcoes=opcoes,
            gabarito=resposta, gabarito_por_lingua=dict(por_lingua),
            pagina_inicio=segmento.pagina_inicio, pagina_fim=segmento.pagina_fim,
            atravessa_pagina=segmento.atravessa_pagina,
            ativos=seus_ativos,
            motor_texto=motor, forma_alternativas=forma, problemas=problemas,
            ocorrencia=segmento.ocorrencia,
        ))

    extraidos = {q.numero for q in resultado.questoes}
    resultado.gabarito_orfaos = sorted(set(chave.respostas) - extraidos)
    if chave.ambiguos:
        resultado.avisos.append(
            f"gabarito com duas respostas por linha nos itens {chave.ambiguos} "
            f"(ordem das colunas: {chave.ordem_das_linguas or 'nao declarada'}) - "
            "ver desenho C6, nao resolvido nesta etapa")
    return resultado
