"""PHASE 9U.2-G5 — the ``curriculum-v2`` deterministic INITIAL bindings.

Nineteen approved contents (N01–N04, N06–N20; N05 is DEFERRED while ENEM 2020
question 107 remains HUMAN_REVIEW). Each entry pairs a catalog CONTENT ``code``
with its AREA ``parent_code`` and a ``RetrievalVocabularyEntry`` whose terms are
taken verbatim from ``docs/phase9u2_g0_curation.md`` §15.6 (no invented terms;
``generic_terms`` is intentionally empty — generic terms never trigger a match).

This module holds DATA only. It is turned into
``DeterministicInitialBinding`` objects by
``build_curriculum_v2_bindings(...)``, which the classification service calls to
extend its single registry. Nothing here reads the database, calls a provider,
or classifies anything. A binding is inert until its ``canonical_code`` exists
as an active CONTENT node under ``parent_code`` in the catalog — until then
``_validate_target_taxonomy`` fails closed.
"""

from __future__ import annotations

TAXONOMY_VERSION = "curriculum-v2"

# (content_code, parent_area_code, primary_terms, specific_terms, contextual_terms)
_SPECS: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "CHEMISTRY-PHYSICAL-EQUILIBRIUM", "CHEMISTRY-PHYSICAL",
        ("equilíbrio químico", "deslocamento de equilíbrio", "princípio de Le Chatelier"),
        ("reação reversível", "sentido direto", "sentido inverso", "efeito da temperatura no equilíbrio"),
        ("reação reversível com ∆H", "mudança de cor reversível"),
    ),
    (
        "CHEMISTRY-PHYSICAL-ACID-BASE", "CHEMISTRY-PHYSICAL",
        # PHASE 11.21 - bare "pH" removed: it normalises to '' (NFKD->ascii 'ph',
        # then dropped as <3 chars) and matched EVERY one of the 332 statements.
        # It was the only anchor that recovered the legitimate 2020 Q104
        # (ocean-acidification / CO2-in-water) classification, so it is replaced
        # by two composite phrases taken verbatim from that statement. 332-sweep:
        # the ACID-BASE binding now matches ONLY 2020 Q104; 0 false positives;
        # 0 losses; 2020 Q104 -> CHEMISTRY-PHYSICAL-ACID-BASE improves rank 4->2.
        ("ácido carbônico", "potencial hidrogeniônico", "acidificação"),
        ("H₂CO₃", "CO₂ dissolvido em água", "caráter ácido", "facilmente solubilizado em água"),
        ("redução do pH da água", "acidificação dos oceanos", "afetará os organismos aquáticos em razão da"),
    ),
    (
        "CHEMISTRY-GENERAL-POLARITY-IMF", "CHEMISTRY-GENERAL",
        # PHASE 11.26 — three composite phrases appended (verbatim from ENEM 2025
        # Q116's STATEMENT — oil/water separation by surface polarity / affinity).
        # 332-sweep: the new phrases fire on Q116 only; 0 false positives; 0
        # candidate losses; Q116 gains this node at controlled-vocabulary rank-1
        # (score 150), out-scoring the "polímero" -> CHEMISTRY-ORGANIC-POLYMERS
        # match (the device material, not the assessed concept). Existing terms
        # unchanged. NOTE: Q116 also references a device schematic; if the decision
        # core flags a visual dependency the consensus verdict remains HUMAN_REVIEW.
        ("polaridade", "forças intermoleculares", "semelhante dissolve semelhante",
         "filtro capaz de separar óleo e água"),
        ("molécula apolar", "solvente apolar", "hexano", "afinidade", "polímero de carga positiva"),
        ("extração com solvente apolar", "solubilidade em função da polaridade",
         "Na utilização desse dispositivo, a retenção do óleo ocorre"),
    ),
    (
        "CHEMISTRY-GENERAL-MATTER-PROPERTIES", "CHEMISTRY-GENERAL",
        ("densidade", "massa específica"),
        ("g/cm³", "volume deslocado", "proveta", "massa e volume"),
        ("medição de densidade por deslocamento de água",),
    ),
    (
        "CHEMISTRY-ORGANIC-POLYMERS", "CHEMISTRY-ORGANIC",
        ("polímero", "polímeros biodegradáveis"),
        ("monômero", "degradação", "polímeros convencionais", "biodegradabilidade"),
        ("substituição de polímeros convencionais por biodegradáveis",),
    ),
    (
        "CHEMISTRY-ENVIRONMENTAL-AIR-POLLUTION", "CHEMISTRY-ENVIRONMENTAL",
        ("poluição atmosférica", "material particulado", "metais pesados"),
        ("dióxido de nitrogênio", "dióxido de enxofre", "gases tóxicos", "chumbo"),
        ("resíduos da combustão suspensos no ar",),
    ),
    (
        "PHYSICS-THERMAL-PHASE-CHANGE", "PHYSICS-THERMAL",
        ("calor latente", "mudança de estado", "ebulição"),
        ("temperatura de ebulição", "vaporização", "patamar de temperatura", "evaporação"),
        ("temperatura constante durante a mudança de fase",),
    ),
    (
        "PHYSICS-THERMAL-THERMODYNAMICS", "PHYSICS-THERMAL",
        ("termodinâmica", "máquina térmica", "ciclo de refrigeração"),
        # PHASE 9U.2-H3 — "refrigerador" added (approved): it is a literal, repeated
        # term in ENEM 2020 Q133's STATEMENT ("Os manuais de refrigerador…",
        # "funcionamento do refrigerador"); it is the device whose thermodynamics
        # the question is about; it collides with none of the other 18 curriculum-v2
        # vocabularies and appears in no other of the 21 questions.
        ("condensador", "compressor", "trabalho do compressor", "liquefação do fluido refrigerante", "refrigerador"),
        ("troca de calor com o ambiente",),
    ),
    (
        "PHYSICS-WAVES-PHENOMENA", "PHYSICS-WAVES",
        ("fenômeno ondulatório", "interferência", "interferência destrutiva"),
        ("onda sonora", "superposição de ondas", "cancelamento de ruído"),
        ("supressão de ruído por ondas em oposição de fase",),
    ),
    (
        "PHYSICS-EM-ELECTROSTATICS", "PHYSICS-ELECTROMAGNETISM",
        ("eletrostática", "eletrização", "carga elétrica"),
        ("eletrização por atrito", "transferência de elétrons", "gaiola de Faraday",
         "blindagem eletrostática", "condutor em equilíbrio"),
        ("movimentação de elétrons entre os corpos", "campo elétrico nulo no interior do condutor"),
    ),
    (
        "PHYSICS-EM-INDUCTION", "PHYSICS-ELECTROMAGNETISM",
        ("indução eletromagnética", "lei de Faraday", "fluxo magnético"),
        ("fluxo magnético variável", "força eletromotriz induzida", "bobina", "velocidade angular"),
        ("diferença de potencial induzida por variação de fluxo",),
    ),
    (
        "PHYSICS-MECHANICS-HYDROSTATICS", "PHYSICS-MECHANICS",
        # PHASE 11.21 - "ρ g h" removed: the Greek rho is stripped by ascii
        # encoding and 'g'/'h' are <3 chars, so the phrase normalises to '' and
        # matched EVERY one of the 332 statements. It was the only anchor that
        # recovered the legitimate 2020 Q134 (diver decompression / hydrostatic
        # pressure) classification, so it is replaced by two composite phrases
        # taken verbatim from that statement. 332-sweep: the two new anchors
        # match ONLY 2020 Q134; 0 false positives introduced; 0 losses;
        # 2020 Q134 -> PHYSICS-MECHANICS-HYDROSTATICS improves rank 4->1.
        ("hidrostática", "pressão hidrostática", "teorema de Stevin"),
        ("pressão em fluidos", "profundidade", "densidade do fluido",
         "tempo de descompressão", "a densidade da água seja de"),
        ("variação de pressão com a profundidade",),
    ),
    (
        "PHYSICS-MECHANICS-PRESSURE-SCALE", "PHYSICS-MECHANICS",
        # PHASE 11.20 - bare "escala" removed from the specific group. Full
        # 332-statement sweep proved it was a cross-discipline false-positive
        # generator ("escala Richter" -> Physics for the MATH logarithms item
        # 2024 Q150, and the MATH scale item 2025 Q150). The legitimate scale-
        # model target 2020 Q135 still recovers this node (rank 2, score 100) via
        # "pressão" + "razão entre pressões" + the contextual anchor; 0 losses,
        # 0 new false positives, 0 change to any other node's candidate set.
        ("pressão", "força por unidade de área", "semelhança geométrica"),
        ("fator de escala linear", "razão entre pressões", "mesmo material"),
        ("como a pressão na base varia com a escala do modelo",),
    ),
    (
        "BIOLOGY-ECOLOGY-POPULATIONS-CONSERVATION", "BIOLOGY-ECOLOGY",
        ("fragmentação de habitat", "conservação da biodiversidade", "corredor ecológico"),
        ("efeito de borda", "isolamento de população", "ilha de habitat", "extinção local"),
        ("ligar fragmentos para conservar espécies",),
    ),
    (
        "BIOLOGY-ECOLOGY-BIOGEOCHEMICAL-CYCLES", "BIOLOGY-ECOLOGY",
        # PHASE 11.10 — three composite phrases appended (verbatim from ENEM 2025
        # Q100's STATEMENT) so the carbon-cycle node is recovered for CO2-removal /
        # mitigation items, not only fossil-fuel-burning ones. False-positive sweep
        # over all 332 statements: only Q100 gains the candidate; 0 losses.
        ("ciclo biogeoquímico", "ciclo do carbono", "remoção desse gás presente na atmosfera", "níveis de CO2 na atmosfera"),
        ("combustível fóssil", "carbono aprisionado nos sedimentos", "queima de combustíveis", "ação mitigadora"),
        ("desequilíbrio no ciclo do carbono pela queima de petróleo", "ação mitigadora auxilia na remoção desse gás presente na atmosfera"),
    ),
    (
        "BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS", "BIOLOGY-ANIMAL-PHYSIOLOGY",
        # PHASE 11.10 — composite phrases appended (verbatim from ENEM 2024 Q134's
        # STATEMENT) so ecdysis / exoskeleton-moult items are recovered alongside the
        # original oil-on-feathers item. Sweep over 332 statements: only Q134 gains
        # the candidate (promoted from ancestor rank-4 to rank-1); 0 losses.
        # PHASE 11.26 — the four "fibras musculares" phrases appended (verbatim from
        # ENEM 2024 Q132's STATEMENT — skeletal-muscle-fibre physiology). 332-sweep:
        # the new phrases fire on Q132 only; 0 false positives; 0 candidate losses;
        # Q132 -> this node promoted from ancestor rank-2 to controlled-vocabulary
        # rank-1, out-scoring the pre-existing "proporção" -> MATH-ALGEBRA-FUNCTIONS
        # lexical trap. Existing terms unchanged.
        ("adaptação animal", "impermeabilização das penas", "exoesqueleto dos crustáceos", "muda do exoesqueleto", "exoesqueleto deve ser substituído",
         "As fibras musculares esqueléticas não são todas iguais"),
        ("camada de cera", "flutuação de aves aquáticas", "glândula uropigiana", "impregnações de sais calcários", "revestimento externo confere proteção",
         "As fibras lentas, também conhecidas como fibras vermelhas", "fibras rápidas, ou fibras brancas"),
        ("recuperar a capacidade de flutuação após contato com óleo", "por ser duro, limita o crescimento desses animais",
         "distribuição das fibras nos músculos esqueléticos do corpo"),
    ),
    (
        "MATH-MEASUREMENT-PLANE-AREA", "MATH-MEASUREMENT",
        ("área", "figura plana", "revestimento"),
        ("retângulo", "quadrado", "lado", "aproveitamento de recortes", "menor sobra", "menor preço"),
        ("quantas peças para revestir a sala", "compra de menor custo"),
    ),
    (
        "MATH-COMBINATORICS-COUNTING", "MATH-COMBINATORICS",
        ("contagem", "princípio fundamental da contagem"),
        ("algarismo", "ocorrências de um dígito", "intervalo de números"),
        ("quantas vezes o algarismo 2 aparece de 100 a 399",),
    ),
    (
        "MATH-STATISTICS-CENTRAL-TENDENCY", "MATH-STATISTICS",
        ("média aritmética", "média das estaturas"),
        ("medida de tendência central", "substituição de elementos", "valor mínimo da média"),
        ("qual média os novos elementos devem ter para a média do conjunto atingir o alvo",),
    ),
    # PHASE 11.5-A2 — six bindings for PHASE 11.4 CONTENT nodes. Composite,
    # domain-specific phrases only; no bare single word (no "área", "pressão",
    # "massa", "função", "gráfico", "escala"); every term normalises to >=3-char
    # tokens. Three PHASE 11.4 nodes were left UNBOUND on purpose
    # (MATH-STATISTICS-DATA-INTERPRETATION - cross-discipline "chart" risk;
    # CHEMISTRY-ORGANIC-FUNCTIONS - only evidence is the protected Q107;
    # BIOLOGY-EVOLUTION-MECHANISMS - no explicit evolution term in any target
    # STATEMENT). See var/inep-pilot/phase11_5a2_binding_design_report.json.
    (
        "MATH-PROBABILITY-BASICS", "MATH-PROBABILITY",
        # "maior probabilidade" dropped: it appears in a Day-1 Linguagens question
        # (2024 Q18) as ordinary prose. The remaining terms are probability-specific.
        ("probabilidade de que", "igual probabilidade", "de maneira aleatória"),
        ("escolhido aleatoriamente", "evento aleatório", "espaço amostral", "casos favoráveis"),
        ("probabilidade de serem sorteados", "probabilidade de que todos os candidatos"),
    ),
    (
        "MATH-GEOMETRY-SPATIAL", "MATH-GEOMETRY",
        ("poliedro", "número de vértices", "relação de Euler", "paralelepípedo reto retângulo"),
        ("planificação", "faces e vértices", "volume do sólido", "polígonos regulares"),
        ("quantos vértices tem esse poliedro", "volume de água despejado"),
    ),
    (
        "MATH-ALGEBRA-PERCENTAGE", "MATH-ALGEBRA",
        ("porcentagem de acertos", "variação percentual", "acréscimo percentual", "desconto percentual"),
        ("ponto percentual", "taxa percentual"),
        ("a porcentagem P de acertos",),
    ),
    (
        "CHEMISTRY-PHYSICAL-STOICHIOMETRY", "CHEMISTRY-PHYSICAL",
        # "massa molar" dropped: it also appears in kinetics questions (protected
        # 2020 Q128). "quantidade estequiométrica" etc. are stoichiometry-only.
        ("quantidade estequiométrica", "cálculo estequiométrico", "reagente limitante", "reagente em excesso"),
        ("proporção em mol", "equação química balanceada", "rendimento da reação"),
        ("excesso em relação à quantidade estequiométrica",),
    ),
    (
        "CHEMISTRY-ORGANIC-REACTIONS", "CHEMISTRY-ORGANIC",
        # bare "aminas" was dropped: it stems into "examinadas" / "descontaminad*"
        # and mis-fired on Q107/Q129. The composite "reação entre aminas" is kept.
        ("compostos nitrosos", "compostos nitrogenados", "ácido nitroso", "reação entre aminas"),
        ("nitrosação", "reação de substituição orgânica"),
        ("ácido nitroso produzido irá reagir com compostos nitrogenados",),
    ),
    (
        "BIOLOGY-IMMUNOLOGY-MICROBIOLOGY-DISEASES", "BIOLOGY-IMMUNOLOGY-MICROBIOLOGY",
        ("retrovírus", "vírus da imunodeficiência humana", "linfócitos T", "células de defesa do organismo"),
        ("resposta imune", "agente infeccioso", "infecção viral", "anticorpos específicos"),
        ("infectam as mesmas células de defesa do organismo",),
    ),
    (
        # PHASE 11.10 — new binding for the pre-existing KINEMATICS content node
        # (relative-velocity / velocity-composition items). Composite phrases only,
        # verbatim from ENEM 2024 Q104's STATEMENT. False-positive sweep over all
        # 332 statements: only Q104 gains this candidate; 0 losses elsewhere.
        "PHYSICS-MECHANICS-KINEMATICS", "PHYSICS-MECHANICS",
        ("velocidade em relação à água", "composição de velocidades", "velocidade relativa"),
        ("corrente marinha", "velocidade do nadador"),
        ("velocidade é de 50 metros por minuto, em relação à água",),
    ),
    # PHASE 11.14 — three bindings for content nodes identified in the PHASE 11.13
    # HUMAN_REVIEW audit as retrieval failures (the correct CONTENT already exists).
    # Composite phrases verbatim from each target STATEMENT. Full false-positive
    # sweep over all 332 statements (Day-1 included): each binding matches ONLY its
    # target question; 0 unintended gains; 0 candidate losses; the existing 26
    # bindings are byte-identical. No existing binding was modified.
    (
        "MATH-ALGEBRA-LOGARITHMS", "MATH-ALGEBRA",
        # ENEM 2024 Q150 — logarithmic equation M2-M1 = (2/3) log(E2/E1). The
        # 'escala Richter' wording is a known lexical trap toward
        # PHYSICS-MECHANICS-PRESSURE-SCALE; these anchors are the log-equation setup.
        ("expressão algébrica relacionando esses valores",
         "energia liberada foi um décimo da observada no segundo terremoto"),
        ("magnitude M2 do segundo terremoto", "primeiro terremoto apresentou a magnitude"),
        ("é conhecida uma expressão algébrica relacionando esses valores dada por",),
    ),
    (
        "BIOLOGY-EVOLUTION-MECHANISMS", "BIOLOGY-EVOLUTION",
        # ENEM 2024 Q133 — Batesian mimicry (false coral snakes). Mimicry terms
        # occur in exactly 1 of 332 statements, so this is a clean single-target
        # binding. 'Mecanismos evolutivos e adaptacao' legitimately hosts mimicry.
        ("falsas-corais", "padrão de coloração muito semelhante"),
        ("corais-verdadeiras", "não possuem peçonha", "que são peçonhentas"),
        ("Essa similaridade traz uma vantagem tanto para as corais falsas",
         "Qual é a vantagem dessa similaridade para as falsas-corais"),
    ),
    (
        "BIOLOGY-CYTOLOGY-ORGANELLES", "BIOLOGY-CYTOLOGY",
        # ENEM 2025 Q99 — organelle-function item (lysosomal storage disease). The
        # existing organelles CONTENT node is at the correct granularity; it was
        # only recovered as a weak ancestor name-match, letting the model invent a
        # spurious TAXONOMY_GRANULARITY_GAP. These anchors give it a strong rank-1
        # controlled-vocabulary candidate.
        ("insuficiência funcional de qual estrutura celular",
         "não degradam colesterol esterificado nem triglicerídeos"),
        ("depósito desses compostos em diversos órgãos", "deficiência da enzima lipase ácida"),
        ("Essa doença resulta da insuficiência funcional de qual estrutura celular",),
    ),
)

