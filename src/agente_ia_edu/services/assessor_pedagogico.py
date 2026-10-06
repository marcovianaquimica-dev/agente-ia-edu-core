"""ASSESSOR PEDAGOGICO - a proxima intervencao de aprendizagem.

O PROBLEMA
===========
Reproduzido no navegador em 2026-10-05, com o Aluno Teste A:

    Atividade de Estequiometria
    -> diagnostico de Balanceamento
    -> 1 de 3
    -> "Continuar"
    -> PRATICA: mais cinco questoes de Balanceamento

Entre errar e responder de novo nao havia nada. O sistema SABIA qual era a
lacuna - a decisao do diagnostico trazia `CONSERVACAO_DE_ATOMOS: 3
respondidas, 0,33` - e respondeu oferecendo mais perguntas. Isso e um
encadeador adaptativo de exercicios. Um assessor ensina antes de perguntar de
novo.

O QUE ESTE MODULO DECIDE
=========================
Uma coisa so: qual e a proxima intervencao.

    ENSINAR     ha uma explicacao e ele ainda nao a usou neste ciclo
    GUIADA      tentar com ajuda progressiva, antes de tentar sozinho
    PRATICAR    tentar sozinho - a evidencia que o dominio le
    VERIFICAR   ele acabou de ir bem depois de ir mal: confirmar
    ESCALAR     o teto de ciclos acabou; insistir sozinho deixou de ajudar
    nenhuma     ele ja mostrou o que precisava, ou nunca foi medido

O SEGUNDO PROBLEMA, DO TESTE HUMANO DE 2026-10-05
==================================================
A versao anterior decidia so entre ENSINAR e PRATICAR, olhando a media
ACUMULADA. Medido pelo caminho real da API:

    diagnostico 0/3, pratica 1/5, pratica 4/5, pratica 5/5
    -> acumulado 10/18 = 0,556 -> ENSINO de novo, e `escalate` ligado

Nove acertos nas ultimas dez, e a tela nao mudou. E, do outro lado, errar
sempre devolvia ENSINO -> PRATICA -> ENSINO -> PRATICA com o mesmo material e
o mesmo lote de cinco questoes: um banco de exercicios, nao um assessor.

As duas correcoes sao a mesma: a decisao passou a olhar a TRAJETORIA
(`trajetoria_do_aluno`) alem da media, e a variar a estrategia por ciclo ate
um teto que agora TERMINA em ESCALAR em vez de recomecar.

E DETERMINISTICO. Nenhuma decisao daqui consulta modelo de IA. O percurso
pedagogico pertence ao Nucleo Edu 360; um modelo podera, depois, adaptar a
LINGUAGEM de uma explicacao - nunca escolher se o aluno precisa dela. Ha
teste lendo a AST deste arquivo que falha se alguem importar um provider.

O QUE ELE NAO DECIDE
=====================
Se o aluno aprendeu. Isso continua sendo da evidencia, do mapa de dominio e
da `PerformanceThresholdPolicy`. Ter lido uma explicacao nao e ter aprendido.

NENHUM CORTE NOVO
==================
As faixas chegam prontas de quem mede (`diagnostico_por_habilidade` e o mapa
de dominio). Ha teste de AST proibindo literal float aqui: um numero neste
arquivo seria uma segunda politica, divergindo da primeira no primeiro
ajuste.

COMO O CICLO E CONTADO SEM TABELA NOVA
=======================================
    ja_ensinado          o aluno ja abriu a explicacao (MaterialProgress)
    praticas_concluidas  quantas praticas daquele conteudo ele terminou

Os dois ja sao gravados pelo sistema, por motivos proprios. O ciclo e
derivado deles. Nao ha persistencia nova nesta camada - e, se um dia houver,
que seja porque algo deixou de ser derivavel, nao por conveniencia.
"""

from __future__ import annotations

from collections.abc import Sequence

from agente_ia_edu.services.pedagogical_analysis import (
    BAND_IMPROVEMENT,
    BAND_INSUFFICIENT,
    BAND_NO_DATA,
)
from agente_ia_edu.services.trajetoria_do_aluno import (
    TENDENCIA_CONFIRMADA,
    TENDENCIA_RECUPERANDO,
    tendencia,
)

# POR ONDE a explicacao entra. Nao sao dois materiais: e o mesmo conteudo
# aberto por outra porta.
#
# UX-4 do teste manual de 2026-10-05: explicacao A -> pratica -> dificuldade
# -> explicacao A de novo, do mesmo ponto. Reabrir o mesmo texto do mesmo
# lugar nao e uma segunda tentativa de ensinar; e a primeira repetida.
ABORDAGEM_CONCEITO = "CONCEITO"   # a ideia, do comeco
ABORDAGEM_EXEMPLO = "EXEMPLO"     # direto no exemplo resolvido

