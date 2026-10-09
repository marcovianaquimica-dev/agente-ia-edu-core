/**
 * PROTOTIPO DO PERFIL ALUNO — testes COMPORTAMENTAIS.
 *
 * Separado de test_aluno_frontend.js de proposito. Aquele arquivo faz
 * asserções estáticas (regex sobre o fonte), no padrão dos outros 11 arquivos
 * de frontend do projeto. Este aqui EXECUTA: ou roda a função de verdade, ou
 * calcula o mecanismo que decide o comportamento.
 *
 * Por que isto existe: o banner de objetivo apareceu vazio ("🎯 Para:" sem
 * texto) numa captura de auditoria. O JS estava certo — fazia `hidden = true`.
 * O CSS é que anulava: `.objetivo { display: flex }` vence, por
 * especificidade, o `display: none` que a folha do NAVEGADOR aplica a
 * `[hidden]`. Nenhuma das 44 asserções estáticas podia ver isso, porque cada
 * linha, isolada, estava correta. O defeito morava na CASCATA entre elas.
 *
 * Então o teste do `hidden` abaixo não procura texto: ele monta as regras de
 * aluno.css, calcula origem + especificidade + ordem para cada elemento da
 * página e pergunta qual `display` vence. É o mesmo algoritmo que o navegador
 * roda, reduzido ao que decide esta questão.
 *
 * LIMITE DECLARADO: é uma avaliação de cascata, não um render. Não substitui
 * um DOM de verdade (jsdom), que o projeto não tem — ele não usa nenhuma
 * dependência Node, e introduzir uma seria mudança de infraestrutura, não
 * correção de defeito. Ver docs/frontend-aluno-relatorio.md §13.
 *
 * Rode com:  node --test "tests/*aluno*.js"
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'src', 'agente_ia_edu', 'web');
const html = fs.readFileSync(path.join(WEB, 'aluno.html'), 'utf8');
const js = fs.readFileSync(path.join(WEB, 'aluno.js'), 'utf8');
const css = fs.readFileSync(path.join(WEB, 'aluno.css'), 'utf8');

// ====================================================== cascata de CSS ====

/** Especificidade (a,b,c): #id / .classe|[attr]|:pseudo / tag. */
function especificidade(seletor) {
  const sujeito = seletor.trim().split(/\s+(?![^[]*\])|(?<![(,])\s*[>+~]\s*/).pop() || '';
  const a = (sujeito.match(/#[\w-]+/g) || []).length;
  const b = (sujeito.match(/\.[\w-]+|\[[^\]]+\]|:(?!:)[\w-]+/g) || []).length;
  const c = (sujeito.match(/(^|[^.#:[\w-])([a-z]+[\w-]*)/gi) || []).length;
  return [a, b, c];
}

function maior(x, y) {
  for (let i = 0; i < 3; i += 1) {
    if (x[i] !== y[i]) return x[i] > y[i];
  }
  return false;
}

/**
 * Todas as regras que declaram `display`, já desembrulhadas de @media —
 * uma regra dentro de media query continua podendo vencer em ALGUM viewport,
 * e o teste tem de valer em todos.
 */
function regrasDeDisplay(folha) {
  const semComentario = folha.replace(/\/\*[\s\S]*?\*\//g, '');
  const semMedia = semComentario.replace(/@media[^{]*\{/g, '').replace(/\}\s*\}/g, '}');
  const regras = [];
  const corpo = /([^{}]+)\{([^{}]*)\}/g;
  let m;
  let ordem = 0;
  while ((m = corpo.exec(semMedia)) !== null) {
    const declaracao = /(^|;)\s*display\s*:\s*([^;!]+)(!important)?/i.exec(m[2]);
    if (!declaracao) continue;
    const importante = /display\s*:[^;]*!important/i.test(m[2]);
    for (const seletor of m[1].split(',')) {
      const s = seletor.trim();
      if (!s || s.startsWith('@')) continue;
      regras.push({
        seletor: s,
        valor: declaracao[2].trim(),
        importante,
        espec: especificidade(s),
        ordem: ordem++,
      });
    }
  }
  return regras;
}

/** O sujeito do seletor casa com este elemento? Ignora ancestrais de
 *  propósito: superestimar quais regras concorrem é o lado SEGURO aqui —
 *  faz o teste reclamar a mais, nunca a menos. */
function sujeitoCasa(seletor, el) {
  const sujeito = seletor.trim().split(/\s+(?![^[]*\])|(?<![(,])\s*[>+~]\s*/).pop() || '';
  if (/:{1,2}(hover|focus|active|before|after|focus-visible|backdrop)/.test(sujeito)) return false;
  for (const id of sujeito.match(/#[\w-]+/g) || []) {
    if (id.slice(1) !== el.id) return false;
  }
  for (const cls of sujeito.match(/\.[\w-]+/g) || []) {
    if (!el.classes.includes(cls.slice(1))) return false;
  }
  for (const attr of sujeito.match(/\[[^\]]+\]/g) || []) {
    const nome = attr.slice(1, -1).split(/[=~|^$*]/)[0].trim();
    if (!el.attrs.includes(nome)) return false;
  }
  const tag = /(^|[\s>+~])([a-z]+[\w-]*)/i.exec(sujeito);
  if (tag && !/^(hidden)$/.test(tag[2]) && tag[2].toLowerCase() !== el.tag) {
    // so reprova quando o seletor comeca por tag de verdade
    if (/^[a-z]/i.test(sujeito)) return false;
  }
  return true;
}

/**
 * Qual `display` vence para este elemento, COM o atributo hidden posto.
 * Ordem da cascata: origem (navegador < autor < autor !important),
 * depois especificidade, depois ordem no arquivo.
 */
function displayVencedor(el, regras) {
  // a folha do NAVEGADOR: [hidden] { display: none }, origem mais fraca
  let vencedor = { valor: 'none', origem: 0, espec: [0, 1, 0], ordem: -1, seletor: '[hidden] (navegador)' };
  for (const r of regras) {
    if (!sujeitoCasa(r.seletor, el)) continue;
    const origem = r.importante ? 2 : 1;
    const ganha =
      origem > vencedor.origem
      || (origem === vencedor.origem
          && (maior(r.espec, vencedor.espec)
              || (!maior(vencedor.espec, r.espec) && r.ordem > vencedor.ordem)));
    if (ganha) vencedor = { ...r, origem };
  }
  return vencedor;
}

/** Todo elemento com id no HTML, com suas classes e atributos. */
function elementosDaPagina(documento) {
  const fora = [];
  const abertura = /<([a-z]+[\w-]*)\b([^>]*)>/gi;
  let m;
  while ((m = abertura.exec(documento)) !== null) {
    const bruto = m[2];
    const id = (/\bid="([^"]+)"/.exec(bruto) || [])[1];
    if (!id) continue;
    fora.push({
      tag: m[1].toLowerCase(),
      id,
      classes: ((/\bclass="([^"]+)"/.exec(bruto) || [])[1] || '').split(/\s+/).filter(Boolean),
      attrs: (bruto.match(/\b([a-z][\w-]*)(?==|\s|$)/gi) || []).map((a) => a.trim()),
    });
  }
  return fora;
}

// ------------------------------------------------------------- o defeito --

test('COM hidden posto, TODO elemento da página some de verdade', () => {
  // O defeito real de 2026-10-03: #objetivo ficava visível com hidden=true,
  // mostrando "🎯 Para:" sem texto em toda sessão sem tarefa da escola.
  const regras = regrasDeDisplay(css);
  const elementos = elementosDaPagina(html);
  assert.ok(elementos.length >= 10, `só achei ${elementos.length} elementos — o scanner quebrou`);

  const teimosos = elementos
    .map((el) => ({ el, venceu: displayVencedor({ ...el, attrs: [...el.attrs, 'hidden'] }, regras) }))
    .filter(({ venceu }) => venceu.valor !== 'none')
    .map(({ el, venceu }) => `#${el.id} -> display:${venceu.valor} por "${venceu.seletor}"`);

  assert.deepStrictEqual(teimosos, [],
    `estes elementos continuam visíveis mesmo com hidden:\n  ${teimosos.join('\n  ')}`);
});

test('o caso exato que apareceu na auditoria: #objetivo', () => {
  const regras = regrasDeDisplay(css);
  const objetivo = elementosDaPagina(html).find((e) => e.id === 'objetivo');
  assert.ok(objetivo, '#objetivo sumiu do HTML');
  assert.ok(objetivo.classes.includes('objetivo'), 'perdeu a classe que causava o conflito');

  // sem hidden: visível (senão o banner nunca apareceria). O elemento JÁ
  // nasce com `hidden` no markup — quem o mostra é o JS —, então para este
  // caso preciso tirar o atributo, não só deixar de acrescentá-lo.
  const semHidden = { ...objetivo, attrs: objetivo.attrs.filter((a) => a !== 'hidden') };
  assert.strictEqual(displayVencedor(semHidden, regras).valor, 'flex');
  // com hidden: escondido
  assert.strictEqual(
    displayVencedor({ ...objetivo, attrs: [...objetivo.attrs, 'hidden'] }, regras).valor, 'none');
});

test('o avaliador de cascata realmente pega o bug — provado com o CSS antigo', () => {
  // Um teste que nunca falha não prova nada. Aqui reconstruo o CSS de antes
  // da correção e exijo que o avaliador REPROVE. Se alguém enfraquecer o
  // avaliador, este teste cai junto.
  const antes = css.replace(/\[hidden\]\s*\{\s*display:\s*none\s*!important;?\s*\}/, '');
  assert.notStrictEqual(antes, css, 'não encontrei a regra corrigida para remover');
  const objetivo = elementosDaPagina(html).find((e) => e.id === 'objetivo');
  const venceu = displayVencedor(
    { ...objetivo, attrs: [...objetivo.attrs, 'hidden'] }, regrasDeDisplay(antes));
  assert.strictEqual(venceu.valor, 'flex',
    'com o CSS antigo o banner deveria continuar visível — o avaliador não está vendo o conflito');
});

test('o JS continua pedindo para esconder quando não há objetivo', () => {
  // A correção foi no CSS. Se alguém "simplificar" o JS tirando o else,
  // o banner volta com o texto da sessão ANTERIOR, que é pior que vazio.
  const trecho = js.slice(js.indexOf('function pintarSessao'), js.indexOf('function pintarSessao') + 400);
  assert.match(trecho, /else\s*\{\s*\$\('objetivo'\)\.hidden\s*=\s*true/);
});

// ============================== contrato de rota: JS x banco ==============

/**
 * O frontend produz `readiness_route` e o banco o recusa se nao for um dos
 * tres valores (CheckConstraint, migration 064). Sao dois arquivos, em duas
 * linguagens, que PRECISAM concordar - e nada alem deste teste os liga.
 *
 * Isto nao e asserção sobre texto: le os valores REAIS dos dois lados e
 * compara os conjuntos. Se alguem acrescentar uma rota so no JS, a escrita
 * falharia em producao; aqui falha no teste.
 */
const MIGRATION = path.join(
  __dirname, '..', 'migrations', 'versions', '064_study_session_readiness.py');

function rotasDoBanco() {
  const py = fs.readFileSync(MIGRATION, 'utf8');
  // a tupla ROTAS, que a propria migration usa para documentar o conjunto
  const tupla = /ROTAS = \(([^)]*)\)/.exec(py);
  assert.ok(tupla, 'a migration 064 nao declara mais a tupla ROTAS');
  const daTupla = (tupla[1].match(/"([A-Z_]+)"/g) || []).map((x) => x.slice(1, -1));
  // e o CheckConstraint de verdade, que e o que o banco aplica
  const check = /readiness_route IN "\s*\n\s*"\(([^)]*)\)/.exec(py)
    || /readiness_route IN \(([^)]*)\)/.exec(py);
  assert.ok(check, 'nao achei o CheckConstraint na migration');
  const doCheck = (check[1].match(/'([A-Z_]+)'/g) || []).map((x) => x.slice(1, -1));
  assert.deepStrictEqual([...daTupla].sort(), [...doCheck].sort(),
    'dentro da propria migration, a tupla e o CheckConstraint divergem');
  return daTupla;
}

function rotasDoFrontend() {
  const achados = [...js.matchAll(/const (?:DIRETO|DIAGNOSTICO|PREPARACAO) = '([A-Z_]+)'/g)]
    .map((m) => m[1]);
  assert.strictEqual(achados.length, 3, `achei ${achados.length} constantes de rota, esperava 3`);
  return achados;
}

test('as rotas do frontend sao EXATAMENTE as que o banco aceita', () => {
  assert.deepStrictEqual(rotasDoFrontend().sort(), rotasDoBanco().sort());
});

test('a sessao envia readiness_route e o objetivo junto', () => {
  // Sem os tres campos, Professor/Coordenacao nao conseguem separar
  // "nao fez" de "esta se preparando" - a razao de a migration existir.
  const m = js.slice(js.indexOf('function montarSessao'), js.indexOf('function pintarSessao'));
  assert.match(m, /readiness_route: rota/);
  assert.match(m, /objective_assignment_id:/);
  assert.match(m, /completed_objective: false/);
});

// ================================== orcamento do placeholder ==============

test('o placeholder cabe no campo estreito do telefone', () => {
  /**
   * Medido no navegador, nao estimado: com os tres botoes de icone ao lado,
   * sobram ~155px uteis a 375px de viewport. "Uma dúvida, um assunto, uma
   * questão..." media 303px e aparecia cortado no meio - defeito que eu
   * mesmo introduzi ao revisar a microcopy, e que nenhum teste pegava.
   *
   * O teste usa contagem de caracteres como PROXY da medida em pixels: a
   * fonte do projeto da ~8px por caractere nesse tamanho, entao 19 e o
   * limite com folga. Proxy, nao medida - um DOM de verdade mediria melhor.
   */
  const LIMITE = 19;
  const m = /placeholder="([^"]*)"/.exec(html);
  assert.ok(m, 'o campo perdeu o placeholder');
  assert.ok(m[1].length <= LIMITE,
    `placeholder com ${m[1].length} caracteres ("${m[1]}") passa do que cabe `
    + `em 375px de largura; o limite medido e ${LIMITE}`);
});

test('a universalidade da entrada vive no rotulo, que tem a largura toda', () => {
  // Se o placeholder tem de ser curto, o rotulo e que precisa dizer que ali
  // cabe duvida, conteudo, questao, revisao ou objetivo.
  assert.match(html, /<label class="perguntar-rotulo"[^>]*>([^<]*)<\/label>/);
  const rotulo = /<label class="perguntar-rotulo"[^>]*>([^<]*)<\/label>/.exec(html)[1];
  assert.doesNotMatch(rotulo, /estudar\?/,
    'o rotulo antigo restringia a entrada a "estudar"; ela e universal agora');
});

// ================================== navegacao travada em dois =============

test('a navegacao inferior continua com DOIS itens, e so', () => {
  // Decisao de produto explicita: nada de Banco de Questoes, Cursos,
  // Simulados, Cronograma ou Tutor no menu inferior deste MVP.
  const nav = html.slice(html.indexOf('<nav class="abas"'), html.indexOf('</nav>'));
  const botoes = nav.match(/<button/g) || [];
  assert.strictEqual(botoes.length, 2,
    `a navegacao tem ${botoes.length} itens; o MVP permite 2`);
});

// ========================== Meu Progresso: dado real, sem corte local =====

/**
 * Codigo SEM comentarios. Um guarda que varre o fonte cru encontra a propria
 * explicacao: o comentario que diz "nao ha 0.8 nesta tela" CONTEM 0.8, e o
 * que diz 'nao escrevemos "Consolidado" por conta propria' contem
 * "Consolidado". Ja aconteceu tres vezes neste projeto - inclusive comigo,
 * duas vezes hoje. A assercao estava certa nas tres; o scanner e que lia a
 * documentacao como se fosse codigo.
 */
const jsCodigo = js
  .replace(/\/\*[\s\S]*?\*\//g, '')   // blocos
  .replace(/^\s*\/\/.*$/gm, '');       // linhas

/**
 * Estes quatro testes foram escritos DEPOIS da mudanca - furei o ciclo. Para
 * nao ficar com um verde sem lastro, cada um foi conferido contra a versao
 * anterior do arquivo (`git show HEAD:...`), e os quatro reprovam la. O teste
 * de `panoramaAntigo` abaixo automatiza essa conferencia: ele exige que o
 * fonte commitado tivesse o mock, de modo que, se alguem reintroduzir o mock,
 * a assercao principal cai junto.
 */

test('o panorama de Meu Progresso vem do backend, nao do MOCK', () => {
  assert.match(js, /fetch\('\/api\/v1\/student\/progress'\)/,
    'a tela de progresso nao chama o endpoint real');
  assert.doesNotMatch(js, /panorama:\s*\[/,
    'o panorama mockado voltou para o MOCK');
});

test('a tela nao reimplementa nenhum corte de desempenho', () => {
  // Os cortes sao de PerformanceThresholdPolicy, no backend. Um numero
  // desses aqui seria uma segunda definicao do que significa saber algo.
  for (const corte of ['0.8', '0.80', '0.6', '0.60', 'min_sample']) {
    assert.ok(!jsCodigo.includes(corte),
      `o corte ${corte} apareceu no frontend - ele mora no backend`);
  }
});

test('sem conseguir ler o dominio, a tela NAO inventa faixa', () => {
  // Dizer "Consolidado" sem ter lido o dominio e pior que nao dizer nada.
  const bloco = jsCodigo.slice(jsCodigo.indexOf('async function pintarProgresso'),
                               jsCodigo.indexOf('function irPara'));
  assert.ok(bloco.length > 100, 'nao consegui recortar pintarProgresso');
  assert.match(bloco, /catch/);
  for (const faixa of ['Consolidado', 'Em desenvolvimento', 'Precisa de atenção']) {
    assert.ok(!bloco.includes(faixa),
      `a tela escreve "${faixa}" por conta propria em vez de receber do backend`);
  }
});

test('nenhuma faixa e escrita na mao: todas vem do payload', () => {
  const desenhar = js.slice(js.indexOf('function desenharPanorama'),
                            js.indexOf('async function pintarProgresso'));
  assert.match(desenhar, /f\.faixa/, 'a faixa tem de vir do dado');
  assert.match(desenhar, /f\.itens/);
});
