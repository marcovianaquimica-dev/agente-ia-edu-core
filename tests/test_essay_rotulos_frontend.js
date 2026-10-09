/**
 * Os códigos do motor de redação, ditos em português.
 *
 * ACHADO NO ENSAIO DA APRESENTAÇÃO
 * =================================
 * A tela de devolutiva — a mais forte do módulo — exibia, logo abaixo da nota:
 *
 *     Nota total: 440 / 1000
 *     OCR_DUVIDOSO
 *
 * E, na revisão mecânica: CONCORDANCIA, ORTOGRAFIA, PONTUACAO.
 *
 * São códigos do contrato do motor, em caixa alta e sem acento. Projetados
 * numa reunião, parecem erro de sistema — e quem está avaliando o produto
 * pergunta o que é, no meio da explicação sobre outra coisa.
 *
 * O código continua no dado; o que muda é o que a pessoa lê.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const Rotulos = require(path.join(__dirname, '..', 'src', 'agente_ia_edu',
                                  'web', 'essay-rotulos.js'));

// O vocabulario fechado do contrato (essay_engine_contract/v4.py).
const ALERTAS = [
  'FUGA_AO_TEMA', 'TANGENCIAMENTO_AO_TEMA', 'TIPO_TEXTUAL',
  'TIPO_TEXTUAL_PREDOMINANTE', 'TEXTO_INSUFICIENTE', 'ANULACAO_PROPOSITAL',
  'PARTE_DESCONECTADA_DO_TEMA', 'IDENTIFICACAO_INDEVIDA',
  'LINGUA_ESTRANGEIRA', 'TEXTO_ILEGIVEL', 'OCR_DUVIDOSO',
  'POSSIVEL_DUPLICIDADE',
];

test('todo codigo de alerta do contrato tem rotulo proprio', () => {
  for (const code of ALERTAS) {
    const r = Rotulos.alerta(code);
    assert.ok(r && r.length > 0, `sem rotulo: ${code}`);
    assert.notEqual(r, code, `${code} saiu sem traducao`);
  }
});

test('nenhum rotulo sai em caixa alta com sublinhado', () => {
  for (const code of ALERTAS) {
    assert.doesNotMatch(Rotulos.alerta(code), /[A-Z]{3,}_/,
                        `${code} ainda parece codigo`);
  }
});

test('o rotulo do OCR fala de leitura da foto, nao de OCR', () => {
  const r = Rotulos.alerta('OCR_DUVIDOSO').toLowerCase();
  assert.doesNotMatch(r, /ocr/);
  assert.match(r, /foto|imagem|leitura|digitaliza/);
});

test('um codigo desconhecido nao quebra nem vaza', () => {
  // O contrato pode ganhar um codigo novo antes de esta tabela: o fallback
  // precisa ser legivel, nao o identificador cru.
  const r = Rotulos.alerta('CODIGO_QUE_NAO_EXISTE_AINDA');
  assert.doesNotMatch(r, /_/);
  assert.ok(r.length > 0);
});

test('codigo vazio ou nulo devolve vazio, nao "undefined"', () => {
  assert.equal(Rotulos.alerta(''), '');
  assert.equal(Rotulos.alerta(null), '');
});

test('as categorias de revisao mecanica tambem sao traduzidas', () => {
  for (const c of ['ORTOGRAFIA', 'ACENTUACAO', 'PONTUACAO', 'CONCORDANCIA',
                   'REGENCIA', 'CRASE']) {
    const r = Rotulos.categoria(c);
    assert.ok(r && r.length > 0, `sem rotulo: ${c}`);
    assert.doesNotMatch(r, /^[A-Z]+$/, `${c} saiu em caixa alta`);
  }
});

test('categoria desconhecida vira texto legivel', () => {
  const r = Rotulos.categoria('ALGO_NOVO');
  assert.doesNotMatch(r, /_/);
});

test('os rotulos sao frases curtas - cabem num selo', () => {
  for (const code of ALERTAS) {
    assert.ok(Rotulos.alerta(code).length <= 42,
              `rotulo longo demais para um selo: ${code}`);
  }
});

test('os codigos que ANULAM a redacao soam serios, nao decorativos', () => {
  // Fuga ao tema e anulacao proposital zeram a nota: o rotulo nao pode
  // parecer um aviso qualquer.
  assert.match(Rotulos.alerta('FUGA_AO_TEMA').toLowerCase(), /tema/);
  assert.match(Rotulos.alerta('TEXTO_INSUFICIENTE').toLowerCase(), /curt|insufici|linha/);
});
