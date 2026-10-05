import type { IngredientRule } from './backendApi';

export function rulePresentation(rule: IngredientRule) {
  const needsReview = Boolean(rule.conditions.primary_basis_required ||
    rule.conditions.production_ready === false ||
    rule.conditions.verification_status !== 'verified_primary');

  switch (rule.conditions.regulatory_status) {
    case 'PERMITTED_WITH_CONDITIONS':
      return { title: 'Применение с условиями',
        summary: 'Разрешение на применение зависит от вида продукта и количества добавки.', needsReview };
    case 'BANNED':
      return { title: needsReview ? 'Сведения о запрете' : 'Запрет применения',
        summary: needsReview ? 'Информация о запрете требует подтверждения.' :
          'В указанном документе применение добавки запрещено.', needsReview };
    case 'PHASE_OUT':
      return { title: 'Изменение условий применения',
        summary: 'Важны дата выпуска и переходные условия. Ранее выпущенные продукты могут оставаться в продаже.', needsReview };
    default:
      return { title: rule.title, summary: rule.assessment_note || rule.explanation, needsReview };
  }
}

export function documentLabel(value: string | null | undefined): string {
  return (value || '').replace(/\bTR_TS_(\d{3})_(\d{4})\b/g, 'ТР ТС $1/$2');
}

export function sourceUrl(value: string | null | undefined): string | undefined {
  try {
    const url = new URL(value || '');
    return ['https:', 'http:'].includes(url.protocol) ? url.href : undefined;
  } catch { return undefined; }
}
