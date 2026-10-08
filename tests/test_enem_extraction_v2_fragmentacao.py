"""Fragmentacao de palavra: deteccao, nunca correcao automatica.

Achado pela terceira adjudicacao humana (amostra aleatoria de 60 sobre as 698
aprovadas). As duas unicas reprovas tinham a mesma observacao: espaco espurio
depois da primeira letra.

    'e nsaio'  'm icronucleos'  'P assado'  'r eflete'  'g enotoxicidade'

A regra NAO pode ser "letra + espaco + sequencia e erro": o portugues tem
palavras legitimas de uma letra, e os itens de exatas usam variaveis (v, t,
n, x), unidades (m, g, s), rotulos (urna A, urna B) e simbolos.

Medido nos 698 itens aprovados, com inspecao caso a caso:

  falso positivo   'X desmagnetizam' / 'X aquecem' / 'X ionizam'  -> raios X
                   'B disponibilizadas' -> urna B;  'X paterno' -> cromossomo
                   'T monitorada';  'W otimizado';  'D min';  'I superassem'
                   => poucas ocorrencias, SEMPRE DA MESMA LETRA

  defeito real     52, 39, 30, 27, 24... ocorrencias POR ITEM, de letras
                   diferentes

Dois sinais, os dois internos ao corpus, sem dicionario externo:

  1. o resto NAO e palavra do corpus, ou o junto E palavra do corpus;
  2. o item inteiro esta afetado: >= 3 ocorrencias e >= 3 letras distintas.

O limiar tem PLATO: qualquer combinacao de (2 ocorr, 2 letras) ate
(5 ocorr, 3 letras) seleciona de 31 a 33 dos 698. Nao e escolha de faca.
"""

from __future__ import annotations

from collections import Counter

from agente_ia_edu.services.enem_extraction_v2 import fragmentation as frag


def vocab(**kw) -> Counter:
    """Vocabulario de teste com tamanho realista.

    O detector tem piso de VOCABULARIO_MINIMO formas: com corpus pobre tudo
    vira "desconhecido" e ele marcaria texto legitimo. Os testes precisam de
    um corpus do tamanho de um caderno real (medido: 941 a 5.033 formas),
    entao as entradas uteis sao completadas com preenchimento inerte.
    """
    c = Counter(kw)
    for i in range(frag.VOCABULARIO_MINIMO + 50):
        c[f"preenchimento{i}"] = 1
    return c


def vocab_pobre(**kw) -> Counter:
    """Corpus pequeno demais para o detector confiar."""
    return Counter(kw)


class TestVocabularioLimpo:
    """O vocabulario NAO pode ser contaminado pelos proprios fragmentos.

    Erro cometido na primeira versao desta medicao: o vocabulario foi
    construido do texto cru, entao 'nsaio' virou "palavra" e o detector
    mediu zero em 9.143 de 9.143 candidatos.
    """

    def test_token_apos_letra_solta_nao_conta_para_o_vocabulario(self):
        """A palavra sobrevive porque tambem aparece em contexto normal.

        O fragmento nao sobrevive porque so existe colado a letra que o
        originou. E por isso que a regra e 'nao conta ESSA ocorrencia', e
        nao 'descarta o token': 'o ensaio' tambem vem depois de uma letra
        solta, e e portugues legitimo.
        """
        v = frag.vocabulario_limpo(
            "um ensaio foi feito. outro ensaio seguiu. um e nsaio ruim")
        assert v["ensaio"] == 2
        assert v["nsaio"] == 0

    def test_palavra_que_SO_aparece_apos_letra_solta_fica_fora(self):
        v = frag.vocabulario_limpo("um e nsaio ruim")
        assert v["nsaio"] == 0

    def test_token_normal_conta(self):
        v = frag.vocabulario_limpo("casa casa casa")
        assert v["casa"] == 3

    def test_caixa_e_acento_sao_normalizados(self):
        v = frag.vocabulario_limpo("Ensaio ensaio ENSAIO")
        assert v["ensaio"] == 3


class TestOcorrenciaSuspeita:
    def test_resto_nao_e_palavra_e_junto_e(self):
        v = vocab(ensaio=18)
        assert frag.suspeita("e", "nsaio", v)

    def test_consoante_solta_com_resto_desconhecido(self):
        v = vocab()
        assert frag.suspeita("n", "ucleolos", v)

    def test_resto_e_palavra_conhecida_nao_e_suspeita(self):
        v = vocab(casa=138)
        assert not frag.suspeita("a", "casa", v)

    def test_vogal_solta_com_resto_conhecido_nao_e_suspeita(self):
        v = vocab(poema=54)
        assert not frag.suspeita("o", "poema", v)


