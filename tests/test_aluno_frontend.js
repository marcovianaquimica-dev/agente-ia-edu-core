/**
 * PROTOTIPO DO PERFIL ALUNO — asserções estáticas sobre aluno.html / .js / .css.
 *
 * Mesmo padrão dos demais testes de frontend do projeto (node:test + regex
 * sobre o markup), para não introduzir uma segunda forma de testar.
 *
 * Estes testes NÃO tocam index.html nem app.js: o portal atual continua
 * intacto e seus 11 arquivos de teste continuam valendo. Ver
 * docs/frontend-aluno-auditoria.md seção 6.
 *
 * Rode com:  node --test "tests/*frontend.js"
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'src', 'agente_ia_edu', 'web');
const html = fs.readFileSync(path.join(WEB, 'aluno.html'), 'utf8');
const js = fs.readFileSync(path.join(WEB, 'aluno.js'), 'utf8');
const css = fs.readFileSync(path.join(WEB, 'aluno.css'), 'utf8');

// ------------------------------------------------------------ isolamento --

test('o protótipo não altera nem importa o portal atual', () => {
  // Verifica CARREGAMENTO, não menção: `app.js` aparece nos comentários de
  // aluno.js justamente para explicar que ele NÃO é usado. Procurar a string
  // no arquivo encontraria a própria explicação — erro já cometido antes
  // neste projeto, num guarda que se auto-detectou na própria docstring.
  const scripts = html.match(/<script[^>]*src="([^"]+)"/g) || [];
  assert.deepStrictEqual(scripts, ['<script src="aluno.js"'],
    'aluno.html só carrega aluno.js');
  const folhas = html.match(/<link[^>]*rel="stylesheet"[^>]*href="([^"]+)"/g) || [];
  assert.ok(folhas.every((l) => /aluno\.css|fonts\.googleapis/.test(l)),
    'aluno.html só usa o CSS próprio e a fonte');
  assert.ok(!/\b(import|require)\b/.test(js), 'aluno.js não importa nada');
  // os arquivos do portal continuam existindo
  assert.ok(fs.existsSync(path.join(WEB, 'index.html')));
  assert.ok(fs.existsSync(path.join(WEB, 'app.js')));
});

// ------------------------------------------------------------- navegação --

test('a navegação permanente tem exatamente dois destinos', () => {
  const abas = html.match(/class="aba[^"]*"\s+data-tela="[a-z]+"/g) || [];
  assert.strictEqual(abas.length, 2,
    `esperado 2 destinos permanentes, encontrado ${abas.length}`);
  assert.match(html, /data-tela="inicio"/);
  assert.match(html, /data-tela="progresso"/);
});

test('o portal atual tem 13 itens de navegação — a comparação é o ponto', () => {
  const indexHtml = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');
  const itens = indexHtml.match(/class="nav-item[^"]*"/g) || [];
  assert.ok(itens.length >= 12,
    'se o portal mudar, a comparação do relatório precisa ser refeita');
});

test('as três telas existem e só uma fica visível por vez', () => {
  ['tela-inicio', 'tela-sessao', 'tela-progresso'].forEach((id) => {
    assert.ok(html.includes(`id="${id}"`), `falta ${id}`);
  });
  assert.match(html, /id="tela-sessao"[^>]*hidden/);
  assert.match(html, /id="tela-progresso"[^>]*hidden/);
  assert.match(js, /\$\(`tela-\$\{t\}`\)\.hidden = \(t !== tela\)/);
});

// ------------------------------------------------------- estados da Home --

test('os oito estados de Home A..H existem', () => {
  'ABCDEFGH'.split('').forEach((k) => {
    assert.match(js, new RegExp(`\\n    ${k}: \\(\\)`), `falta o estado ${k}`);
  });
});

test('a precedência entre estados é explícita e determinística', () => {
  const bloco = js.slice(js.indexOf('function estadoDaHome'),
                         js.indexOf('// ==================================================== Home: renderers'));
  // retomar vem antes de tarefa; tarefa antes de prova
  assert.ok(bloco.indexOf("return 'D'") < bloco.indexOf("return 'B'"),
    'terminar o que começou deve vir antes de começar outra coisa');
  assert.ok(bloco.indexOf("return 'B'") < bloco.indexOf("return 'E'"));
});

test('tarefa pendente mostra disciplina, prazo e UMA ação principal', () => {
  const B = js.slice(js.indexOf('    B: () =>'), js.indexOf('    C: () =>'));
  assert.match(B, /selo-prazo/);
  assert.match(B, /data-acao="tarefa"/);
  const principais = B.match(/botao-principal/g) || [];
  assert.strictEqual(principais.length, 1, 'uma única ação principal por estado');
});

test('sem tarefa, a Home pergunta o tempo', () => {
  const C = js.slice(js.indexOf('    C: () =>'), js.indexOf('    D: () =>'));
  assert.match(C, /blocoDeTempo\('Quanto tempo você tem hoje\?'\)/);
});

test('a primeira entrada não mostra nenhum zero nem métrica', () => {
  const A = js.slice(js.indexOf('    A: () =>'), js.indexOf('    B: () =>'));
  assert.ok(!/0[.,]0%|Média Geral|Questões Respondidas/.test(A),
    'aluno sem histórico não pode receber métricas zeradas');
});

test('sessão interrompida oferece retomar', () => {
  const D = js.slice(js.indexOf('    D: () =>'), js.indexOf('    E: () =>'));
  assert.match(D, /data-acao="retomar"/);
  assert.match(D, /Continuar/);
});

test('aluno sem escola não recebe vocabulário de tarefa', () => {
  const G = js.slice(js.indexOf('    G: () =>'), js.indexOf('    H: () =>'));
  assert.ok(!/tarefa|atividade|professor|escola|prazo/i.test(G),
    'o estado G é do aluno independente');
});

// ---------------------------------------------------------------- tempo ---

test('o tempo oferece os quatro presets e a saída sem tempo', () => {
  assert.match(js, /\[15, 30, 45, 60\]\.map/);
  assert.match(js, /data-minutos="0"[\s\S]{0,40}Sem tempo definido/);
});

test('o tempo escolhido vai para o planejamento, não é decorativo', () => {
  assert.match(js, /comecarSessao\(\{[\s\S]{0,200}minutos: m,/);
  assert.match(js, /function montarSessao\(\{ objetivo, conteudos, minutos \}\)/);
});

test('o contrato real do backend está citado para a troca ser direta', () => {
  assert.match(js, /available_minutes/);
  assert.match(js, /target_content_codes/);
});

// ============================ REQUISITO 6 ATUALIZADO: prontidão ===========

test('os três caminhos de prontidão existem e espelham os estados do planejador', () => {
  assert.match(js, /const DIRETO = 'DIRECT'/);
  assert.match(js, /const DIAGNOSTICO = 'DIAGNOSED'/);
  assert.match(js, /const PREPARACAO = 'PREPARED'/);
  // os estados são os reais do backend, não inventados
  assert.match(js, /BLOCKED_BY_PREREQUISITE/);
  assert.match(js, /INSUFFICIENT_EVIDENCE/);
});

test('lacuna conhecida vira preparação ANTES da atividade', () => {
  const inicio = js.indexOf('function rotaDeProntidao');
  const f = js.slice(inicio, js.indexOf('const app = {', inicio));
  assert.ok(f.length > 100, 'a fatia da função não pode sair vazia');
  // Mede a ordem dos RAMOS, não a primeira menção ao texto: o nome
  // INSUFFICIENT_EVIDENCE aparece antes como valor padrão do .map(), e
  // comparar indexOf cru daria a resposta errada.
  const ramos = (f.match(/if \(estados\.includes\('([A-Z_]+)'\)\)/g) || [])
    .map((m) => m.match(/'([A-Z_]+)'/)[1]);
  assert.deepStrictEqual(ramos,
    ['BLOCKED_BY_PREREQUISITE', 'INSUFFICIENT_EVIDENCE'],
    'lacuna conhecida tem precedência sobre prontidão incerta');
});

test('a atividade continua sendo o OBJETIVO mesmo quando não é o primeiro passo', () => {
  assert.match(js, /tipo: 'OBJECTIVE'/);
  assert.match(html, /id="objetivo"/);
  assert.match(js, /\$\('objetivo-texto'\)\.textContent = s\.objetivo\.title/);
});

test('a preparação é explicada como ajuda, nunca como falta do aluno', () => {
  const m = js.slice(js.indexOf('function montarSessao'), js.indexOf('function pintarSessao'));
  assert.match(m, /Isso vai te ajudar a resolver a tarefa/);
  assert.ok(!/você não sabe|não domina|deficiência|fraco/i.test(m),
    'a linguagem não pode culpar o aluno');
});

test('o dado que Professor e Coordenação vão precisar já nasce na sessão', () => {
  assert.match(js, /readiness_route/);
  assert.match(js, /objective_assignment_id/);
});

test('com preparação, o aluno é avisado de que pode não terminar hoje', () => {
  assert.match(js, /parcial: Boolean\(objetivo\) && gasto > 0/);
  assert.match(js, /seu progresso fica salvo/);
});

// -------------------------------------------------------------- sessão ----

test('a sessão mostra um bloco por vez, com o próximo anunciado', () => {
  assert.match(js, /const b = s\.blocos\[s\.atual\]/);
  assert.match(js, /const proximo = s\.blocos\[s\.atual \+ 1\]/);
  assert.match(js, /a-seguir/);
});

test('o fechamento sai DO orçamento, não por cima dele', () => {
  assert.match(js, /const paraObjetivo = Math\.max\(5, minutos - gasto - FECHAMENTO\)/);
});

test('bloco sem experiência executável aparece sem ação falsa', () => {
  assert.match(js, /disponivel: false/);
  assert.match(js, /b\.disponivel[\s\S]{0,180}indisponivel/);
  // o backend marca review_action_available = False; a interface respeita
  assert.match(js, /review_action_available/);
});

test('sair da sessão preserva o progresso e volta como estado D', () => {
  assert.match(js, /btn-sair-sessao[\s\S]{0,400}MOCK\.interrompida = \{/);
  assert.match(js, /app\.homeForcada = 'D'/);
});

// ------------------------------------------------------------ progresso ---

test('o progresso são frases, não um painel de métricas', () => {
  assert.ok(!/canvas|chart|Chart|gráfico/i.test(html),
    'o MVP de progresso não tem gráfico');
  assert.match(js, /Você estudou 3 dias esta semana/);
  assert.match(html, /id="btn-proximo-passo"[\s\S]{0,80}Próximo passo/);
});

test('o panorama completo fica recolhido', () => {
  assert.match(html, /<details class="panorama">[\s\S]{0,200}<summary>ver panorama completo<\/summary>/);
});

test('média geral e questões respondidas foram removidas', () => {
  assert.ok(!/Média Geral|Questões Respondidas/.test(html + js),
    'métricas que não ajudam a agir ficaram fora do MVP');
});

// --------------------------------------------------------- honestidade ----

test('o que não tem backend nasce desabilitado e dito', () => {
  const icones = html.match(/<button class="icone"[^>]*>/g) || [];
  assert.strictEqual(icones.length, 3, 'foto, arquivo e voz');
  icones.forEach((b) => assert.match(b, /disabled/));
  assert.match(html, /ainda não está disponível/);
});

test('o texto livre não finge interpretar a dúvida', () => {
  assert.match(js, /A interpretação de texto livre ainda não existe no/);
});

test('os mocks estão identificados', () => {
  assert.match(js, /O QUE E REAL E O QUE E MOCK/);
  assert.match(js, /const MOCK = \{/);
  assert.match(js, /\/\/ MOCK/);
});

// ------------------------------------------------------ acessibilidade ----

test('marcos, um h1 por tela e link de pular', () => {
  assert.match(html, /<a class="pular"/);
  assert.match(html, /<header class="topo">/);
  assert.match(html, /<main id="conteudo"/);
  assert.match(html, /<nav class="abas" aria-label="Navegação principal">/);
  const h1 = html.match(/<h1[^>]*>/g) || [];
  assert.strictEqual(h1.length, 3, 'um h1 por tela');
});

test('o que muda sozinho é anunciado', () => {
  assert.match(html, /id="home" aria-live="polite"/);
  assert.match(html, /id="bloco" aria-live="polite"/);
});

test('a aba ativa é anunciada e o foco é visível', () => {
  assert.match(js, /setAttribute\('aria-current', 'page'\)/);
  assert.match(css, /:focus-visible \{[\s\S]{0,120}outline: 3px solid/);
});

test('alvos de toque de 44px e movimento reduzido respeitado', () => {
  assert.match(css, /--toque: 44px/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
});

test('os controles desabilitados dizem o motivo', () => {
  assert.match(html, /aria-label="Enviar foto \(em breve\)"/);
  assert.match(html, /aria-label="Falar \(em breve\)"/);
});

// -------------------------------------------------------- mobile first ----

test('o CSS é mobile first: o base é telefone e min-width acrescenta', () => {
  const mins = css.match(/@media \(min-width:/g) || [];
  const maxs = css.match(/@media \(max-width:/g) || [];
  assert.ok(mins.length >= 2, 'precisa de breakpoints min-width');
  assert.strictEqual(maxs.length, 0,
    'nenhum max-width: o CSS do portal atual é desktop-first, este não é');
});

test('a barra de abas é alcançável pelo polegar no telefone', () => {
  assert.match(css, /\.abas \{[\s\S]{0,200}position: fixed;[\s\S]{0,120}bottom: 0/);
  assert.match(css, /@media \(min-width: 768px\)[\s\S]{0,900}\.abas \{[\s\S]{0,80}position: static/);
});

test('no desktop a coluna não vira dashboard', () => {
  assert.match(css, /largura maxima de LEITURA[\s\S]{0,200}max-width: 760px/);
});

test('área segura de telefone considerada', () => {
  assert.match(css, /env\(safe-area-inset-bottom/);
});

// ------------------------------------------------------------ contraste ---

/** Luminancia relativa (WCAG 2.1). */
function luminancia(hex) {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}

