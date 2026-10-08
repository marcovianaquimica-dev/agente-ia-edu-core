/**
 * Os códigos do motor de redação, ditos em português.
 *
 * POR QUE ISTO EXISTE
 * ====================
 * O contrato do motor (`essay_engine_contract`) usa um vocabulário fechado em
 * caixa alta e sem acento — `OCR_DUVIDOSO`, `FUGA_AO_TEMA`, `CONCORDANCIA`.
 * Isso é certo para o contrato: identificador estável, fácil de versionar,
 * impossível de confundir.
 *
 * Errado é mostrá-lo ao aluno. Encontrado no ensaio da apresentação, a tela de
 * devolutiva exibia, logo abaixo da nota:
 *
 *     Nota total: 440 / 1000
 *     OCR_DUVIDOSO
 *
 * Projetado numa reunião, parece erro de sistema — e quem está avaliando
 * pergunta o que é, no meio da explicação sobre outra coisa.
 *
 * O CÓDIGO CONTINUA NO DADO. O que muda é o que a pessoa lê. Um código novo no
 * contrato, ainda sem entrada aqui, vira texto legível em vez do identificador
 * cru — e há teste para isso.
 */
(function (raiz) {
  'use strict';

  // O vocabulario de `Alert.code` (essay_engine_contract/v4.py).
  var ALERTAS = {
    FUGA_AO_TEMA: 'Fugiu do tema',
    TANGENCIAMENTO_AO_TEMA: 'Tangenciou o tema',
    TIPO_TEXTUAL: 'Fora do tipo de texto pedido',
    TIPO_TEXTUAL_PREDOMINANTE: 'Tipo de texto predominante não é o pedido',
    TEXTO_INSUFICIENTE: 'Texto curto demais',
    ANULACAO_PROPOSITAL: 'Anulação proposital',
    PARTE_DESCONECTADA_DO_TEMA: 'Trecho desconectado do tema',
    IDENTIFICACAO_INDEVIDA: 'Identificação indevida no texto',
    LINGUA_ESTRANGEIRA: 'Trecho em língua estrangeira',
    TEXTO_ILEGIVEL: 'Trecho ilegível',
    // "OCR" nao diz nada a um aluno nem a um diretor. O que importa e que a
    // leitura da FOTO ficou incerta - e que por isso vale conferir.
    OCR_DUVIDOSO: 'Leitura da foto incerta',
    POSSIVEL_DUPLICIDADE: 'Possível texto repetido',
  };

  // O vocabulario de `MechanicalOccurrence.category`.
  var CATEGORIAS = {
    ORTOGRAFIA: 'Ortografia',
    ACENTUACAO: 'Acentuação',
    PONTUACAO: 'Pontuação',
    CONCORDANCIA: 'Concordância',
    REGENCIA: 'Regência',
    CRASE: 'Crase',
    COLOCACAO_PRONOMINAL: 'Colocação pronominal',
    GRAFIA_DE_NOMES: 'Grafia de nomes',
    MAIUSCULAS_MINUSCULAS: 'Maiúsculas e minúsculas',
    SEPARACAO_SILABICA: 'Separação silábica',
  };

  /**
   * Transforma um identificador em frase, quando não houver tabela para ele.
   *
   * `UM_CODIGO_NOVO` -> `Um codigo novo`. Não é bonito, mas é legível — e o
   * ponto é nunca mostrar sublinhado e caixa alta a quem está lendo.
   */
  function legivel(code) {
    var texto = String(code).replace(/_/g, ' ').toLowerCase().trim();
    return texto.charAt(0).toUpperCase() + texto.slice(1);
  }

  function alerta(code) {
    if (!code) return '';
    return ALERTAS[code] || legivel(code);
  }

  function categoria(code) {
    if (!code) return '';
    return CATEGORIAS[code] || legivel(code);
  }

  var EssayRotulos = {
    alerta: alerta,
    categoria: categoria,
    ALERTAS: ALERTAS,
    CATEGORIAS: CATEGORIAS,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = EssayRotulos;
  } else {
    raiz.EssayRotulos = EssayRotulos;
  }
}(typeof globalThis !== 'undefined' ? globalThis : this));
