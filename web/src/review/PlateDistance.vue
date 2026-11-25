<script setup lang="ts">
import {ref, watch} from 'vue';
import {PLATE_DISTANCE_MAX_M, takesDistance} from './catalog';
import {releaseFocus} from './dom';
import {setPlateDistance} from './edits';
import {vFill} from './fill';
import {report} from './store';
import type {Detection} from './types';

const props = defineProps<{detection: Detection}>();
const invalid = ref(false);
watch(() => props.detection, () => { invalid.value = false; });

function change(event: Event): void {
  const input = event.target as HTMLInputElement;
  const value = input.value === '' ? null : input.valueAsNumber;
  invalid.value = input.validity.badInput || (value !== null
    && (!Number.isInteger(value) || value < 1 || value > PLATE_DISTANCE_MAX_M));
  if (invalid.value) return;
  if ((props.detection.plate_distance_m ?? null) !== value) setPlateDistance(value);
  releaseFocus(event);
}
</script>

<template>
  <div v-if="takesDistance(detection)" id="plateDistance" class="value-line">
    <label for="plateMetres">Distance (m)</label>
    <input v-fill="detection.plate_distance_m == null ? '' : String(detection.plate_distance_m)"
           id="plateMetres" type="number" min="1" :max="PLATE_DISTANCE_MAX_M" step="1" inputmode="numeric"
           placeholder="–" :disabled="report.readOnly" :aria-invalid="invalid" :aria-describedby="invalid ? 'metresError' : undefined"
           @change="change" @keydown.enter.stop.prevent="releaseFocus">
    <span v-if="invalid" class="field-error" id="metresError" role="alert">Enter whole metres from 1 to {{ PLATE_DISTANCE_MAX_M }}.</span>
  </div>
</template>
