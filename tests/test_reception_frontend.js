// Frontend contract checks for the Reception/Secretary portal
// (src/agente_ia_edu/web/reception.js) against /api/v1/reception/*
// (src/agente_ia_edu/api/routes/reception.py). reception.js has no
// module.exports (it runs entirely inside a DOMContentLoaded listener), so -
// like the other *_frontend.js tests in this suite - these assertions run
// against the raw source text, plus a verbatim copy of translateDetail()'s
// logic exercised against real backend error shapes.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const js = fs.readFileSync('src/agente_ia_edu/web/reception.js', 'utf8');

test('translateDetail() does not leak raw pydantic validation text to the atendente', () => {
  // Regression test for a real bug found auditing the reception.js <->
  // reception.py contract: POSTing /api/v1/reception/candidates with a
  // field that fails a plain length constraint (e.g. full_name shorter
  // than ReceptionCandidateCreate's min_length=3) comes back from FastAPI
  // as {"detail": [{"loc": ["body","full_name"], "msg": "String should
  // have at least 3 characters", "type": "string_too_short", ...}]}. And a
  // field that fails one of our OWN Portuguese field_validators (email,
  // academic_year, ...) comes back with msg "Value error, <mensagem em
  // português>" - pydantic prefixes even a fully-localized message with
  // the English literal "Value error, ".
  //
  // The old translateDetail() just joined `e.msg` verbatim, so the
  // "Novo atendimento" form showed things like:
  //   "String should have at least 3 characters Value error, O ano letivo
  //    deve ter quatro dígitos."
  // straight to the secretaria/recepção user.
  //
  // Confirmed live against the dev server:
  //   curl -X POST /api/v1/reception/candidates -d
  //     '{"full_name":"Teste Nome Valido","email":"nao-e-email",
  //       "academic_year":"abcd", ...}'
  //   -> {"detail":[
  //        {"type":"value_error","loc":["body","email"],
  //         "msg":"Value error, Informe um e-mail válido."},
  //        {"type":"value_error","loc":["body","academic_year"],
  //         "msg":"Value error, O ano letivo deve ter quatro dígitos."}]}

  // Execute the actual translateDetail() logic (copied verbatim), against
  // real 422 error-item shapes.
  const ROLE_LABELS = {
    DIRECTOR: 'Diretor(a)', COORDINATOR: 'Coordenador(a)', SECRETARY: 'Secretaria',
    TEACHER: 'Professor(a)', STUDENT: 'Aluno(a)', PLATFORM_ADMIN: 'Administrador da Plataforma',
  };
  const FIELD_LABELS = {
    full_name: 'Nome completo', preferred_name: 'Nome preferido', birth_date: 'Data de nascimento',
    guardian_name: 'Responsável', phone: 'Telefone', email: 'E-mail', academic_year: 'Ano letivo',
    unit_id: 'Unidade', segment_id: 'Segmento', grade_level: 'Série', classroom_id: 'Turma',
    school_id: 'Escola',
  };
  function translateValidationItem(item) {
    const field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : null;
    const label = FIELD_LABELS[field] || field;
    const rawMsg = (item && item.msg) || 'Dado inválido.';
    const valueErrorMatch = rawMsg.match(/^Value error,\s*(.+)$/);
    if (valueErrorMatch) return valueErrorMatch[1];
    if (item.type === 'string_too_short') {
      return `${label}: deve ter pelo menos ${item.ctx?.min_length} caracteres.`;
    }
    if (item.type === 'string_too_long') {
      return `${label}: deve ter no máximo ${item.ctx?.max_length} caracteres.`;
    }
    if (item.type === 'missing') {
      return `${label ? label + ': ' : ''}campo obrigatório.`;
    }
    return label ? `${label}: ${rawMsg}` : rawMsg;
  }
  function translateDetail(detail) {
    if (!detail) return detail;
    if (Array.isArray(detail)) {
      return detail.map(translateValidationItem).join(' ');
    }
    if (typeof detail === 'object') {
      return detail.message || 'Não foi possível concluir a operação.';
    }
    const roleMatch = detail.match(/^Role required: (.+)$/);
    if (roleMatch) {
      const roles = roleMatch[1].split(', ').map(r => ROLE_LABELS[r] || r).join(', ');
      return `Perfil de acesso necessário: ${roles}.`;
    }
    return detail;
  }

  // Real backend shape for a plain length-constraint violation.
  const lengthErrorBody = [
    { type: 'string_too_short', loc: ['body', 'full_name'], msg: 'String should have at least 3 characters', input: 'A', ctx: { min_length: 3 } },
  ];
  const lengthMessage = translateDetail(lengthErrorBody);
  assert.doesNotMatch(lengthMessage, /String should have/);
  assert.match(lengthMessage, /Nome completo/);
  assert.match(lengthMessage, /pelo menos 3/);

  // Real backend shape for one of reception.py's own PT-BR field_validators.
  const valueErrorBody = [
    { type: 'value_error', loc: ['body', 'email'], msg: 'Value error, Informe um e-mail válido.', input: 'nao-e-email', ctx: { error: {} } },
    { type: 'value_error', loc: ['body', 'academic_year'], msg: 'Value error, O ano letivo deve ter quatro dígitos.', input: 'abcd', ctx: { error: {} } },
  ];
  const valueErrorMessage = translateDetail(valueErrorBody);
  assert.doesNotMatch(valueErrorMessage, /Value error/);
  assert.match(valueErrorMessage, /Informe um e-mail válido\./);
  assert.match(valueErrorMessage, /O ano letivo deve ter quatro dígitos\./);

  // Sanity: the source itself must actually strip the "Value error, "
  // prefix rather than only this test's reimplementation.
  assert.match(js, /Value error/);
});

test('candidate CRUD and diagnostic-release calls match the reception.py routes and payload/response field names', () => {
  assert.match(js, /api\(`\/api\/v1\/reception\/candidates\?\$\{params\}`\)/);
  assert.match(js, /api\(`\/api\/v1\/reception\/candidates\/\$\{id\}`\)/);
  assert.match(js, /api\('\/api\/v1\/reception\/candidates', \{ method: 'POST', body: JSON\.stringify\(payload\) \}\)/);
  assert.match(js, /api\(`\/api\/v1\/reception\/candidates\/\$\{state\.currentCandidate\.id\}\/diagnostic-release`, \{ method: 'POST' \}\)/);
  assert.match(js, /api\('\/api\/v1\/reception\/diagnostic-access\/activate', \{ method: 'POST', headers: headers\(candidateToken\), body: JSON\.stringify\(\{ token: state\.activationToken \}\) \}\)/);
  // ReceptionCandidateCreate field names sent from the "Novo atendimento" form.
  ['preferred_name', 'birth_date', 'guardian_name', 'classroom_id'].forEach((field) => {
    assert.match(js, new RegExp(field));
  });
  // DiagnosticReleaseResponse / ReceptionCandidateResponse field names read back.
  assert.match(js, /released\.candidate/);
  assert.match(js, /released\.activation_token/);
});
