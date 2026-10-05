/**
 * COMPORTAMENTO da prática guiada na tela — executando o código, não lendo-o.
 *
 * POR QUE ESTE ARQUIVO É DIFERENTE DOS OUTROS .js DESTE PROJETO
 * =============================================================
 * Os testes .js daqui varrem o fonte com expressões regulares: provam que uma
 * string existe, não que a tela funciona. E, pior, ninguém os executa — não há
 * runner. Um teste que não roda não é um teste.
 *
 * Este carrega `aluno-guiada.js` de verdade e chama suas funções. Ele roda com
 * `node --test`, sem dependência nova, e entra na suíte pela ponte em
 * `tests/test_frontend_comportamental.py`.
 *
 * O QUE AINDA FALTA (dívida declarada)
 * =====================================
 * Clique real em DOM. Isso exigiria jsdom, que não está instalado; o navegador
 * cobre essa camada neste bloco. O que está aqui é a decisão de interface —
 * qual botão fica ativo, que frase aparece — que antes não tinha teste nenhum.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const GuiadaUI = require(path.join(__dirname, '..', 'src', 'agente_ia_edu',
                                   'web', 'aluno-guiada.js'));

/** O payload que a API devolve, no estado inicial. */
function recemAberto(extra = {}) {
  return Object.assign({
    item_key: 'BAL-CONSERVACAO-1',
    question: 'Em qual destas equações os átomos fecham?',
    options: [{ key: 'A', text: 'H₂ + O₂ → H₂O' },
              { key: 'B', text: '2 H₂ + O₂ → 2 H₂O' }],
    ajudas: [],
    ajudas_disponiveis: 4,
    attempts: 0,
    hints_used: 0,
    max_hint_level: 0,
    completed: false,
    solved_unaided: false,
  }, extra);
}

test('quem acabou de abrir pode tentar e pode pedir ajuda', () => {
  const v = GuiadaUI.estado(recemAberto());
  assert.equal(v.podeResponder, true);
  assert.equal(v.podePedirAjuda, true);
  assert.equal(v.concluido, false);
});

test('sem escolher alternativa, o botao de responder fica desabilitado', () => {
  const v = GuiadaUI.estado(recemAberto(), { escolha: null });
  assert.equal(v.responderHabilitado, false);
  const comEscolha = GuiadaUI.estado(recemAberto(), { escolha: 'A' });
  assert.equal(comEscolha.responderHabilitado, true);
});

test('usadas todas as ajudas, nao se pede mais', () => {
  const v = GuiadaUI.estado(recemAberto({ hints_used: 4, ajudas_disponiveis: 4 }));
  assert.equal(v.podePedirAjuda, false);
});

test('concluido, nao se responde nem se pede ajuda de novo', () => {
  const v = GuiadaUI.estado(recemAberto({ completed: true }));
  assert.equal(v.podeResponder, false);
  assert.equal(v.podePedirAjuda, false);
  assert.equal(v.concluido, true);
});

test('o CTA do fim anuncia a tentativa SOZINHO', () => {
  const v = GuiadaUI.estado(recemAberto({ completed: true }));
  assert.match(v.cta, /sozinho/i);
});

test('errar nao recebe a palavra "errado"', () => {
  const texto = GuiadaUI.falaDoAssessor(recemAberto({ attempts: 1 }),
                                        { correct: false });
  assert.doesNotMatch(texto, /errado|errou|falhou|fraco/i);
  assert.ok(texto.trim().length > 0);
});

test('errar convida a olhar de novo, e oferece ajuda', () => {
  const texto = GuiadaUI.falaDoAssessor(recemAberto({ attempts: 1 }),
                                        { correct: false });
  assert.match(texto, /ajuda|dica|de novo|outra vez|tente/i);
});

test('acertar SEM ajuda recebe elogio legitimo', () => {
  const dados = recemAberto({ completed: true, solved_unaided: true,
                              hints_used: 0, attempts: 1 });
  const texto = GuiadaUI.falaDoAssessor(dados, { correct: true });
  assert.match(texto, /sozinh|sem ajuda|de primeira/i);
});

test('acertar DEPOIS de ajuda nao recebe elogio falso', () => {
  const dados = recemAberto({ completed: true, solved_unaided: false,
                              hints_used: 4, attempts: 5 });
  const texto = GuiadaUI.falaDoAssessor(dados, { correct: true });
  // A frase nao pode dizer que ele conseguiu sozinho - ele nao conseguiu.
  assert.doesNotMatch(texto, /sozinho|sem ajuda|de primeira/i);
  // E tem de apontar o proximo passo honesto: tentar um sem ajuda.
  assert.match(texto, /com a ajuda|juntos|agora/i);
});

test('a fala de quem acertou com ajuda nao humilha', () => {
  const dados = recemAberto({ completed: true, solved_unaided: false,
                              hints_used: 4 });
  const texto = GuiadaUI.falaDoAssessor(dados, { correct: true });
  assert.doesNotMatch(texto, /errado|falhou|fraco|nao conseguiu/i);
});

test('o contador de ajuda e legivel para o aluno', () => {
  const v = GuiadaUI.estado(recemAberto({ hints_used: 2, ajudas_disponiveis: 4 }));
  assert.match(v.resumoDaAjuda, /2/);
  assert.match(v.resumoDaAjuda, /4/);
});

test('sem ajuda usada, nao se anuncia contador nenhum', () => {
  const v = GuiadaUI.estado(recemAberto());
  assert.equal(v.resumoDaAjuda, '');
});

test('so as ajudas liberadas sao desenhadas, na ordem', () => {
  const dados = recemAberto({
    hints_used: 2,
    ajudas: [{ nivel: 1, tipo: 'CONCEITO', texto: 'primeira' },
             { nivel: 2, tipo: 'ONDE_OLHAR', texto: 'segunda' }],
  });
  const v = GuiadaUI.estado(dados);
  assert.deepEqual(v.ajudas.map((a) => a.texto), ['primeira', 'segunda']);
});

test('a tela nunca sabe qual e a alternativa correta antes da hora', () => {
  const v = GuiadaUI.estado(recemAberto());
  assert.equal(JSON.stringify(v).includes('correct_option'), false);
  assert.equal(v.correta, undefined);
});

test('depois de concluir, a correta pode ser apontada', () => {
  const v = GuiadaUI.estado(recemAberto({ completed: true, correct_option: 'B' }));
  assert.equal(v.correta, 'B');
});

test('retomar com tentativas e ajudas preserva os dois contadores', () => {
  const v = GuiadaUI.estado(recemAberto({ attempts: 3, hints_used: 2 }));
  assert.equal(v.tentativas, 3);
  assert.equal(v.ajudasUsadas, 2);
});

test('um payload vazio nao quebra a tela', () => {
  const v = GuiadaUI.estado(null);
  assert.equal(v.podeResponder, false);
  assert.equal(v.concluido, false);
});