ACAO_ENSINAR = "TEACH"
ACAO_GUIADA = "GUIDED"
ACAO_PRATICAR = "PRACTICE"
ACAO_VERIFICAR = "VERIFY"
ACAO_ESCALAR = "ESCALATE"

# Depois de tantos ciclos de ensinar-praticar sem o aluno destravar, insistir
# sozinho deixa de ser ajuda. Ate 2026-10-05 este limite acendia um sinalizador
# e o sistema continuava oferecendo o mesmo lote de questoes; agora o passo
# vira ESCALAR, porque "mais cinco questoes" ja foi tentado tres vezes.
LIMITE_DE_CICLOS = 3

_FAIXAS_DE_LACUNA = (BAND_IMPROVEMENT,)


def habilidade_que_trava(habilidades: dict, *, grafo=None) -> str | None:
    """A micro-habilidade que esta travando, para quem precisa saber ANTES.

    Existe por uma ordem de perguntas: para decidir se a guiada e uma opcao, e
    preciso saber se ha item guiado PARA A LACUNA - e a lacuna e escolhida
    aqui. Sem este acesso, quem monta a pergunta chutava `skill=None` e
    recebia sempre o primeiro item do conteudo; quando a habilidade que
    travava mudava, a guiada da nova lacuna nunca era oferecida.

    COM GRAFO, A PERGUNTA MUDA
    ===========================
    Sem grafo a escolha e pelo MENOR ACERTO. Para quem vai mal na leitura da
    formula (0/3) e pior ainda no problema completo (0/5), isso aponta o
    problema completo - e o sistema ensina a cadeia inteira a quem nao le o
    indice do NH3.

    Com grafo a pergunta deixa de ser "qual esta pior" e passa a ser "em qual
    delas ele esta PRONTO para aprender agora": `primeiro_gargalo` desce ate a
    fraca mais basica da cadeia.

    Se o grafo nao reconhecer nenhuma das fracas - subconteudos antigos, de
    antes do contrato V2 - a escolha volta a ser a de sempre. Calar seria
    deixar o aluno sem intervencao por causa de um nome de codigo.

    E QUANDO NAO HA LACUNA MEDIDA, MAS HOUVE ERRO
    ==============================================
    A sondagem pergunta uma coisa de cada habilidade; a politica exige tres
    respostas para concluir. Em serie, as duas se anulam: o aluno respondia e
    nenhuma habilidade saia como lacuna.

    `sinal_diagnostico` resolve isso sem mexer no dominio - um erro basta para
    SUSPEITAR, um acerto nao basta para CONFIRMAR. A suspeita so e consultada
    DEPOIS da medida: uma habilidade com tres respostas e 0,2 de acerto e
    informacao melhor que um erro isolado em outra, e inverter a ordem faria o
    sistema abandonar o que sabe para perseguir o que apenas desconfia.

    A suspeita escolhe ALVO. Ela nao entra no mapa de dominio, nao vira
    evidencia e nao contorna o minimo de amostra - ha teste de cada uma dessas
    tres coisas.
    """
    pior = _pior_habilidade(habilidades)[0]
    if grafo is None:
        return pior
    por_habilidade = (habilidades or {}).get("por_habilidade") or {}
    fracas = [s for s, v in por_habilidade.items()
              if v.get("band") in _FAIXAS_DE_LACUNA]
    medido = grafo.primeiro_gargalo(fracas) or pior
    if medido is not None:
        return medido

    from agente_ia_edu.services.sinal_diagnostico import alvo_sugerido

    return alvo_sugerido(grafo, habilidades)


def _pior_habilidade(habilidades: dict) -> tuple[str | None, str | None]:
    """A habilidade com lacuna MEDIDA, ou (None, None).

    "Medida" e o ponto: uma habilidade com amostra insuficiente nao entra.
    Apontar a lacuna errada manda o aluno estudar o que ele ja sabe, e ele
    percebe.
    """
    por_habilidade = (habilidades or {}).get("por_habilidade") or {}
    candidatas = [
        (s, v) for s, v in por_habilidade.items()
        if v.get("band") in _FAIXAS_DE_LACUNA
    ]
    if not candidatas:
        return None, None
    # A de menor acerto primeiro; o codigo desempata para a saida ser estavel.
    # `or 0` e um default de ordenacao, nao um corte - o guarda de AST que
    # proibe literal float neste arquivo esta certo em ser cego a diferenca.
    pior = min(candidatas, key=lambda kv: (kv[1].get("accuracy") or 0, kv[0]))
    return pior[0], (pior[1].get("name") or pior[0])


