<script setup lang="ts">
// The editor of the selected box: where it comes from and Fit box (its zoom is inside the photo, ZoomInset.vue), the
// labels used last, road, speed signs, other value, type and kinds of other signs, details.
import {computed} from 'vue';
import {BOOLEAN_FIELDS, FAMILIES, ROADS, SELECT_FIELDS,
  SIGN_KIND_GROUPS, familyHasValue, isPainted, type BooleanField,
  type SelectField} from './catalog';
import {aiBlocker, canFitLocally, fitBox, fitLocal, fitting, localFitting} from './ai';
import {addedBy, originalOf} from './boxes';
import {releaseFocus} from './dom';
import {setFamily, setField, setKind, setOnVehicle, setOtherOurs, setValue, squareSelected, togglePainted, toggleVariable} from './edits';
import {workOf} from './frames';
import {vFill} from './fill';
import LightChips from './LightChips.vue';
import PlateControls from './PlateControls.vue';
import SpeedValueChips from './SpeedValueChips.vue';
import {report, state} from './store';
import type {Detection, FrameRow} from './types';

const props = defineProps<{row: FrameRow; detection: Detection | null}>();


const usual = FAMILIES.filter((family) => !family.minor);
const minor = FAMILIES.filter((family) => family.minor);
const hasValueField = computed(() => props.detection !== null && familyHasValue(props.detection.family));
const road = computed(() => props.detection?.applicability || 'unresolved');
const variable = computed(() => props.detection?.family === 'variable_display');
const detailFields = computed(() => report.embedded ? SELECT_FIELDS.filter(key => key !== 'supplementary_panel') : SELECT_FIELDS);
// O X U and G P R E M on a keyboard (use-keyboard.ts).
const ROAD_KEYS: Record<string, string> = {own_road: 'O', other_road: 'X', unresolved: 'U'};
/** The traffic lights, the kind given most, are LightChips.vue; the other kinds under Type. */
const KIND_GROUPS = SIGN_KIND_GROUPS.filter((group) => group.label !== 'Traffic light');
const KIND_KEYS: Record<string, string> = {give_way: 'G', stop: 'P', roundabout: 'R', other: 'E', main_road: 'M'};
const canFit = computed(() => props.detection !== null && aiBlocker() === '');
const fittingThis = computed(() => fitting.key === props.row.frame_key && fitting.index === state.selected);
const canFitHere = computed(() => props.detection !== null && canFitLocally());
const fittingHere = computed(() => localFitting.key === props.row.frame_key && localFitting.index === state.selected);
// Where the box comes from: the model's proposal, else who added it (the reviewer by hand, or an AI reviewer).
const evidence = computed(() => {
  const d = props.detection;
  if (d === null) return '';
  const original = originalOf(props.row, d);
  const label = d.model_label || original?.model_label;
  if (label) return 'Model proposal: ' + label;
  if (original !== null) return 'Model proposal.';
  return 'Box ' + addedBy(workOf(props.row), d) + '.';
});

const SELECT_CHOICES: Record<SelectField, [string, string][]> = {
  visibility: [['unknown', 'To assess'], ['clear', 'Clear'], ['partial', 'Partial'], ['poor', 'Poor']],
  occlusion: [['unknown', 'To assess'], ['none', 'None'], ['partial', 'Partial'], ['heavy', 'Heavy']],
  supplementary_panel: [['unknown', 'To assess'], ['none', 'None'], ['time', 'Hours'], ['wet', 'Wet'], ['distance', 'Distance'],
    ['present', 'Other'], ['unresolved', 'Unresolved']],
};
const SELECT_LABELS: Record<SelectField, string> = {
  visibility: 'Visibility', occlusion: 'Occlusion', supplementary_panel: 'Panel below',
};
const BOOLEAN_LABELS: Record<BooleanField, string> = {temporary: 'Temporary', variable_display: 'Variable speed limit'};

function selectValue(key: SelectField): string {
  return props.detection?.[key] ?? 'unknown';
}
function booleanValue(key: BooleanField): string {
  const value = props.detection?.[key];
  return value === true ? 'true' : value === false ? 'false' : 'unknown';
}

