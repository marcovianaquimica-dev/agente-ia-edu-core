"""Fragmentacao de palavra: DETECTA, nunca corrige. PROTOTIPO EXPERIMENTAL.

Defeito achado pela terceira adjudicacao humana, sobre amostra aleatoria
simples de 60 entre as 698 aprovadas. As duas unicas reprovas tinham a mesma
observacao do adjudicador - espaco espurio depois da primeira letra:

    'e nsaio'   'm icronucleos'   'P assado'   'r eflete'   't este'

E o mesmo estilhacamento de grupo de glifos que destruiu o rotulo 'QUESTAO'
em 2025 (causa C2 do diagnostico), em versao mais branda: atinge palavras
isoladas em vez do cabecalho.

POR QUE SO DETECTAR, E NAO CORRIGIR
-----------------------------------
Juntar a letra ao resto parece trivial e nao e. Medido em 2020 D1 item 37, um
item APROVADO:

    's o ci o e c o n o m ic as'   =   'socioeconomicas'

A corrupcao tambem acontece em nivel de CARACTERE, e juntar dois tokens
produziria 'so cioeconomicas' - meio consertado, que e pior do que declarado.
Alem disso o usuario fixou a politica do MVP: quando a correcao automatica
nao puder ser demonstrada segura, prefira REQUIRES_REVIEW.

POR QUE A REGRA NAO E "LETRA + ESPACO = ERRO"
---------------------------------------------
Portugues tem palavras de uma letra, e itens de exatas usam variaveis,
unidades, rotulos e simbolos. Inspecao caso a caso dos 698 aprovados:

    falso positivo   'X desmagnetizam' / 'X aquecem' / 'X ionizam' -> raios X
                     'B disponibilizadas' -> urna B
                     'X paterno' -> cromossomo X
                     'T monitorada'; 'W otimizado'; 'D min'; 'I superassem'
                     => poucas ocorrencias, SEMPRE DA MESMA LETRA

    defeito real     52, 39, 30, 27, 24... ocorrencias no MESMO item, de
                     letras diferentes

Dois sinais, os dois internos ao corpus:

  1. lexical  - o resto nao e palavra do corpus, ou o junto e palavra dele;
  2. sistemico - o item inteiro esta afetado.

O limiar do sinal 2 tem PLATO. Medido sobre os 698: qualquer combinacao de
(2 ocorrencias, 2 letras) ate (5 ocorrencias, 3 letras) seleciona de 31 a 33
itens. Com (3, 3) sao 33, e os 6 excluidos sao exatamente os falsos positivos
identificados por inspecao. A escolha nao depende dos dois casos adjudicados:
eles entram por qualquer uma das combinacoes do plato.

HIPOTESE MINHA DERRUBADA PELOS DADOS
------------------------------------
Tentei um segundo detector, por densidade de tokens de uma letra no item.
Medido: >= 0,25 marca 33 itens, dos quais 19 nao sao pegos pela regra lexical.
Inspecao desses 19 mostrou que quase todos sao MATEMATICA LEGITIMA:

    'd Q d R 1 4'  (fracao)      'R 2' / '2 R' / '4 R'  (raio)
    '1 e 2.'       (enumeracao)

Apenas 1 dos 19 era corrupcao real. O sinal de densidade foi DESCARTADO.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

# Letra isolada, um espaco, sequencia minuscula de 2+ caracteres.
PADRAO_CANDIDATO = re.compile(r"(?<![0-9A-Za-zÀ-ÿ])([A-Za-zÀ-ÿ]) ([a-zà-ÿ]{2,})")
PADRAO_TOKEN = re.compile(r"[A-Za-zÀ-ÿ]+")

# Letras que sao palavra legitima de uma letra em portugues.
VOGAIS_SOZINHAS = frozenset("aeoàáâãéêíóôõúAEOÀÁÂÃÉÊÍÓÔÕÚ")

# Ver o PLATO documentado acima. Nao sao numeros de faca.
MINIMO_DE_OCORRENCIAS = 3
MINIMO_DE_LETRAS_DISTINTAS = 3

# GUARDA. O detector so sabe o que e palavra olhando o corpus. Com
# vocabulario pequeno TUDO vira "desconhecido" e ele marca texto legitimo -
# descoberto escrevendo os testes, quando tres fixtures minhas falharam por
# vocabulario vazio, e nao por defeito do codigo.
# Medido por caderno (so o motor escolhido): de 941 formas (2021 D1, o
# caderno degradado) a 5.033 (2022 D1). 500 fica abaixo de todos os reais.
VOCABULARIO_MINIMO = 500


def _normal(texto: str) -> str:
    return unicodedata.normalize("NFC", texto).lower()


def vocabulario_limpo(documento: str) -> Counter:
    """Frequencia dos tokens, IGNORANDO os que vem logo apos uma letra solta.

    Sem esta exclusao o vocabulario fica contaminado pelos proprios
    fragmentos: 'nsaio' passaria a ser "palavra do corpus" e o detector
    mediria zero. Foi exatamente o erro da primeira versao desta medicao.
    """
    contagem: Counter = Counter()
    anterior = ""
    for token in PADRAO_TOKEN.findall(documento):
        if len(anterior) != 1:
            contagem[_normal(token)] += 1
        anterior = token
    return contagem


def suspeita(letra: str, resto: str, vocabulario: Counter) -> bool:
    """Esta ocorrencia parece uma palavra partida?

    Verdadeiro quando o resto nao e palavra conhecida do corpus. Nesse caso,
    ou o junto e palavra (prova positiva), ou a letra e consoante (nao existe
    palavra de uma consoante em portugues).
    """
    if vocabulario.get(_normal(resto), 0) > 0:
        return False
    if vocabulario.get(_normal(letra + resto), 0) > 0:
        return True
    return letra not in VOGAIS_SOZINHAS


def ocorrencias(textos: list[str], vocabulario: Counter) -> list[tuple[str, str]]:
    """Todas as ocorrencias suspeitas nos textos de um item."""
    achados: list[tuple[str, str]] = []
    for texto in textos:
        for m in PADRAO_CANDIDATO.finditer(texto or ""):
            letra, resto = m.group(1), m.group(2)
            if suspeita(letra, resto, vocabulario):
                achados.append((letra, resto))
    return achados


def fragmentacao_sistematica(textos: list[str], vocabulario: Counter) -> bool:
    """O item inteiro esta fragmentado, e nao apenas usa uma variavel?

    Exige as duas coisas: volume de ocorrencias E variedade de letras. Uma
    variavel repetida ('raios X' tres vezes) tem volume e nao tem variedade.
    """
    if len(vocabulario) < VOCABULARIO_MINIMO:
        return False
    achados = ocorrencias(textos, vocabulario)
    if len(achados) < MINIMO_DE_OCORRENCIAS:
        return False
    letras = {letra.lower() for letra, _ in achados}
    return len(letras) >= MINIMO_DE_LETRAS_DISTINTAS
