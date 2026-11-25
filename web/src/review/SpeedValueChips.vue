<script setup lang="ts">
import {computed} from 'vue';
import {QUICK_VALUES, US_VALUES, familyHasValue, signStyleOf} from './catalog';
import {releaseFocus} from './dom';
import {setValue} from './edits';
import {countryOf, workOf} from './frames';
import {report} from './store';
import type {Detection, FrameRow} from './types';

const props = defineProps<{row: FrameRow | null; detection: Detection | null; fallbackMph?: boolean}>();
const noValue = computed(() => props.detection !== null && familyHasValue(props.detection.family)
  && props.detection.value_kph == null);
// In the US the signs are the white SPEED LIMIT rectangles, in mph and every 5; elsewhere the red discs of km/h.
const usSigns = computed(() => props.row ? signStyleOf(countryOf(props.row, workOf(props.row))) === 'us' : !!props.fallbackMph);
const quickValues = computed(() => usSigns.value ? US_VALUES : QUICK_VALUES);
const disabled = computed(() => props.detection === null || report.readOnly);
</script>

<template>
  <div :class="['signs', {led: detection?.family === 'variable_display', us: usSigns}]" id="quickValues"
       role="group" :aria-label="usSigns ? 'Speed signs in mph' : 'Speed signs'">
    <template v-if="usSigns">
      <button v-for="value in quickValues" :key="value" type="button" class="sign us" :disabled="disabled"
              :aria-pressed="detection?.value_kph === value" :data-value="value" :aria-label="value + ' mph written on sign'"
              @click="setValue(value); releaseFocus($event)"><span class="us-top" aria-hidden="true">SPEED<br>LIMIT</span><span class="us-num">{{ value }}</span></button>
    </template>
    <template v-else>
      <button v-for="value in quickValues" :key="value" type="button" :class="value >= 100 ? 'sign wide' : 'sign'" :disabled="disabled"
              :aria-pressed="detection?.value_kph === value" :data-value="value" :aria-label="value + ' written on sign'"
              @click="setValue(value); releaseFocus($event)">{{ value }}</button>
    </template>
    <button type="button" :class="usSigns ? 'sign us unknown' : 'sign unknown'" :disabled="disabled" :aria-pressed="noValue" data-value="unknown"
            title="Number not readable (?)" aria-label="Number not readable" aria-keyshortcuts="?"
            @click="setValue(null); releaseFocus($event)"><template
            v-if="usSigns"><span class="us-top" aria-hidden="true">SPEED<br>LIMIT</span><span class="us-num">?</span></template><template
            v-else>?</template></button>
  </div>
</template>
