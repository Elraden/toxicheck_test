<script setup lang="ts">
import { computed } from 'vue';
import { ExternalLink } from 'lucide-vue-next';
import type { IngredientRule } from '@/services/backendApi';
import { documentLabel, rulePresentation, sourceUrl } from '@/services/ingredientPresentation';

const props = defineProps<{ rules: IngredientRule[] }>();
const items = computed(() => props.rules.map(rule => ({ rule, ...rulePresentation(rule) })));
</script>

<template>
  <div class="ingredient-rules">
    <article v-for="item in items" :key="item.rule.id" class="rule">
      <h4>{{ item.title }}</h4>
      <p v-if="item.summary">{{ item.summary }}</p>
      <p v-if="item.needsReview" class="verification-note">Требует проверки по первоисточнику.</p>
      <details>
        <summary>Основание и источники</summary>
        <p v-if="item.rule.assessment_note || item.rule.explanation" class="rule-context">{{ item.rule.assessment_note || item.rule.explanation }}</p>
        <p v-if="item.rule.explanation && item.rule.assessment_note && item.rule.explanation !== item.rule.assessment_note" class="rule-context">{{ item.rule.explanation }}</p>
        <ul v-if="item.rule.conditions.evidence?.length" class="sources">
          <li v-for="(evidence, index) in item.rule.conditions.evidence" :key="index" class="source">
            <h5>{{ documentLabel(evidence.source_title || item.rule.source_title) || 'Источник' }}</h5>
            <p v-if="evidence.locator" class="source-location">{{ documentLabel(evidence.locator) }}</p>
            <a v-if="sourceUrl(evidence.url)" :href="sourceUrl(evidence.url)" target="_blank" rel="noopener noreferrer">Открыть документ<ExternalLink aria-hidden="true" /></a>
            <small v-if="evidence.verification_status !== 'verified_primary'">Подтверждение по этому источнику не завершено.</small>
          </li>
        </ul>
        <div v-else class="source">
          <h5 v-if="item.rule.source_title">{{ documentLabel(item.rule.source_title) }}</h5>
          <p v-if="item.rule.citation" class="source-location">{{ documentLabel(item.rule.citation) }}</p>
          <a v-if="sourceUrl(item.rule.source_url)" :href="sourceUrl(item.rule.source_url)" target="_blank" rel="noopener noreferrer">Открыть документ<ExternalLink aria-hidden="true" /></a>
        </div>
      </details>
    </article>
  </div>
</template>

<style scoped>
.ingredient-rules { overflow-wrap: anywhere; }
.rule { padding: 16px 0; border-bottom: 1px solid #dfe6e3; }
.rule h4 { font-size: 15px; line-height: 1.5; }
.rule p { margin-top: 8px; font-size: 14px; line-height: 1.65; color: #52605a; }
.rule .verification-note { color: #875607; font-size: 12px; }
.rule summary { width: fit-content; padding: 12px 0; color: #24684f; cursor: pointer; font-size: 13px; line-height: 1.5; }
.rule .rule-context { margin: 0 0 12px; font-size: 13px; white-space: pre-line; }
.sources { list-style: none; margin: 0; padding: 0; }
.source { margin-top: 12px; padding-left: 12px; border-left: 2px solid #cbded5; }
.source h5 { font-size: 13px; line-height: 1.6; }
.source .source-location { font-size: 12px; margin: 6px 0; }
.source a { display: inline-flex; align-items: center; gap: 6px; min-height: 36px; color: #236c52; font-size: 13px; line-height: 1.5; }
.source a svg { width: 14px; height: 14px; flex-shrink: 0; }
.source small { display: block; color: #66736e; font-size: 11px; line-height: 1.6; }
:is(a, summary):focus-visible { outline: 2px solid #25a777; outline-offset: 3px; }
</style>