def decidir_intervencao(
    *,
    habilidades: dict,
    banda_do_conteudo: str,
    ja_ensinado: bool,
    praticas_concluidas: int,
    ha_material: bool,
    ha_guiada_pendente: bool = False,
    tentativas: Sequence[dict] | None = None,
    objetivo_nome: str | None = None,
    conteudo_nome: str | None = None,
    ultima_foi_verificacao: bool = False,
    alvo: str | None = None,
    alvo_nome: str | None = None,
) -> dict:
    """A proxima intervencao, ou `action=None` quando nao ha o que intervir.

    `habilidades` e a saida de `diagnostico_por_habilidade`; `banda_do_conteudo`
    e a faixa do conteudo inteiro. Os dois ja passaram pela politica de cortes.

    `tentativas` sao as tentativas daquele conteudo em ordem cronologica, cada
    uma com `answered` e `correct` - a trajetoria que a media acumulada apaga.
    """
    ciclo = int(praticas_concluidas or 0) + 1
    escalar = int(praticas_concluidas or 0) >= LIMITE_DE_CICLOS
    # O ALVO VEM DE FORA QUANDO QUEM CHAMA JA O ESCOLHEU.
    #
    # `_pior_habilidade` escolhe pelo menor acerto e so enxerga lacuna MEDIDA.
    # Quem tem grafo ja decidiu melhor - pelo primeiro gargalo, e com a
    # suspeita da sondagem quando nao ha medida. Recalcular aqui descartaria
    # essa decisao e devolveria `skill=None` logo depois de uma sondagem, que
    # foi o que se mediu em 2026-10-06.
    skill, skill_nome = _pior_habilidade(habilidades)
    if alvo:
        skill = alvo
        por_habilidade = (habilidades or {}).get("por_habilidade") or {}
        skill_nome = ((por_habilidade.get(alvo) or {}).get("name")
                      or alvo_nome or alvo)
    trajeto = tendencia(tentativas)

    base = {
        "action": None,
        "skill": skill,
        "skill_name": skill_nome,
        "cycle": ciclo,
        "escalate": escalar,
        "trend": trajeto,
        # So faz sentido quando a acao e ENSINAR: praticar nao tem "por onde
        # entrar". Fica None no resto para ninguem ler significado onde nao ha.
        "approach": None,
        "target_name": objetivo_nome,
        "blocking_name": conteudo_nome,
        "reason": None,
        "learning_objective": None,
        "next_check": None,
    }

    # SEM MEDIDA NAO HA LACUNA. Quem ainda nao foi medido e assunto do
    # diagnostico, nao do assessor - e "voce tem dificuldade em X" dito a
    # quem nunca respondeu nada sobre X e inventar sobre a pessoa.
    if banda_do_conteudo in (BAND_INSUFFICIENT, BAND_NO_DATA):
        return base

    # RECUPERACAO CONFIRMADA. Duas tentativas fortes seguidas dizem que ele
    # aprendeu, mesmo que a media acumulada - carregando o comeco ruim - ainda
    # nao diga. Sem isto, quem comecava mal nunca mais saia da preparacao.
    if trajeto == TENDENCIA_CONFIRMADA:
        return base

    # Alvo escolhido de fora JA e a afirmacao de que ha onde intervir - ele so
    # existe quando houve lacuna medida ou suspeita da sondagem.
    ha_lacuna = banda_do_conteudo in _FAIXAS_DE_LACUNA or skill is not None
    if not ha_lacuna:
        # Quem ja demonstrou o que precisava nao e interrompido: intervir em
        # quem sabe custa o tempo do aluno e a credibilidade do sistema.
        return base

    assunto = skill_nome or conteudo_nome or "este conteúdo"

    acao = _acao(
        trajeto=trajeto, escalar=escalar, ja_ensinado=ja_ensinado,
        ha_material=ha_material, ha_guiada_pendente=ha_guiada_pendente,
        verificacao_falhou=(bool(ultima_foi_verificacao)
                            and trajeto != TENDENCIA_RECUPERANDO))
    base.update({
        "action": acao,
        "approach": _abordagem(acao, ciclo),
        "reason": _motivo(assunto, conteudo_nome, objetivo_nome),
        "learning_objective": f"Entender {assunto} e usar isso para resolver "
                              f"exercícios sem travar.",
        "next_check": "Depois da prática, eu confiro se você já consegue "
                      "resolver sozinho — e só então seguimos.",
    })
    return base


