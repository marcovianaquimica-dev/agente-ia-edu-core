"""Qrels da perna VETORIAL - julgamentos de relevancia CONGELADOS.

CONGELADOS ANTES DE EXISTIR EMBEDDING ALGUM. Esta e a disciplina que
da sentido a medicao: julgar relevancia depois de ver o ranking do
modelo mede a concordancia do juiz consigo mesmo, nao a qualidade do
modelo.

COMO FORAM CONSTRUIDOS
======================

Pooling no estilo TREC: para cada consulta, varias SONDAS lexicais
diferentes - incluindo parafrases e termos tecnicos que a propria
consulta nao usa - contribuem candidatos, e cada candidato do pool foi
lido e julgado em processo (spec 9.3).

Relevancia GRADUADA, o que permite nDCG@10:

    2 - responde diretamente: ensina ou enuncia o conceito perguntado
    1 - aplica ou usa o conceito de forma substantiva
    0 - nao relevante (ausente deste mapa)

LIMITACAO QUE TODA MEDICAO SOBRE ESTES QRELS TEM DE DECLARAR
============================================================

O pool foi construido com busca LEXICAL. Ele nao contem o que e
semanticamente proximo e lexicalmente distante - que e exatamente o que
a perna vetorial existe para achar. Logo:

    Recall@10 calculado contra estes qrels e um LIMITE INFERIOR
    para a perna vetorial.

A mitigacao foi usar varias sondas de parafrase por consulta, nunca so
os termos da pergunta. A limitacao permanece, e o relatorio a repete.

CHAVE: ``text_hash``
====================

``text_hash`` e deterministico a partir do conteudo (sha256 do
``retrieval_text``). UUID de chunk muda a cada ingestao e nao serviria
como chave de um conjunto congelado.
"""

from __future__ import annotations