class TestFragmentacaoSistematica:
    """O discriminador que separa defeito de variavel."""

    def test_muitas_ocorrencias_de_letras_diferentes_e_fragmentacao(self):
        v = vocab(passado=37, reflete=8, metaforiza=2, explicita=6, confirma=2)
        textos = ["P assado e r eflete",
                  "m etaforiza, e xplicita e c onfirma"]
        assert frag.fragmentacao_sistematica(textos, v)

    def test_tres_ocorrencias_da_MESMA_letra_nao_e_fragmentacao(self):
        """Caso real: 'raios X desmagnetizam / X aquecem / X ionizam'."""
        v = vocab()
        textos = ["Os raios X desmagnetizam o material",
                  "Os raios X aquecem a amostra",
                  "Os raios X ionizam o gas"]
        assert not frag.fragmentacao_sistematica(textos, v)

    def test_uma_ocorrencia_isolada_nao_e_fragmentacao(self):
        """Casos reais: 'W otimizado', 'T monitorada', 'D min'."""
        for texto in ("o trabalho W otimizado pelo sistema",
                      "a temperatura T monitorada durante o ensaio",
                      "a distancia D minima entre os pontos"):
            assert not frag.fragmentacao_sistematica([texto], vocab())

    def test_o_piso_de_vocabulario_e_o_declarado(self):
        assert frag.VOCABULARIO_MINIMO == 500

    def test_rotulo_de_alternativa_nao_dispara(self):
        """Caso real: 'uma das duas urnas A e B disponibilizadas'."""
        v = vocab(urnas=4, disponibilizadas=2)
        textos = ["escolhe uma das duas urnas A e B disponibilizadas"]
        assert not frag.fragmentacao_sistematica(textos, v)

    def test_texto_limpo_nao_dispara(self):
        v = vocab(poema=54, minha=134, gestacao=3)
        textos = ["o poema foi escrito em toda a minha gestacao",
                  "a resposta e o que se espera"]
        assert not frag.fragmentacao_sistematica(textos, v)

    def test_os_limiares_sao_os_declarados(self):
        assert frag.MINIMO_DE_OCORRENCIAS == 3
        assert frag.MINIMO_DE_LETRAS_DISTINTAS == 3


class TestAdversarialFragmentacao:
    """Nada abaixo pode ser classificado como fragmentacao."""

    def test_formula_matematica(self):
        v = vocab(razao=50, vale=20, raio=40, dobro=15)
        textos = ["a razao d Q d R vale 1 4", "o raio R 2 e o dobro"]
        assert not frag.fragmentacao_sistematica(textos, v)

    def test_unidades_de_medida(self):
        """'m por', 'g por', 's apenas' - o resto E palavra do corpus."""
        v = vocab(velocidade=20, por=900, litro=12, apenas=40, massa=30)
        textos = ["a velocidade de 35 m por segundo",
                  "massa de 10 g por litro", "tempo de 5 s apenas"]
        assert not frag.fragmentacao_sistematica(textos, v)

    def test_simbolos_quimicos_e_variaveis(self):
        """'N aparece', 'P esta', 'f de' - o resto E palavra do corpus."""
        v = vocab(aparece=70, esta=400, de=16230, elemento=25, ponto=60)
        textos = ["o elemento N aparece", "o ponto P esta em", "a funcao f de x"]
        assert not frag.fragmentacao_sistematica(textos, v)

    def test_artigos_e_conjuncoes(self):
        v = vocab(casa=138, mesa=40, porta=30, vida=200, obra=60)
        textos = ["a casa e a mesa", "o porta e a vida", "e a obra toda"]
        assert not frag.fragmentacao_sistematica(textos, v)

    def test_algarismo_romano(self):
        """Caso real: 'as afirmativas I superassem as demais'."""
        v = vocab(superassem=1)
        assert not frag.fragmentacao_sistematica(
            ["caso as afirmativas I superassem as demais"], v)

    def test_inicial_de_nome_proprio(self):
        v = vocab(machado=3, assis=3)
        textos = ["segundo J Machado de Assis", "conforme M Assis escreveu",
                  "para A Machado isso importa"]
        # tres letras distintas, mas os restos SAO palavras do corpus
        assert not frag.fragmentacao_sistematica(textos, v)

    def test_sigla_seguida_de_palavra(self):
        v = vocab(projeto=12, programa=9, plano=7)
        textos = ["o A projeto segue", "o B programa segue", "o C plano segue"]
        assert not frag.fragmentacao_sistematica(textos, v)

    def test_palavra_estrangeira_desconhecida_do_corpus_nao_basta(self):
        """Uma unica ocorrencia nao atinge o minimo, qualquer que seja."""
        v = vocab()
        assert not frag.fragmentacao_sistematica(["o termo e software"], v)

    def test_vocabulario_pobre_desliga_o_detector(self):
        """GUARDA. O detector depende de um corpus rico para saber o que e
        palavra. Com vocabulario pequeno tudo vira 'desconhecido' e o
        detector marca texto legitimo. Descoberto escrevendo estes testes:
        tres fixtures minhas falharam por isso, e nao por defeito do codigo.
        """
        pobre = vocab_pobre(uma=2)
        assert len(pobre) < frag.VOCABULARIO_MINIMO
        textos = ["o elemento N aparece", "o ponto P esta em", "a funcao f de x"]
        assert not frag.fragmentacao_sistematica(textos, pobre)


class TestNaoCorrige:
    """A V2 detecta e sinaliza. Nunca reescreve o texto."""

    def test_o_modulo_nao_expoe_funcao_de_correcao(self):
        nomes = [n for n in dir(frag) if not n.startswith("_")]
        proibidos = [n for n in nomes
                     if any(p in n.lower() for p in ("corrig", "junta", "merge",
                                                     "reparar", "fix"))]
        assert proibidos == [], proibidos