def _abordagem(acao: str, ciclo: int) -> str | None:
    """Por onde a explicacao entra desta vez.

    Na primeira, pela ideia - e o percurso que o material foi escrito para
    ter. Da segunda em diante, direto pelo EXEMPLO RESOLVIDO: quem leu a
    explicacao e continuou travando raramente destrava relendo o mesmo
    paragrafo, e ver a conta sendo feita ate o fim ataca o mesmo ponto por
    outro caminho.

    Nao e um segundo material - e o mesmo, aberto em outro lugar. Inventar um
    texto alternativo aqui seria inventar conteudo pedagogico, que nao e
    decisao deste modulo nem deste sistema sem um humano.
    """
    if acao != ACAO_ENSINAR:
        return None
    return ABORDAGEM_CONCEITO if int(ciclo or 1) <= 1 else ABORDAGEM_EXEMPLO


def _acao(*, trajeto: str, escalar: bool, ja_ensinado: bool,
          ha_material: bool, ha_guiada_pendente: bool,
          verificacao_falhou: bool = False) -> str:
    """A estrategia desta vez - e ela precisa MUDAR quando a anterior falhou.

    A ordem e a propria politica, e cada linha existe por um motivo:

    1. VERIFICAR vence tudo. Quem acabou de ir bem depois de ir mal merece a
       chance de confirmar - inclusive quem ja passou do teto de ciclos.
       Escalar alguem que esta melhorando seria punir a recuperacao.
    2. ESCALAR vem antes de qualquer nova tentativa. Depois de
       LIMITE_DE_CICLOS praticas sem destravar, "mais cinco questoes" ja foi
       respondido tres vezes - e e aqui que o loop TERMINA.
    3. ENSINAR, sempre que a leitura anterior ja nao vale. Quem consome
       `ja_ensinado` o envelhece a cada tentativa: praticou e continuou mal,
       entao aquela leitura nao bastou.
    4. GUIADA antes de PRATICAR: tentar com ajuda antes de tentar sozinho. Ela
       volta a entrar nos ciclos seguintes quando a habilidade que trava muda,
       porque muda tambem o item guiado.
    5. PRATICAR, a unica das cinco que produz evidencia de dominio.

    O QUE GARANTE QUE NAO E UM BANCO DE QUESTOES
    =============================================
    Duas coisas, e nenhuma delas e um limite de reensino. A primeira: entre
    duas praticas sempre ha uma intervencao, porque `ja_ensinado` envelhece a
    cada tentativa - nunca saem dois lotes de questoes seguidos. A segunda, e
    a que faltava ate 2026-10-05: o ciclo ACABA em ESCALAR, em vez de comecar
    de novo.

    Tentei primeiro limitar o reensino a dois ciclos, e o resultado foi pior:
    no terceiro ciclo, sem material e com a guiada daquela habilidade ja
    concluida, so sobrava PRATICA - e entao vinham duas praticas seguidas,
    exatamente o que o limite existia para evitar.
    """
    if trajeto == TENDENCIA_RECUPERANDO:
        return ACAO_VERIFICAR
    if escalar:
        return ACAO_ESCALAR
    if ha_material and not ja_ensinado:
        return ACAO_ENSINAR
    if ha_guiada_pendente:
        return ACAO_GUIADA
    # UMA VERIFICACAO QUE FALHOU NAO PEDE O MESMO LOTE DE NOVO.
    #
    # Medido em 2026-10-06, pelo caminho real da decisao: 1/5 -> 5/5 ->
    # VERIFY 0/3, sem material publicado, devolvia PRACTICE e a tela dizia
    # "Vamos tentar de novo, sozinho." Uma verificacao forte negativa nao
    # informa que o aluno precisa treinar mais: informa que a INTERVENCAO
    # ANTERIOR NAO BASTOU. As duas saidas acima - reensinar por outro caminho,
    # ou tentar com ajuda - ja foram pesadas e nao estavam disponiveis. Entao
    # as estrategias deste sistema acabaram, e quem continua e o professor.
    if verificacao_falhou:
        return ACAO_ESCALAR
    return ACAO_PRATICAR


def _motivo(assunto: str, conteudo_nome: str | None, objetivo_nome: str | None) -> str:
    """Por que estou estudando isto, em linguagem de aluno.

    Sem `readiness`, `DIRECT`, `band` ou `origin_breakdown`: esses conceitos
    sao do sistema, e ler o proprio diagnostico escrito em codigo interno nao
    ajuda ninguem a aprender.
    """
    onde = conteudo_nome or "esta base"
    if objetivo_nome:
        return (f"Pelas suas respostas, {assunto} ainda está travando. "
                f"{onde} é a base de {objetivo_nome} — vale firmar isso antes.")
    return (f"Pelas suas respostas, {assunto} ainda está travando. "
            f"Vale firmar isso antes de seguir.")
