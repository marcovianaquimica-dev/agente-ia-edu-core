"""A BUSCA REAL NO YOUTUBE — §8, e o que dela dá para fazer hoje.

O §8 fecha com uma instrução que manda neste arquivo:

    "Se a integração atual não permitir busca e validação confiáveis,
     identifique a limitação e implemente apenas o que puder ser feito
     corretamente, sem inventar resultados."

O QUE DAVA PARA FAZER CORRETAMENTE
===================================
Tudo menos a chamada HTTP: a consulta, a validação, o descarte e o recorte
são determinísticos, e cada um é exercitado aqui com respostas REAIS da forma
que a API v3 devolve — injetadas, sem rede e sem chave.

O QUE NÃO DAVA, E ESTÁ DITO
============================
Não há `YOUTUBE_API_KEY` neste ambiente. Então a busca não acontece — e o
ponto deste arquivo é que ela NÃO ACONTECE DE FORMA DISTINGUÍVEL: até hoje o
provedor devolvia `[]`, que é a mesma coisa que "procurei e não achei nada". A
diferença entre "não procurei" e "não achei" é exatamente o tipo de coisa que
o §8.2 proíbe embaçar.

AS REGRAS DO §8 QUE VIRARAM TESTE
==================================
    2   não inventar título, URL, ID, duração ou conteúdo
    3   priorizar vídeos curtos e específicos
    8   exibir incorporado — então só vídeo que PERMITE incorporação
    9   não iniciar reprodução automaticamente
    13  tempo assistido não é evidência de domínio
    +   não enviar informações pessoais do estudante nas consultas
    +   sem legenda, não afirmar que se conhece o trecho
"""

from __future__ import annotations

import unittest

# Uma resposta de `search.list` como a API devolve: snippet SEM duração e SEM
# o estado de incorporação. É por isso que existe a segunda chamada.
BUSCA = {
    "items": [
        {"id": {"kind": "youtube#video", "videoId": "abc123"},
         "snippet": {"title": "Estequiometria em 7 minutos",
                     "description": "Mol, massa molar e proporção.",
                     "channelTitle": "Canal de Química",
                     "thumbnails": {"high": {"url": "https://i.ytimg.com/vi/abc123/hq.jpg"}}}},
        {"id": {"kind": "youtube#video", "videoId": "def456"},
         "snippet": {"title": "Aula completa de Estequiometria",
                     "description": "Aula de uma hora.",
                     "channelTitle": "Outro Canal",
                     "thumbnails": {"high": {"url": "https://i.ytimg.com/vi/def456/hq.jpg"}}}},
        {"id": {"kind": "youtube#channel", "channelId": "chan789"},
         "snippet": {"title": "Um canal, não um vídeo"}},
    ]
}

DETALHE = {
    "items": [
        {"id": "abc123",
         "status": {"embeddable": True, "privacyStatus": "public"},
         "contentDetails": {"duration": "PT7M31S", "caption": "true"},
         "snippet": {"defaultAudioLanguage": "pt-BR"}},
        {"id": "def456",
         "status": {"embeddable": True, "privacyStatus": "public"},
         "contentDetails": {"duration": "PT1H2M", "caption": "false"},
         "snippet": {"defaultAudioLanguage": "pt"}},
    ]
}


class ACONSULTANAOLEVADADOPESSOAL(unittest.TestCase):
    """§8: "Não envie informações pessoais do estudante nas consultas"."""

    def test_nenhum_parametro_carrega_identificador_do_aluno(self):
        from agente_ia_edu.services.youtube_busca import parametros_de_busca

        p = parametros_de_busca(consulta="Estequiometria mol", chave="K")
        juntos = " ".join(f"{k}={v}" for k, v in p.items()).lower()
        for pessoal in ("aluno_", "student", "@", "cpf", "matricula",
                        "external_user", "school_id"):
            with self.subTest(pessoal):
                self.assertNotIn(pessoal, juntos)

    def test_e_a_consulta_e_recusada_se_alguem_tentar(self):
        """Uma consulta montada com o identificador do aluno não sai daqui."""
        from agente_ia_edu.services.youtube_busca import (
            ConsultaComDadoPessoal,
            parametros_de_busca,
        )

        with self.assertRaises(ConsultaComDadoPessoal):
            parametros_de_busca(consulta="aluno_qa_123 estequiometria",
                                chave="K")

    def test_nem_um_email(self):
        from agente_ia_edu.services.youtube_busca import (
            ConsultaComDadoPessoal,
            parametros_de_busca,
        )

        with self.assertRaises(ConsultaComDadoPessoal):
            parametros_de_busca(consulta="duvida de alguem@escola.br",
                                chave="K")


