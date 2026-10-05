import assert from 'node:assert/strict';
import { test } from 'node:test';
import { documentLabel, rulePresentation, sourceUrl } from '../src/services/ingredientPresentation.ts';

function rule(status, overrides = {}) {
  return {
    title: 'Original title', explanation: 'Original explanation', assessment_note: null,
    conditions: { regulatory_status: status, verification_status: 'verified_primary', ...overrides }
  };
}

test('permission has a readable title without an unconditional safety claim', () => {
  const result = rulePresentation(rule('PERMITTED_WITH_CONDITIONS'));
  assert.equal(result.title, 'Применение с условиями');
  assert.match(result.summary, /зависит/);
  assert.equal(result.needsReview, false);
});

test('unverified prohibition stays qualified', () => {
  for (const conditions of [
    { primary_basis_required: true }, { production_ready: false },
    { verification_status: 'verified_official_publication' }, { verification_status: undefined }
  ]) {
    const result = rulePresentation(rule('BANNED', conditions));
    assert.equal(result.title, 'Сведения о запрете');
    assert.match(result.summary, /требует подтверждения/);
    assert.equal(result.needsReview, true);
  }
  assert.equal(rulePresentation(rule('BANNED')).title, 'Запрет применения');
});

test('phase-out retains production-date and prior-circulation caveats', () => {
  const result = rulePresentation(rule('PHASE_OUT'));
  assert.match(result.summary, /дата выпуска/);
  assert.match(result.summary, /Ранее выпущенные продукты/);
});

test('unknown rule types keep their original explanation and title', () => {
  const input = rule('FUTURE_STATUS');
  input.assessment_note = 'Assessed context';
  const result = rulePresentation(input);
  assert.equal(result.title, input.title);
  assert.equal(result.summary, input.assessment_note);
});

test('document label keeps exact clause references', () => {
  assert.equal(documentLabel('TR_TS_029_2012, приложение 2, позиция E202'),
    'ТР ТС 029/2012, приложение 2, позиция E202');
  assert.equal(documentLabel('Решение № 84, пункт 7(а)'), 'Решение № 84, пункт 7(а)');
  assert.equal(documentLabel(null), '');
});

test('source links allow web URLs only and preserve PDF page anchors', () => {
  assert.equal(sourceUrl('https://example.org/source.pdf#page=40'), 'https://example.org/source.pdf#page=40');
  for (const value of ['javascript:alert(1)', 'data:text/html,bad', 'file:///private', '', null]) {
    assert.equal(sourceUrl(value), undefined);
  }
});