#: consulta -> {text_hash: grau}. Congelado.
VECTOR_QRELS_V1: dict[str, dict[str, int]] = {
    # 28 candidatos julgados; 9 de grau 2, 5 de grau 1
    'o que sobra quando um dos reagentes acaba primeiro': {
        "13545eca46ea700905559f9d54da426bb6d201ccae85543f001e43684a285655": 2,  # Cotidiano p207
        "883bc4e454586f4c94e3eeac9f89cb2ab42f352e7be9b61a042d618bdcd18835": 2,  # Cotidiano p207
        "b06478793a7c28aee4ef8f8ea3e850139dbd8946133f3775775be9724132613a": 2,  # Cotidiano p212
        "aa83c5514fb84b244f3605b5207d3171285d7900a7477b4fbdf4c1872b8c5a2c": 2,  # Investigar p157
        "838f8a9460e62f6a5200fcbd2af5c31f852289b0ceea17631b94a7e9ce451d70": 2,  # Investigar p158
        "e073694d5ce1e2ad5985590f6394158093573fc3ae9862cf4fb9a0d5d72d7dd3": 2,  # Investigar p499
        "ebfa173c5ca09f25f989fb934acbe3f8e09db6d9d54dd45a5f157246ab704d8c": 2,  # SuperAcao p78
        "00401d1bd5814914afdafce4b301b470273a728b74b49b4e2e884a92f2e1494f": 2,  # SuperAcao p473
        "893ebaeaddc3ab92877342c5215f4780983885fb7d3bf3c773b00b0cccc6d8d3": 2,  # SuperAcao p477
        "4a87705d79bd87eb5c3bf7bee133b5682c9c39e4f9e0274a2264c48cfe6b27af": 1,  # Cotidiano p214
        "61d4c158cd024edcd191b895f9163fbfc8741d3210182d3beb7b56186d3b2848": 1,  # Cotidiano p501
        "6911d73f9282a371af9eb749c6167923988168b1887ac564711a24a86da88aaa": 1,  # Investigar p152
        "dc5186ac7dcafb8abfa6a71f56c938442b8bf7b3ac22f05fc456eab83b96d45d": 1,  # Investigar p152
        "69c1cce3cf3bd716b6cc8571b3991af15e138e3b646942971e1fa1db46a28758": 1,  # SuperAcao p473
    },
    # 38 candidatos julgados; 11 de grau 2, 8 de grau 1
    'como saber qual substância acaba antes numa reação química': {
        "13545eca46ea700905559f9d54da426bb6d201ccae85543f001e43684a285655": 2,  # Cotidiano p207
        "883bc4e454586f4c94e3eeac9f89cb2ab42f352e7be9b61a042d618bdcd18835": 2,  # Cotidiano p207
        "9e54d485b77b78a53b2a318addfa68c50ba64908c6cd73780ef6aa84b4796d6a": 2,  # Cotidiano p207
        "b06478793a7c28aee4ef8f8ea3e850139dbd8946133f3775775be9724132613a": 2,  # Cotidiano p212
        "a057ccb01711f3e960f9a32ed4bb4e625ddfc7ba1053b758f49d78dc08da19b5": 2,  # Cotidiano p500
        "aa83c5514fb84b244f3605b5207d3171285d7900a7477b4fbdf4c1872b8c5a2c": 2,  # Investigar p157
        "838f8a9460e62f6a5200fcbd2af5c31f852289b0ceea17631b94a7e9ce451d70": 2,  # Investigar p158
        "e073694d5ce1e2ad5985590f6394158093573fc3ae9862cf4fb9a0d5d72d7dd3": 2,  # Investigar p499
        "ebfa173c5ca09f25f989fb934acbe3f8e09db6d9d54dd45a5f157246ab704d8c": 2,  # SuperAcao p78
        "00401d1bd5814914afdafce4b301b470273a728b74b49b4e2e884a92f2e1494f": 2,  # SuperAcao p473
        "893ebaeaddc3ab92877342c5215f4780983885fb7d3bf3c773b00b0cccc6d8d3": 2,  # SuperAcao p477
        "f0827cb59c429417884180f6a160532a2312fb1d93403e4557e860ad7b4ecf39": 1,  # Cotidiano p207
        "7749b75b0a386df868c1b8a891125f2b777a0b06b7f9efdd2c7bdf6f594345c5": 1,  # Cotidiano p500
        "587e9d1601c06a9da3a44ea9112260d7578d42ae9548a5b5693a9e74ffd07398": 1,  # Investigar p149
        "6911d73f9282a371af9eb749c6167923988168b1887ac564711a24a86da88aaa": 1,  # Investigar p152
        "1ce9aa38186f2c8688d4e90ddd44cd5086aa69ae543276e6ae4382766695ea26": 1,  # Investigar p154
        "7cf877428970d5053b38b721643c0a9e31e0cbf237b8b4b7e33531baa1308080": 1,  # Investigar p246
        "a7d6444797ecd9f4e95291da26f3d75b3c06feb00c7f500c80c49059735d6222": 1,  # Investigar p496
        "69c1cce3cf3bd716b6cc8571b3991af15e138e3b646942971e1fa1db46a28758": 1,  # SuperAcao p473
    },
    # 40 candidatos julgados; 10 de grau 2, 11 de grau 1
    'por que adicionar água deixa a solução mais fraca': {
        "ab3d87a6f82644d1f6b22cb0c0ac33bfe06db8147fdb22e8dcc252c8ac37a799": 2,  # Cotidiano p168
        "f6399c9632bb2c982307df05be8f442118fc56b0c4ebed1d0305a9c68d89a752": 2,  # Cotidiano p168
        "e744cf6d0db81d7f5ff06dcaea7e3d1b93eb0aa202357f86cd72747db2088753": 2,  # Cotidiano p170
        "3fa780cfd03f2b27a878ae36b7f0a29ec0860fd63754af12e1d0d89ed03d76b8": 2,  # Cotidiano p491
        "37b1c104f6e08e4470143517f441cef3141861f15d734271ea7e02abbf2fe8bc": 2,  # Cotidiano p504
        "c74a91388c0bc69e8248b1868ed403a75e5be983cadc84d99e04a0ac18caea43": 2,  # Investigar p164
        "0afb20ee11c304f061fc16e752712aa939dd7d968e150698331ceeedaeda5f2b": 2,  # Investigar p176
        "a39b3b2f31255c6aa525aae65847fa01a41c0c006ae997d2dfbb75252a914f5b": 2,  # Investigar p177
        "9307d188ac55fcd386bf17ec21c7e18148b86f5fd910066d4db3f809f675de81": 2,  # SuperAcao p407
        "a8392f30f97241c573118601b8748b47ae2f0458552fcc4345c5277fdaad20ed": 2,  # SuperAcao p494
        "10beed7fc3d571c2c73665226b4a3ae65baf34c6a37b6facd39cebf61103cfe5": 1,  # Cotidiano p158
        "61235191f12a5f9b3712d1a1a964f099a66825c2c30a5763b79d6ecc9d7bc1a6": 1,  # Cotidiano p159
        "97fa71f810c9f438363e17ada0fca6ca44a83275f29ed97ef09d3aad7a037bcf": 1,  # Cotidiano p161
        "83f5db3d7e9f8b6ae6c710add3348554a270868d6068f55e718b735d880a6444": 1,  # Cotidiano p162
        "d13c03ddd45d907c0355ba580058449801800e01480a6a40d6f9be907ffa789d": 1,  # Cotidiano p165
        "177d54777a29559695ad3e4d346d809ac8310f0a2f0bf0f182aeacc0e7dc572a": 1,  # Cotidiano p171
        "880546d2ffb6121ffec8d676a6222c9f7986db2c664fa72454b5d6f6fbb20d0a": 1,  # Cotidiano p220
        "40fa440c1b774fe0c4803b87fb92179eda0be8d21b7d4d0593daeb66b3ac2a70": 1,  # Cotidiano p504
        "fff1cb078374ceb8fe8089ba13f727d481ed5c377c58d4fffb1565e1676a2615": 1,  # Investigar p169
        "f62e18721e2b957085fbc301d40028e49bf10258244122b1b65438abd50f916d": 1,  # SuperAcao p496
        "d5534038939ab0a7ad21f6777682a59b8d1e0fa4631c5aedd865432702c9b288": 1,  # SuperAcao p535
    },
    # 35 candidatos julgados; 6 de grau 2, 8 de grau 1
    'quantas partículas existem numa amostra de uma substância': {
        "0b6243d2d4d657e34c051fa8b6b2a7863208bf649362d87c329fc2c286a88380": 2,  # Cotidiano p184
        "caad5b0d571b14f1d5cdc695f46b599cf88e242f7610e4e632303f3e8076980c": 2,  # Cotidiano p195
        "18fbb83da27d3afb53f4b293aefbaa664198fd5773bedfd08e4ae5f7348cef08": 2,  # Cotidiano p195
        "9d6dc1e89e03c4cb42d62897f121e35dc5feebf9a97be70463b54c99893d353a": 2,  # Cotidiano p196
        "57f97abcb8d5c23bbb94bca8859226b7ac6f0e08ac97277aae4a9fb3dec84e1d": 2,  # Cotidiano p498
        "4fd3e57c6df2763b89f4592ec3640d8469f74dcb720116cde768cfbd07dcdfc9": 2,  # Investigar p140
        "fe1a13500116a17605609ae58571452e2e67fd36a455e3099b856c42f995dee6": 1,  # Cotidiano p184
        "563620314be89e6db536f5997f29860a581f58535b927bc6f8f690bb4db494fe": 1,  # Cotidiano p184
        "efd87ca82decdeb451e8fb8c0d014165b6a07f2cf6f4172932a3fbf018b41537": 1,  # Cotidiano p221
        "7c657b33eab081e9e7c679d7b30ab956967ab81c33c20bbe6aa348f175495eb2": 1,  # Cotidiano p497
        "8124d464fb548b412d1eadec5855b37847a12a480d39f09a460a966efb68f30b": 1,  # Investigar p139
        "5305407f7e400781931d7d11ca3ee2fbf7f1d617667eb2f4908f388d27ed0eae": 1,  # Investigar p150
        "5443dc11352f98898ce6c974d6492e85ce13ac9ea872210fc73277fecce7638c": 1,  # Investigar p182
        "9af2df6ae1e5b6fdac6ea41acd2fb0fc25bf48b87de8590d94169fe30dbceb75": 1,  # SuperAcao p69
    },
    # 31 candidatos julgados; 9 de grau 2, 7 de grau 1
    'relação entre a massa de uma substância e o número de partículas': {
        "0b6243d2d4d657e34c051fa8b6b2a7863208bf649362d87c329fc2c286a88380": 2,  # Cotidiano p184
        "e2d93060c0c18354fc5143f664ed51468575190bdf227009efd967e58b1b70b0": 2,  # Cotidiano p196
        "cd5a662842891e8c01fee531ad883c0ba2fca98c84e008790db56908d3e648f7": 2,  # Cotidiano p497
        "7c657b33eab081e9e7c679d7b30ab956967ab81c33c20bbe6aa348f175495eb2": 2,  # Cotidiano p497
        "57f97abcb8d5c23bbb94bca8859226b7ac6f0e08ac97277aae4a9fb3dec84e1d": 2,  # Cotidiano p498
        "b52ec6583980d8bfab3d93109bf6d67a5f5cec152dbee5b7fe01851ef137da82": 2,  # Cotidiano p500
        "4fd3e57c6df2763b89f4592ec3640d8469f74dcb720116cde768cfbd07dcdfc9": 2,  # Investigar p140
        "5305407f7e400781931d7d11ca3ee2fbf7f1d617667eb2f4908f388d27ed0eae": 2,  # Investigar p150
        "5e406f7bde6d57a75df417eddff4ca5136d39d5b0c9baca28a3d42fa1eb30205": 2,  # Investigar p497
        "875b4e99ac20b309d9a6ed177c9da188311ff4f8df05cf0ac69683e3aba843a6": 1,  # Cotidiano p184
        "df2dd27b4115a744fec4f40a175d4dfa5471763aa7a0568c0d9b0d05c493ae42": 1,  # Cotidiano p184
        "06acf48442746529317abb5c32f8763337bc8b9c935259cfe5c48f4ebe01ecde": 1,  # Cotidiano p504
        "5443dc11352f98898ce6c974d6492e85ce13ac9ea872210fc73277fecce7638c": 1,  # Investigar p182
        "80c9d40f7df4ae78b2f2714b05b5bb383f5cd03a6a0dd9d888589e18ac363478": 1,  # Investigar p190
        "c31aed55166077216d103030c644d77022d49ab32d3ba4cf6dd6d1bcdff60b17": 1,  # SuperAcao p75
        "998a71b7bf776b2f8ca817378d23c32c40a427caf15dadc1d955db485b4d78e4": 1,  # SuperAcao p478
    },
    # 26 candidatos julgados; 8 de grau 2, 7 de grau 1
    'preparar uma solução partindo de outra mais concentrada': {
        "f6399c9632bb2c982307df05be8f442118fc56b0c4ebed1d0305a9c68d89a752": 2,  # Cotidiano p168
        "e744cf6d0db81d7f5ff06dcaea7e3d1b93eb0aa202357f86cd72747db2088753": 2,  # Cotidiano p170
        "c74a91388c0bc69e8248b1868ed403a75e5be983cadc84d99e04a0ac18caea43": 2,  # Investigar p164
        "a39b3b2f31255c6aa525aae65847fa01a41c0c006ae997d2dfbb75252a914f5b": 2,  # Investigar p177
        "9fc7b13e6aaec963549d92d10d930855cf7b11eafc60405504a6e275b2a4755e": 2,  # SuperAcao p401
        "9307d188ac55fcd386bf17ec21c7e18148b86f5fd910066d4db3f809f675de81": 2,  # SuperAcao p407
        "a40a4e0b5d1215077d76b05052893b14f5d249eae912b404c47ff095758116ec": 2,  # SuperAcao p408
        "747abcb25c63a8ebe6cf25f630ddcdf6525ab23e6186ae3243d1a4536880647e": 2,  # SuperAcao p535
        "ab3d87a6f82644d1f6b22cb0c0ac33bfe06db8147fdb22e8dcc252c8ac37a799": 1,  # Cotidiano p168
        "0afb20ee11c304f061fc16e752712aa939dd7d968e150698331ceeedaeda5f2b": 1,  # Investigar p176
        "5b2775b305a6f9e916e7b4b929a3e113e6f3f410ec0be205439be0386f858dcf": 1,  # SuperAcao p168
        "d05cf7fe63cb1f6a2791de0dac568efe3e577ab279726253b86d2ae25ddc9417": 1,  # SuperAcao p401
        "a8392f30f97241c573118601b8748b47ae2f0458552fcc4345c5277fdaad20ed": 1,  # SuperAcao p494
        "fe0a585524ec2e6d021a63b85a582c0abbb68fb94ea7a5872acfbcbf10efe889": 1,  # SuperAcao p496
        "d5534038939ab0a7ad21f6777682a59b8d1e0fa4631c5aedd865432702c9b288": 1,  # SuperAcao p535
    },
    # 17 candidatos julgados; 9 de grau 2, 2 de grau 1
    'reagente limitante': {
        "13545eca46ea700905559f9d54da426bb6d201ccae85543f001e43684a285655": 2,  # Cotidiano p207
        "883bc4e454586f4c94e3eeac9f89cb2ab42f352e7be9b61a042d618bdcd18835": 2,  # Cotidiano p207
        "b06478793a7c28aee4ef8f8ea3e850139dbd8946133f3775775be9724132613a": 2,  # Cotidiano p212
        "aa83c5514fb84b244f3605b5207d3171285d7900a7477b4fbdf4c1872b8c5a2c": 2,  # Investigar p157
        "838f8a9460e62f6a5200fcbd2af5c31f852289b0ceea17631b94a7e9ce451d70": 2,  # Investigar p158
        "e073694d5ce1e2ad5985590f6394158093573fc3ae9862cf4fb9a0d5d72d7dd3": 2,  # Investigar p499
        "ebfa173c5ca09f25f989fb934acbe3f8e09db6d9d54dd45a5f157246ab704d8c": 2,  # SuperAcao p78
        "00401d1bd5814914afdafce4b301b470273a728b74b49b4e2e884a92f2e1494f": 2,  # SuperAcao p473
        "893ebaeaddc3ab92877342c5215f4780983885fb7d3bf3c773b00b0cccc6d8d3": 2,  # SuperAcao p477
        "6911d73f9282a371af9eb749c6167923988168b1887ac564711a24a86da88aaa": 1,  # Investigar p152
        "69c1cce3cf3bd716b6cc8571b3991af15e138e3b646942971e1fa1db46a28758": 1,  # SuperAcao p473
    },
    # 27 candidatos julgados; 5 de grau 2, 6 de grau 1
    'fator limitante para a vida de espécies aquáticas': {
        "b307c36e0626cf494e516aba0bc541e1e53f5c6ffa4b311033d5005983b4e441": 2,  # Cotidiano p410
        "496c09b5ba00e4eb69d4b1e3cfdaf0a2c818297917f75f42628b44f1f2dd42b9": 2,  # Cotidiano p517
        "2752d7f6df4b4d3a7ec6cea977984f1e6f87dcd23de5e6d9af93803667652e30": 2,  # Investigar p490
        "de78ddbb9e9f57e681ca16e7e2d106b22f908d70755e74f85d9118702324498e": 2,  # SuperAcao p165
        "8141eb36a9cc72d7e281436e4a934da525dded9161edbe83ae0f6350b80e5baf": 2,  # SuperAcao p173
        "ae66c12d214d9df881e2ad02ff4ebd6cbfc0fd8806afdb88960abdaaa635adda": 1,  # BNCC p119
        "903f7ef77243583ca11929167589322087348a36b986d5370bf3d18cdf1b0256": 1,  # Cotidiano p150
        "211e205de19a36d5ccb550d597b31458d4b33049cf99bb682da4b240e12f7a89": 1,  # Cotidiano p329
        "323f7bf2bb230c7ab82c3e0ef0280e305c666fbe9b5bacebe902ae8d10316c7c": 1,  # Investigar p100
        "483d30da849d2a6960200c54d206b6fea3729a617013ba6f5dc1eb59b6005ac3": 1,  # Investigar p141
        "f6421796be57f0a6e2ce974fc78d222709c8906076581406e15868f0bcc16803": 1,  # SuperAcao p509
    },
    # 32 candidatos julgados; 1 de grau 2, 4 de grau 1
    'habilidade sobre transformações e conservações em sistemas': {
        "0c1b4cfa94befb13e08e693520ea211264f92d16ad7b264bfb8e8f05f22ccf39": 2,  # BNCC p117
        "a92d90e7b042259540025ce994e40c77529e2fbbdda8c4b74db3eb68f36af1f3": 1,  # Cotidiano p455
        "9472363aa19bd4c81a95ed7a5bb863459adf4869ae4f711ae796ae30aa2b86ba": 1,  # Investigar p24
        "3be43240f9ba94dddd20ca6f4b877ee4248afa7e6cca6b8191d1fe0034bde616": 1,  # Investigar p449
        "3ee6267384c34442c102e160f74571239a1b6f7035d780e09ec5a8f3c6305f40": 1,  # SuperAcao p446
    },
    # 23 candidatos julgados; 2 de grau 2, 5 de grau 1
    'fotossíntese nas plantas': {
        "cfc3706c13add91ff1585f42887f4559990e951b29b04749d0d0b358fe223834": 2,  # SuperAcao p88
        "ca597291818fcdc0134596ac1b3ea6ecc2980ffc6f1b7eb1d1d61ac5ad3e9a69": 2,  # SuperAcao p104
        "5e0b4a42ba0184ef6a24726a75461747214a7160b32ac1cbbea79feea8833585": 1,  # Cotidiano p472
        "ce41d59901e965bf969031a48ef6e5d57638bee3330356a5416e278acf29c99c": 1,  # Investigar p45
        "f5734f66d5204b71f993554557172536621ea6d96007e749635caaea9d7e79a8": 1,  # Investigar p115
        "eb5a46ad3459823b1438c89b02290a7115631a1885911550e37a97c142b8eb20": 1,  # Investigar p129
        "890f562c44cdba19089a5c15c1d692ffef7feaaf8f965b16480fd8b1d25f0645": 1,  # Investigar p273
    },
}


def relevant(query: str, *, minimum: int = 1) -> frozenset[str]:
    """``text_hash`` relevantes de uma consulta, a partir do grau dado."""
    return frozenset(
        h for h, grau in VECTOR_QRELS_V1.get(query, {}).items()
        if grau >= minimum
    )