function contraste(a, b) {
  const [x, y] = [luminancia(a), luminancia(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

function token(nome) {
  const m = css.match(new RegExp(`--${nome}:\\s*(#[0-9a-fA-F]{6})`));
  assert.ok(m, `token --${nome} nao encontrado`);
  return m[1];
}

test('todo par de cor usado em texto passa em AA (4.5:1)', () => {
  const pares = [
    ['texto principal no cartao', token('tinta'), token('papel')],
    ['texto fraco no cartao', token('tinta-fraca'), token('papel')],
    ['texto fraco no fundo', token('tinta-fraca'), token('fundo')],
    ['branco no botao primario', '#ffffff', token('primaria')],
    ['link sobre o fundo', token('primaria'), token('fundo')],
    ['selo de prazo', '#92400e', token('alerta-claro')],
    ['selo bom', '#065f46', token('sucesso-claro')],
    ['chip de tempo', token('primaria-escura'), token('primaria-clara')],
  ];
  const reprovados = pares
    .map(([nome, fg, bg]) => [nome, Number(contraste(fg, bg).toFixed(2))])
    .filter(([, r]) => r < 4.5);
  assert.deepStrictEqual(reprovados, [],
    `pares abaixo de AA: ${JSON.stringify(reprovados)}`);
});

test('o cinza do portal atual reprovaria — por isso este e mais escuro', () => {
  // #64748b e o --text-muted de styles.css: 4,40 sobre o fundo da aplicacao.
  // Medido, nao suposto. Se alguem "alinhar" os dois, este teste avisa.
  assert.ok(contraste('#64748b', token('fundo')) < 4.5);
  assert.ok(contraste(token('tinta-fraca'), token('fundo')) >= 4.5);
});

// -------------------------------------------- orcamento de tempo (real) ---

/**
 * Os testes acima sao estaticos (regex sobre o fonte), como os outros 11
 * arquivos de frontend do projeto. Para a regra de orcamento isso nao basta:
 * o bug que motivou esta secao — pedir 15 min e receber 22 — passava por
 * qualquer regex, porque cada linha isolada estava certa. Entao aqui a
 * funcao e EXECUTADA de verdade, com suas dependencias injetadas.
 */
function carregarMontarSessao(mock) {
  const inicio = js.indexOf('  function montarSessao(');
  const fim = js.indexOf('\n  function pintarSessao(', inicio);
  assert.ok(inicio > 0 && fim > inicio, 'nao consegui recortar montarSessao');
  const fonte = js.slice(inicio, fim);
  // eslint-disable-next-line no-new-func
  return new Function('MOCK', 'rotaDeProntidao', 'DIAGNOSTICO', 'PREPARACAO',
    `${fonte}\n return montarSessao;`)(mock, () => mock.__rota, 'DIAGNOSED', 'PREPARED');
}

const MOCK_FALSO = {
  prerequisitos: { 'quimica.estequiometria': [{ nome: 'Mol e massa molar' }] },
  __rota: 'PREPARED',
};

test('a sessao NUNCA passa do tempo que o aluno pediu', () => {
  const estouros = [];
  for (const rota of ['DIRECT', 'DIAGNOSED', 'PREPARED']) {
    for (const minutos of [5, 10, 15, 20, 25, 30, 45, 60, 90]) {
      const montar = carregarMontarSessao({ ...MOCK_FALSO, __rota: rota });
      const s = montar({
        objetivo: { title: 'Atividade', assignment_id: 'a1' },
        conteudos: ['quimica.estequiometria'],
        minutos,
      });
      const total = s.blocos.reduce((a, b) => a + b.minutos, 0);
      if (total > minutos) estouros.push({ rota, minutos, total });
    }
  }
  assert.deepStrictEqual(estouros, [],
    `a sessao estourou o orcamento: ${JSON.stringify(estouros)}`);
});

test('quando o tempo e curto demais, a atividade sai e o aluno e avisado', () => {
  // 15 min com lacuna de pre-requisito: os pisos (8+5+5+4) somam 22. O certo
  // e fazer so a preparacao hoje, nao espremer tudo nem estourar o tempo.
  const montar = carregarMontarSessao({ ...MOCK_FALSO, __rota: 'PREPARED' });
  const s = montar({
    objetivo: { title: 'Atividade de Estequiometria', assignment_id: 'a1' },
    conteudos: ['quimica.estequiometria'],
    minutos: 15,
  });
  assert.ok(s.blocos.every((b) => b.tipo !== 'OBJECTIVE'), 'a atividade deveria ter saido');
  assert.strictEqual(s.objetivoFicouDeFora, true);
  // o objetivo continua registrado: a sessao seguinte retoma de onde parou
  assert.strictEqual(s.objective_assignment_id, 'a1');
  assert.strictEqual(s.completed_objective, false);
});

test('com tempo suficiente, nada e cortado e a atividade acontece', () => {
  for (const minutos of [30, 45, 60]) {
    const montar = carregarMontarSessao({ ...MOCK_FALSO, __rota: 'PREPARED' });
    const s = montar({
      objetivo: { title: 'Atividade', assignment_id: 'a1' },
      conteudos: ['quimica.estequiometria'],
      minutos,
    });
    assert.strictEqual(s.objetivoFicouDeFora, false, `${minutos} min cortou a atividade`);
    assert.ok(s.blocos.some((b) => b.tipo === 'OBJECTIVE'));
    assert.ok(s.blocos.some((b) => b.tipo === 'REVIEW'), 'o fechamento sumiu');
  }
});

test('o aviso de corte chega ao resumo da sessao, nao fica so no estado', () => {
  assert.match(js, /objetivoFicouDeFora[\s\S]{0,120}a atividade fica para a próxima/);
});