class SOVIDEOQUEPODESEREMBUTIDO(unittest.TestCase):
    """§8.8: exibir incorporado ao Núcleo — então só o que permite."""

    def test_a_busca_ja_pede_so_o_que_e_embutivel(self):
        from agente_ia_edu.services.youtube_busca import parametros_de_busca

        p = parametros_de_busca(consulta="Estequiometria", chave="K")
        self.assertEqual("true", str(p.get("videoEmbeddable")).lower())
        self.assertEqual("video", p.get("type"))

    def test_e_o_que_voltar_nao_embutivel_e_descartado(self):
        from agente_ia_edu.services.youtube_busca import candidatos

        detalhe = {"items": [dict(DETALHE["items"][0],
                                  status={"embeddable": False,
                                          "privacyStatus": "public"})]}
        self.assertEqual([], candidatos(BUSCA, detalhe))

    def test_video_privado_tambem(self):
        from agente_ia_edu.services.youtube_busca import candidatos

        detalhe = {"items": [dict(DETALHE["items"][0],
                                  status={"embeddable": True,
                                          "privacyStatus": "private"})]}
        self.assertEqual([], candidatos(BUSCA, detalhe))


class NADAEINVENTADO(unittest.TestCase):
    """§8.2: não inventar título, URL, ID, duração ou conteúdo."""

    def test_sem_duracao_o_candidato_SAI_em_vez_de_ganhar_um_valor(self):
        from agente_ia_edu.services.youtube_busca import candidatos

        detalhe = {"items": [{"id": "abc123",
                              "status": {"embeddable": True,
                                         "privacyStatus": "public"},
                              "contentDetails": {"caption": "true"}}]}
        self.assertEqual([], candidatos(BUSCA, detalhe))

    def test_sem_titulo_tambem(self):
        from agente_ia_edu.services.youtube_busca import candidatos

        busca = {"items": [{"id": {"kind": "youtube#video", "videoId": "abc123"},
                            "snippet": {"description": "sem titulo"}}]}
        self.assertEqual([], candidatos(busca, DETALHE))

    def test_o_que_nao_e_video_nao_vira_video(self):
        from agente_ia_edu.services.youtube_busca import candidatos

        ids = [c["external_id"] for c in candidatos(BUSCA, DETALHE)]
        self.assertNotIn("chan789", ids)

    def test_a_duracao_vem_PARSEADA_da_resposta_e_nao_estimada(self):
        from agente_ia_edu.services.youtube_busca import duracao_em_segundos

        self.assertEqual(451, duracao_em_segundos("PT7M31S"))
        self.assertEqual(3720, duracao_em_segundos("PT1H2M"))
        self.assertEqual(45, duracao_em_segundos("PT45S"))

    def test_e_duracao_ilegivel_e_None_e_nunca_zero(self):
        """Zero segundos é um vídeo de duração zero. None é não saber."""
        from agente_ia_edu.services.youtube_busca import duracao_em_segundos

        for ruim in ("", None, "sete minutos", "P1D2X"):
            with self.subTest(repr(ruim)):
                self.assertIsNone(duracao_em_segundos(ruim))

    def test_a_url_e_montada_do_id_que_a_API_devolveu(self):
        from agente_ia_edu.services.youtube_busca import candidatos

        c = candidatos(BUSCA, DETALHE)[0]
        self.assertIn(c["external_id"], c["url"])


