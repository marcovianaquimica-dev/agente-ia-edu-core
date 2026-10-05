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

test('depois de pedir ajuda, a fala nao volta a "tente primeiro"', () => {
  // Encontrado no ensaio: o apresentador erra de proposito para mostrar a
  // ajuda progressiva, e a tela volta a dizer "Tente primeiro por conta
  // propria" - contradizendo o que acabou de acontecer na frente de todos.
  const dados = recemAberto({ attempts: 1, hints_used: 1,
                              ajudas: [{ nivel: 1, tipo: 'CONCEITO', texto: 'x' }] });
  const texto = GuiadaUI.falaDoAssessor(dados, { pediuAjuda: true });
  assert.doesNotMatch(texto, /primeiro por conta|tente primeiro/i);
  assert.ok(texto.trim().length > 0);
});

test('a fala de quem pediu ajuda aponta para a dica e convida a tentar', () => {
  const dados = recemAberto({ attempts: 1, hints_used: 2 });
  const texto = GuiadaUI.falaDoAssessor(dados, { pediuAjuda: true });
  assert.match(texto, /dica|ajuda|abaixo|tente/i);
});

test('quem ainda nao fez nada continua recebendo o convite inicial', () => {
  // O convite morava solto no HTML de `aluno.js`, fora de qualquer teste.
  // Trazido para ca, a tela passa a ter UMA fonte de fala - e foi justamente
  // a existencia de duas que deixou a tela se contradizer no ensaio.
  const texto = GuiadaUI.falaDoAssessor(recemAberto(), null);
  assert.match(texto, /primeiro|conta pr/i);
});

test('quem reabre uma pratica ja comecada nao ouve "tente primeiro"', () => {
  // Recarregar a pagina no meio da pratica perde o ultimo resultado, mas nao
  // perde o historico: dizer "tente primeiro" a quem ja tentou duas vezes e
  // pediu ajuda contradiz o que esta desenhado logo abaixo, na mesma tela.
  const dados = recemAberto({ attempts: 2, hints_used: 1,
                              ajudas: [{ nivel: 1, tipo: 'CONCEITO', texto: 'x' }] });
  const texto = GuiadaUI.falaDoAssessor(dados, null);
  assert.doesNotMatch(texto, /primeiro por conta|tente primeiro/i);
  // Nem pode anunciar um erro que nao acabou de acontecer: ninguem respondeu
  // nada agora, a pagina so foi recarregada.
  assert.doesNotMatch(texto, /ainda n[aã]o [eé] essa/i);
  assert.ok(texto.trim().length > 0);
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

test('reabrir uma pratica JA CONCLUIDA nao convida a continuar tentando', () => {
  // Concluida e concluida: o item ja foi resolvido e registrado. Dizer
  // "continue de onde parou" aqui mandaria o aluno refazer o que acabou.
  const dados = recemAberto({ completed: true, solved_unaided: false,
                              hints_used: 3, attempts: 4 });
  const texto = GuiadaUI.falaDoAssessor(dados, null);
  assert.doesNotMatch(texto, /de onde voc[eê] parou|tente primeiro/i);
  assert.match(texto, /com a ajuda|juntos|agora/i);
});