function valueChanged(event: Event): void {
  const raw = (event.target as HTMLInputElement).value.trim();
  if (raw === '') {
    setValue(null);
    return;
  }
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 5 || value > 250) {
    state.valueError = true;
    return;
  }
  setValue(value);
}
function selectChanged(key: SelectField, event: Event): void {
  setField(key, (event.target as HTMLSelectElement).value);
}
function booleanChanged(key: BooleanField, event: Event): void {
  const value = (event.target as HTMLSelectElement).value;
  setField(key, value === 'true' ? true : value === 'false' ? false : null);
}
</script>

<template>
  <section id="editor" :hidden="detection === null">
    <div class="section-head"><h2 id="editorTitle">Box {{ state.selected + 1 }}</h2></div>
    <!-- The traffic lights first, right under the heading; on Speed and sign review on the drive's title row instead. -->
    <LightChips v-if="!report.embedded"/>
    <div class="zoom-foot">
      <p class="evidence" id="evidence">{{ evidence }}</p>
      <div class="fit-chips">
        <!-- Square on its centre (Q); Shift while drawing draws a square, while dragging a corner keeps the box's proportions. -->
        <button type="button" class="chip fit" id="squareBox" :disabled="report.readOnly"
                title="Make this box a square on its centre. Shift ↑ and Shift ↓ make the box larger and smaller with its proportions; Shift while dragging a corner keeps them too"
                @click="squareSelected(); releaseFocus($event)">Square<kbd>Q</kbd></button>
        <!-- This rough box made tight on the server, in a fraction of a second; C fits every box of the frame. -->
        <button v-if="canFitHere" type="button" class="chip fit" id="fitLocal" :disabled="localFitting.key !== null"
                title="This box; C fits every box of the frame"
                @click="fitLocal('box'); releaseFocus($event)">{{ fittingHere ? 'Fitting…' : 'Fit box' }}</button>
        <!-- The same by the AI, shown only the picture around it (Shift+C): slower, for the boxes Fit box misses. -->
        <button v-if="canFit" type="button" class="chip fit ai" id="fitBox" :disabled="fitting.key !== null"
                @click="fitBox(true); releaseFocus($event)">{{ fittingThis ? 'Fitting…' : 'Fit with AI' }}<kbd>⇧C</kbd></button>
      </div>
    </div>
    <!-- Asked for nearly every box: right under the box's heading. -->
    <p class="field-label">Road</p>
    <div class="segmented" id="roads" role="group" aria-label="Which road it applies to">
      <button v-for="item in ROADS" :key="item.id" type="button" :aria-pressed="road === item.id"
              :data-applicability="item.id" @click="setField('applicability', item.id); releaseFocus($event)">{{ item.label }}<kbd class="key-hint">{{ ROAD_KEYS[item.id] }}</kbd></button>
    </div>
    <!-- Most boxes: an other sign for our road, in one tap (S); a truck's sticker, not a sign (V). -->
    <div class="quick-row">
      <!-- An action, never lit: Road and Type above and below say what the box is. -->
      <button type="button" class="chip quick" id="otherOurs" @click="setOtherOurs(); releaseFocus($event)">Other sign · our road<kbd>S</kbd></button>
      <button type="button" class="chip quick" id="onVehicle" title="A sign-like disc or panel on a vehicle, such as a truck's top-speed sticker: not a sign"
              @click="setOnVehicle(); releaseFocus($event)">On a vehicle<kbd>V</kbd></button>
    </div>
    <!-- In Speed and sign review the same picker is below the drive name. -->
    <SpeedValueChips v-if="!report.embedded" :row="row" :detection="detection"/>
    <!-- The lit display over the road (L): the number stays either way. -->
    <div class="quick-row">
      <button type="button" class="chip quick variable" id="variable" :aria-pressed="variable"
              @click="toggleVariable(); releaseFocus($event)">Variable speed limit<kbd>L</kbd></button>
    </div>
    <!-- The embedded editor puts the whole plate editor next to the speed picker instead. -->
    <PlateControls v-if="!report.embedded && detection" :key="row.frame_key + ':' + state.selected" :row="row" :detection="detection"/>
    <div id="valueBlock" class="value-line" :hidden="!hasValueField">
      <label for="value">Other value</label>
      <input v-fill="detection?.value_kph === null || detection?.value_kph === undefined ? '' : String(detection.value_kph)"
             type="number" id="value" min="5" max="250" step="1" inputmode="numeric" placeholder="–" @change="valueChanged">
      <select aria-label="Sign unit" :value="detection?.sign_unit ?? 'unknown'"
              @change="setField('sign_unit', ($event.target as HTMLSelectElement).value as 'km/h' | 'mph' | 'unknown')">
        <option value="unknown">Unit not recorded</option>
        <option value="km/h">km/h</option>
        <option value="mph">mph</option>
      </select>
      <span v-if="state.typing" class="typing" role="status">typing {{ state.typing }}…</span>
    </div>
    <p class="field-error" id="valueError" :hidden="!state.valueError">Type a whole number from 5 to 250, or leave it empty if it cannot be read.</p>
    <p class="field-label">Type</p>
    <div class="chips" id="families" role="group" aria-label="Sign type">
      <button v-for="family in usual" :key="family.id" type="button" class="chip"
              :aria-pressed="detection?.family === family.id" :data-family="family.id"
              @click="setFamily(family.id); releaseFocus($event)">{{ family.label }}</button>
    </div>
    <p class="effect-note">✕ in the list removes a box (a duplicate, a box on nothing); <strong>Not a sign</strong> keeps it, to teach the model what to ignore.</p>
    <div class="chips minor" id="familiesMore" role="group" aria-label="Other types">
      <button v-for="family in minor" :key="family.id" type="button" class="chip"
              :aria-pressed="detection?.family === family.id" :data-family="family.id"
              :title="family.id === 'not_a_sign' ? 'Stays as a negative example; to remove a wrong box use ✕' : undefined"
              @click="setFamily(family.id); releaseFocus($event)">{{ family.label }}</button>
    </div>
    <!-- Other signs by kind: a button makes the box an "Other sign" of that kind. -->
    <div class="kinds" id="signKinds">
      <div v-for="group in KIND_GROUPS" :key="group.label" class="kind-group" role="group" :aria-label="group.label">
        <span class="kind-group-label">{{ group.label }}</span>
        <button v-for="kind in group.kinds" :key="kind.id" type="button" class="chip kind"
                :aria-pressed="detection?.family === 'other_sign' && detection?.sign_type === kind.id"
                :data-kind="kind.id" :title="kind.label + (KIND_KEYS[kind.id] ? ' (' + KIND_KEYS[kind.id] + ')' : '')"
                @click="setKind(kind.id); releaseFocus($event)">{{ kind.chip }}<kbd v-if="KIND_KEYS[kind.id]" class="key-hint">{{ KIND_KEYS[kind.id] }}</kbd></button>
      </div>
      <!-- On or off, with any kind: the children's triangle with "SCHULE" is Children, painted; a number painted on
           the road ("30", "30 ZONE") is a Road marking (#96). -->
      <div class="kind-group" role="group" aria-label="Where">
        <span class="kind-group-label">Where</span>
        <button type="button" class="chip kind" id="painted"
                :aria-pressed="detection !== null && isPainted(detection)"
                title="A number painted on the road (30, 30 ZONE) is a Road marking with that number"
                @click="togglePainted(); releaseFocus($event)">Painted on the road</button>
      </div>
    </div>
    <details>
      <summary>Sign details</summary>
      <div class="grid2">
        <label v-for="key in detailFields" :key="key">{{ SELECT_LABELS[key] }}
          <select :id="key" v-fill="selectValue(key)" @change="selectChanged(key, $event)">
            <option v-for="[value, text] in SELECT_CHOICES[key]" :key="value" :value="value">{{ text }}</option>
          </select>
        </label>
        <label v-for="key in BOOLEAN_FIELDS" :key="key">{{ BOOLEAN_LABELS[key] }}
          <select :id="key" v-fill="booleanValue(key)" @change="booleanChanged(key, $event)">
            <option value="unknown">Not determined</option>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        </label>
        <label>Notes
          <input v-fill="detection?.notes ?? ''" type="text" id="notes" autocomplete="off"
                 @change="setField('notes', ($event.target as HTMLInputElement).value)">
        </label>
      </div>
    </details>
  </section>
</template>