class CURTOPRIMEIRO(unittest.TestCase):
    """§8.3: priorizar vídeos curtos e específicos."""

    def test_o_mais_curto_vem_antes(self):
        from agente_ia_edu.services.youtube_busca import candidatos

        duracoes = [c["duration_seconds"] for c in candidatos(BUSCA, DETALHE)]
        self.assertEqual(sorted(duracoes), duracoes)

    def test_e_a_busca_ja_pede_duracao_curta_ou_media(self):
        from agente_ia_edu.services.youtube_busca import parametros_de_busca

        p = parametros_de_busca(consulta="Estequiometria", chave="K")
        self.assertIn(str(p.get("videoDuration")), ("short", "medium"))


class SEMLEGENDANAOSEAFIRMAOTRECHO(unittest.TestCase):
    """§8: sem transcrição, o Edu não diz que conhece um trecho."""

    def test_a_disponibilidade_de_legenda_vem_registrada(self):
        from agente_ia_edu.services.youtube_busca import candidatos

        por_id = {c["external_id"]: c for c in candidatos(BUSCA, DETALHE)}
        self.assertTrue(por_id["abc123"]["transcricao_disponivel"])
        self.assertFalse(por_id["def456"]["transcricao_disponivel"])

    def test_e_nao_se_presume_legenda_quando_a_API_nao_diz(self):
        from agente_ia_edu.services.youtube_busca import candidatos

        detalhe = {"items": [{"id": "abc123",
                              "status": {"embeddable": True,
                                         "privacyStatus": "public"},
                              "contentDetails": {"duration": "PT7M31S"}}]}
        c = candidatos(BUSCA, detalhe)[0]
        self.assertFalse(c["transcricao_disponivel"])


class OEMBUTIDONAOTOCASOZINHO(unittest.TestCase):
    """§8.9: não iniciar reprodução automaticamente."""

    def test_a_url_de_embutir_nao_pede_autoplay(self):
        from agente_ia_edu.services.youtube_busca import url_de_embutir

        u = url_de_embutir("abc123")
        self.assertNotIn("autoplay=1", u)
        self.assertIn("abc123", u)

    def test_e_ela_nao_promete_esconder_o_player(self):
        """§8: "não prometa [...] ocultar controles nativos do player"."""
        from agente_ia_edu.services.youtube_busca import url_de_embutir

        self.assertNotIn("controls=0", url_de_embutir("abc123"))


class TEMPOASSISTIDONAOEEVIDENCIA(unittest.TestCase):
    """§8.13, varrido na fonte: o módulo não sabe medir domínio."""

    def _fonte(self) -> str:
        import pathlib

        from _fonte import codigo

        raiz = pathlib.Path(__file__).resolve().parent.parent
        return codigo(raiz / "src/agente_ia_edu/services/youtube_busca.py")

    def test_o_modulo_nao_fala_de_dominio_nem_de_evidencia(self):
        from _fonte import menciona

        fonte = self._fonte()
        for proibido in ("mastery", "evidence", "dominio", "accuracy",
                         "watch_time", "tempo_assistido", "concluido"):
            with self.subTest(proibido):
                self.assertFalse(menciona(fonte, proibido))

    def test_e_nenhum_candidato_carrega_campo_de_progresso(self):
        from agente_ia_edu.services.youtube_busca import candidatos

        for c in candidatos(BUSCA, DETALHE):
            for chave in c:
                with self.subTest(chave):
                    self.assertNotIn("watch", chave.lower())
                    self.assertNotIn("progress", chave.lower())


