<script setup lang="ts">
import {computed, ref} from 'vue';
import {PANELS, PLATE_HOURS, holdsPlate, signStyleOf, takesDistance} from './catalog';
import {releaseFocus} from './dom';
import {setField} from './edits';
import {countryOf, workOf} from './frames';
import PlateDistance from './PlateDistance.vue';
import {report} from './store';
import type {Detection, FrameRow} from './types';

const props = defineProps<{row: FrameRow; detection: Detection; compact?: boolean}>();
const panel = computed(() => props.detection.supplementary_panel ?? 'unknown');
const miles = computed(() => signStyleOf(countryOf(props.row, workOf(props.row))) === 'us');
const panels = computed(() => {
  const choices = props.compact ? [...PANELS, {id: 'unresolved', label: 'Unresolved'}] : PANELS;
  return choices.filter(item => item.id !== 'distance' || takesDistance(props.detection));
});
const plateNote = computed(() => !holdsPlate(props.detection) ? ''
  : panel.value === 'distance' ? 'The box takes the sign and the plate.' : 'The box takes the disc and the plate.');

// Until both hours are chosen, keep the partial entry for this box only.
const halfHours = ref<[string, string]>(['', '']);
const hours = computed<[string, string]>(() => {
  const [from = '', to = ''] = props.detection.plate_hours?.split('-') ?? [];
  return props.detection.plate_hours ? [from, to] : halfHours.value;
});
function choosePanel(id: string, event: Event): void {
  halfHours.value = ['', ''];
  setField('supplementary_panel', panel.value === id ? 'unknown' : id);
  releaseFocus(event);
}
function hoursChanged(side: 0 | 1, event: Event): void {
  const chosen: [string, string] = [...hours.value];
  chosen[side] = (event.target as HTMLSelectElement).value;
  halfHours.value = chosen;
  const [from, to] = chosen;
  const value = from !== '' && to !== '' && from !== to ? from + '-' + to : null;
  if ((props.detection.plate_hours ?? null) !== value) setField('plate_hours', value);
  releaseFocus(event);
}
</script>

<template>
  <div class="plate-controls" :title="compact && plateNote ? plateNote : undefined">
    <p class="field-label" id="plateLabel">Panel below</p>
    <div class="segmented" id="panels" role="group" aria-labelledby="plateLabel">
      <button v-for="item in panels" :key="item.id" type="button" :aria-pressed="panel === item.id" :data-panel="item.id"
              :disabled="report.readOnly" @click="choosePanel(item.id, $event)">{{ item.label }}</button>
    </div>
    <p v-if="!compact && plateNote" class="effect-note" id="plateNote">{{ plateNote }}</p>
    <PlateDistance v-if="!compact || panel === 'distance'" :key="String(miles)" :detection="detection" :miles="miles"/>
    <div v-if="panel === 'time' && detection.family === 'maximum_speed'" id="plateHours" class="value-line plate-hours">
      <label for="hoursFrom">From</label>
      <select id="hoursFrom" aria-label="Plate valid from" :value="hours[0]" :disabled="report.readOnly" @change="hoursChanged(0, $event)">
        <option value="">–</option>
        <option v-for="hour in PLATE_HOURS" :key="hour" :value="String(hour)">{{ hour }}</option>
      </select>
      <label for="hoursTo">to</label>
      <select id="hoursTo" aria-label="Plate valid to" :value="hours[1]" :disabled="report.readOnly" @change="hoursChanged(1, $event)">
        <option value="">–</option>
        <option v-for="hour in PLATE_HOURS" :key="hour" :value="String(hour)">{{ hour === 0 ? '0 / 24' : hour }}</option>
      </select>
    </div>
  </div>
</template>