# The AREAs that must exist (parent = an existing DISCIPLINE).
NEW_AREAS: tuple[tuple[str, str, str], ...] = (
    ("CHEMISTRY-GENERAL", "Química Geral", "CHEMISTRY"),
    ("CHEMISTRY-ORGANIC", "Química Orgânica", "CHEMISTRY"),
    ("CHEMISTRY-ENVIRONMENTAL", "Química Ambiental", "CHEMISTRY"),
    ("PHYSICS-THERMAL", "Termologia", "PHYSICS"),
    ("PHYSICS-WAVES", "Ondulatória", "PHYSICS"),
    ("PHYSICS-ELECTROMAGNETISM", "Eletromagnetismo", "PHYSICS"),
    ("BIOLOGY-ECOLOGY", "Ecologia", "BIOLOGY"),
    ("BIOLOGY-ANIMAL-PHYSIOLOGY", "Fisiologia Animal / Zoologia", "BIOLOGY"),
    ("MATH-MEASUREMENT", "Grandezas e Medidas", "MATH"),
    ("MATH-COMBINATORICS", "Análise Combinatória", "MATH"),
    ("MATH-STATISTICS", "Estatística", "MATH"),
)

# The CONTENTs to create (code, name, parent_area_code).
NEW_CONTENTS: tuple[tuple[str, str, str], ...] = (
    ("CHEMISTRY-PHYSICAL-EQUILIBRIUM", "Equilíbrio químico", "CHEMISTRY-PHYSICAL"),
    ("CHEMISTRY-PHYSICAL-ACID-BASE", "Ácidos e bases (pH)", "CHEMISTRY-PHYSICAL"),
    ("CHEMISTRY-GENERAL-POLARITY-IMF", "Polaridade e forças intermoleculares", "CHEMISTRY-GENERAL"),
    ("CHEMISTRY-GENERAL-MATTER-PROPERTIES", "Propriedades da matéria (densidade)", "CHEMISTRY-GENERAL"),
    ("CHEMISTRY-ORGANIC-POLYMERS", "Polímeros", "CHEMISTRY-ORGANIC"),
    ("CHEMISTRY-ENVIRONMENTAL-AIR-POLLUTION", "Poluição atmosférica", "CHEMISTRY-ENVIRONMENTAL"),
    ("PHYSICS-THERMAL-PHASE-CHANGE", "Mudanças de estado e calor latente", "PHYSICS-THERMAL"),
    ("PHYSICS-THERMAL-THERMODYNAMICS", "Termodinâmica e máquinas térmicas", "PHYSICS-THERMAL"),
    ("PHYSICS-WAVES-PHENOMENA", "Fenômenos ondulatórios (interferência)", "PHYSICS-WAVES"),
    ("PHYSICS-EM-ELECTROSTATICS", "Eletrostática", "PHYSICS-ELECTROMAGNETISM"),
    ("PHYSICS-EM-INDUCTION", "Indução eletromagnética", "PHYSICS-ELECTROMAGNETISM"),
    ("PHYSICS-MECHANICS-HYDROSTATICS", "Hidrostática (pressão em fluidos)", "PHYSICS-MECHANICS"),
    ("PHYSICS-MECHANICS-PRESSURE-SCALE", "Pressão e análise de escala", "PHYSICS-MECHANICS"),
    ("BIOLOGY-ECOLOGY-POPULATIONS-CONSERVATION", "Ecologia de populações e conservação", "BIOLOGY-ECOLOGY"),
    ("BIOLOGY-ECOLOGY-BIOGEOCHEMICAL-CYCLES", "Ciclos biogeoquímicos", "BIOLOGY-ECOLOGY"),
    ("BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS", "Fisiologia e adaptações animais", "BIOLOGY-ANIMAL-PHYSIOLOGY"),
    ("MATH-MEASUREMENT-PLANE-AREA", "Área de figuras planas e aplicações", "MATH-MEASUREMENT"),
    ("MATH-COMBINATORICS-COUNTING", "Princípio fundamental da contagem", "MATH-COMBINATORICS"),
    ("MATH-STATISTICS-CENTRAL-TENDENCY", "Medidas de tendência central (média)", "MATH-STATISTICS"),
)

DEFERRED_CONTENTS: tuple[str, ...] = ("CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS",)  # N05 — Q107 HUMAN_REVIEW


def _slug(code: str) -> str:
    return code.lower().replace("-", "_")


def build_curriculum_v2_bindings(vocab_cls, binding_cls):
    """Return the 19 ``curriculum-v2`` DeterministicInitialBinding objects.

    ``vocab_cls`` / ``binding_cls`` are injected (RetrievalVocabularyEntry /
    DeterministicInitialBinding) so this module never imports the service and
    there is no import cycle.
    """
    out = []
    for code, parent, primary, specific, contextual in _SPECS:
        vocab = vocab_cls(
            canonical_code=code,
            primary_terms=primary,
            specific_terms=specific,
            contextual_expressions=contextual,
            generic_terms=(),
            version=f"{TAXONOMY_VERSION}-{_slug(code)}-v1",
            enabled=True,
        )
        out.append(
            binding_cls(
                taxonomy_version=TAXONOMY_VERSION,
                canonical_code=code,
                parent_code=parent,
                vocabulary=vocab,
            )
        )
    return tuple(out)