class SEMCHAVEADIFERENCAAPARECE(unittest.TestCase):
    """"Não procurei" e "não achei nada" não podem ser a mesma resposta."""

    def test_a_disponibilidade_diz_que_a_busca_real_esta_desligada(self):
        from agente_ia_edu.services.youtube_busca import disponibilidade

        d = disponibilidade(chave=None)
        self.assertFalse(d["busca_real"])
        self.assertTrue(d["motivo"])
        self.assertTrue(d["o_que_falta"])

    def test_e_com_chave_ela_diz_que_esta_ligada(self):
        from agente_ia_edu.services.youtube_busca import disponibilidade

        self.assertTrue(disponibilidade(chave="uma-chave")["busca_real"])

    def test_o_provedor_sem_chave_NAO_devolve_lista_vazia_em_silencio(self):
        import asyncio

        from agente_ia_edu.services.video_discovery import (
            YouTubeDiscoveryProvider,
        )
        from agente_ia_edu.services.youtube_busca import BuscaIndisponivel

        p = YouTubeDiscoveryProvider(api_key=None)
        self.assertFalse(p.esta_configurado)
        with self.assertRaises(BuscaIndisponivel):
            asyncio.run(p.search("Estequiometria"))

    def test_o_que_falta_nomeia_a_variavel_de_ambiente(self):
        from agente_ia_edu.services.youtube_busca import disponibilidade

        texto = " ".join(disponibilidade(chave=None)["o_que_falta"])
        self.assertIn("YOUTUBE_API_KEY", texto)


class ABUSCAACONTECESEMREDE(unittest.IsolatedAsyncioTestCase):
    """O cliente é exercitado com as duas respostas reais, injetadas."""

    class _HttpFalso:
        def __init__(self, respostas):
            self.respostas = list(respostas)
            self.chamadas = []

        async def get(self, url, params=None):
            self.chamadas.append((url, dict(params or {})))
            return self.respostas.pop(0)

    async def test_duas_chamadas_a_busca_e_o_detalhe(self):
        from agente_ia_edu.services.youtube_busca import buscar

        http = self._HttpFalso([BUSCA, DETALHE])
        achados = await buscar("Estequiometria", chave="K", http=http)
        self.assertEqual(2, len(http.chamadas))
        self.assertTrue(achados)

    async def test_e_a_segunda_pede_exatamente_os_ids_da_primeira(self):
        from agente_ia_edu.services.youtube_busca import buscar

        http = self._HttpFalso([BUSCA, DETALHE])
        await buscar("Estequiometria", chave="K", http=http)
        pedidos = http.chamadas[1][1].get("id", "")
        self.assertIn("abc123", pedidos)
        self.assertIn("def456", pedidos)
        self.assertNotIn("chan789", pedidos)

    async def test_nenhuma_chamada_leva_a_chave_na_URL_do_log(self):
        """A chave vai em parâmetro, e o teste garante que ela não vaza no path."""
        from agente_ia_edu.services.youtube_busca import buscar

        http = self._HttpFalso([BUSCA, DETALHE])
        await buscar("Estequiometria", chave="SEGREDO", http=http)
        for url, _p in http.chamadas:
            with self.subTest(url):
                self.assertNotIn("SEGREDO", url)

    async def test_busca_sem_nenhum_video_devolve_vazio_e_nao_chama_detalhe(self):
        from agente_ia_edu.services.youtube_busca import buscar

        http = self._HttpFalso([{"items": []}])
        self.assertEqual([], await buscar("x", chave="K", http=http))
        self.assertEqual(1, len(http.chamadas))


class OQUEFALTAESTAREGISTRADO(unittest.TestCase):
    """O §8 manda IDENTIFICAR a limitação, e não só contorná-la."""

    def _doc(self) -> str:
        import pathlib

        caminho = (pathlib.Path(__file__).resolve().parent.parent
                   / "docs/youtube-o-que-falta-para-ligar.md")
        self.assertTrue(caminho.exists(),
                        "o registro do que falta para ligar a busca não existe")
        return caminho.read_text(encoding="utf-8")

    def test_o_documento_nomeia_a_credencial(self):
        self.assertIn("YOUTUBE_API_KEY", self._doc())

    def test_e_diz_o_que_ainda_nao_tem_superficie_no_aluno(self):
        doc = self._doc().lower()
        self.assertIn("aluno", doc)


if __name__ == "__main__":
    unittest.main()
