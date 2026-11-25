<script setup lang="ts">
import {computed, ref} from 'vue';
import {PLATE_DISTANCE_MAX_M, milesToMetres, plateDistanceValue, takesDistance} from './catalog';
import {releaseFocus} from './dom';
import {setPlateDistance} from './edits';
import {vFill} from './fill';
import {report} from './store';
import type {Detection} from './types';

const props = defineProps<{detection: Detection; miles: boolean}>();
const invalid = ref(false);
const distance = computed(() => {
  const m = props.detection.plate_distance_m;
  return m == null ? '' : plateDistanceValue(m, props.miles);
});

const fractions: Record<string, string> = {'¼': '1/4', '½': '1/2', '¾': '3/4', '⅛': '1/8', '⅜': '3/8', '⅝': '5/8', '⅞': '7/8', '⅓': '1/3', '⅔': '2/3'};
function readMiles(text: string): number {
  const fraction = text.replace(/[¼½¾⅛⅜⅝⅞⅓⅔]/g, item => ' ' + fractions[item]).trim()
    .match(/^(?:(\d+)\s+)?(\d+)\s*\/\s*(\d+)$/);
  if (fraction) return Number(fraction[1] ?? 0) + Number(fraction[2]) / Number(fraction[3]);
  return /^(?:\d+(?:[.,]\d*)?|[.,]\d+)$/.test(text) ? Number(text.replace(',', '.')) : NaN;
}

function change(event: Event): void {
  const input = event.target as HTMLInputElement;
  const text = input.value.trim();
  const value = text === '' ? null : props.miles ? milesToMetres(readMiles(text)) : input.valueAsNumber;
  invalid.value = input.validity.badInput || (value !== null
    && (!Number.isInteger(value) || value < 1 || value > PLATE_DISTANCE_MAX_M));
  if (invalid.value) return;
  if ((props.detection.plate_distance_m ?? null) !== value) setPlateDistance(value);
  releaseFocus(event);
}
</script>

<template>
  <div v-if="takesDistance(detection)" id="plateDistance" class="value-line">
    <label for="plateMetres">Distance ({{ miles ? 'mi' : 'm' }})</label>
    <input v-fill="distance" id="plateMetres" :type="miles ? 'text' : 'number'"
           :min="miles ? undefined : 1" :max="miles ? undefined : PLATE_DISTANCE_MAX_M" :step="miles ? undefined : 1"
           :inputmode="miles ? 'decimal' : 'numeric'" :placeholder="miles ? 'e.g. ½' : '–'" :disabled="report.readOnly"
           :aria-invalid="invalid" :aria-describedby="invalid ? 'metresError' : undefined"
           @input="invalid = false"
           @change="change" @keydown.enter.stop.prevent="releaseFocus">
    <span v-if="invalid" class="field-error" id="metresError" role="alert">{{
      miles ? 'Enter a positive distance up to 3.107 mi (e.g. 0.5 or 1/2).' : `Enter whole metres from 1 to ${PLATE_DISTANCE_MAX_M}.`
    }}</span>
  </div>
</template>
