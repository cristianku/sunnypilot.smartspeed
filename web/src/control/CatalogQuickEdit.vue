<script setup lang="ts">
import {computed, onBeforeUnmount, onMounted, ref, shallowRef, useTemplateRef, watch} from 'vue';
import {api, ApiError, messageOf, post} from '../common/api';
import type {AuthState} from '../common/auth-types';
import {readLocal} from '../review/dom';
import {describe, dropStaleKind, FAMILIES, familyHasValue, holdsPlate, PANELS, PLATE_DISTANCE_MAX_M, ROADS, SIGN_KIND_GROUPS, takesDistance} from '../review/catalog';
import {zoomView} from '../review/overlay';
import {sameBox, scaleBox} from '../review/geometry';
import type {Box, Detection, SaveAnswer} from '../review/types';
import {detail, forgetLegacyDetail, squareBox, updateLegacyReview, type SignRef} from './catalog-edit';

const props = defineProps<{target: SignRef; driveLink: string; startFit: boolean; squareFit: boolean}>();
const emit = defineEmits<{close: []; next: [saved: boolean]}>();
const dialog = useTemplateRef<HTMLDialogElement>('dialog');
const svg = useTemplateRef<SVGSVGElement>('svg');
const form = useTemplateRef<HTMLFormElement>('form');
const saved = shallowRef<Awaited<ReturnType<typeof detail>> | null>(null);
const draft = ref<Detection | null>(null);
const error = ref(''), note = ref(''), busy = ref(false), stale = ref(false), remove = ref(false);
const reviewer = ref(''), local = ref(false), discard = ref(false);
const modified = computed(() => remove.value || (saved.value && JSON.stringify(draft.value) !== JSON.stringify(saved.value.box)));
const hasSpeed = computed(() => draft.value && familyHasValue(draft.value.family));
const label = computed(() => draft.value?.family === 'other_sign' && draft.value.sign_type ? 'kind:' + draft.value.sign_type : draft.value?.family);
const scene = computed(() => {
  const d = saved.value;
  if (!d) return null;
  const v = zoomView(d.box.bbox_xyxy, d.picture.width, d.picture.height);
  return {href: '/route/' + props.target[0] + '/' + d.picture.preview_path, viewBox: `${v.x} ${v.y} ${v.width} ${v.height}`};
});
const bounds = computed(() => draft.value?.bbox_xyxy);
type Drag = {pointer: number; origin: [number, number]; box: Box; moving: boolean};
const drag = shallowRef<Drag | null>(null);
const advancing = ref(false);
let ticket = 0;
onBeforeUnmount(() => { ++ticket; forgetLegacyDetail(); });
async function load(autoFit = false): Promise<void> {
  const n = ++ticket;
  saved.value = null; draft.value = null; stale.value = false; remove.value = false; discard.value = false;
  drag.value = null; advancing.value = false;
  note.value = ''; error.value = ''; busy.value = true;
  try {
    const data = await detail(props.target);
    if (ticket !== n) return;
    saved.value = data; draft.value = structuredClone(data.box);
  } catch (e) { if (ticket === n) error.value = messageOf(e); }
  finally { if (ticket === n) busy.value = false; }
  if (autoFit && ticket === n && draft.value) await fit();
}
onMounted(() => {
  dialog.value?.showModal();
  void api<AuthState>('/api/auth/state').then(a => {
    local.value = a.local; reviewer.value = a.user || (a.local ? readLocal('nnslr-reviewer', '') : '');
  }).catch(e => { error.value = messageOf(e); });
  void load(props.startFit);
});
watch(() => props.target, () => { void load(); });
function close(): void {
  if (busy.value && draft.value) return;
  if (modified.value && !discard.value) { discard.value = true; return; }
  emit('close');
}
function changeLabel(e: Event): void {
  const d = draft.value!;
  const value = (e.target as HTMLSelectElement).value;
  d.family = value.startsWith('kind:') ? 'other_sign' : value;
  d.sign_type = value.startsWith('kind:') ? value.slice(5) : null;
  if (!familyHasValue(d.family)) { d.value_kph = null; d.sign_value = null; d.value_state = 'unknown'; }
  dropStaleKind(d);
}
function number(e: Event): number | null {
  const el = e.target as HTMLInputElement;
  return el.value === '' ? null : el.valueAsNumber;
}
function speed(e: Event): void {
  const d = draft.value!, v = number(e);
  d.value_kph = v; d.sign_value = v; d.value_state = v === null ? 'unknown' : 'reviewed';
}
function metres(e: Event): void {
  const d = draft.value!, v = number(e);
  if (v !== null) d.supplementary_panel = 'distance';
  d.plate_distance_m = v; dropStaleKind(d);
}
function setBox(b: Box, ai = false): void {
  const d = draft.value!;
  d.bbox_xyxy = b; d.box_source = ai ? 'ai_fit' : 'hand'; delete d.check_position;
}
function square(): void {
  if (!saved.value || !bounds.value || !draft.value || holdsPlate(draft.value)) return;
  try {
    const box = squareBox(bounds.value, saved.value.picture.width, saved.value.picture.height);
    if (!sameBox(box, bounds.value)) setBox(box);
  }
  catch (e) { error.value = messageOf(e); }
}
function resize(direction: 1 | -1): void {
  if (!saved.value || !bounds.value) return;
  const box = scaleBox(bounds.value, saved.value.picture.width, saved.value.picture.height, direction);
  if (box) setBox(box);
}
function shortcut(e: KeyboardEvent): void {
  if (e.defaultPrevented || e.isComposing || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === 'Enter' && !e.shiftKey) {
    if (e.target instanceof Element && e.target.closest('a,[data-own-enter]')) return;
    e.preventDefault();
    e.stopPropagation();
    if (!e.repeat && !drag.value && !discard.value) void save();
    return;
  }
  if (busy.value || stale.value || remove.value || !draft.value || advancing.value || drag.value) return;
  if (e.target instanceof Element && e.target.closest('input,select,textarea,[contenteditable="true"]')) return;
  if (!e.shiftKey && e.key.toLowerCase() === 'q') square();
  else if (e.shiftKey && e.key === 'ArrowUp') resize(1);
  else if (e.shiftKey && e.key === 'ArrowDown') resize(-1);
  else return;
  e.preventDefault();
  e.stopPropagation();
}
async function fit(): Promise<void> {
  const data = saved.value, box = draft.value;
  if (!data || !box || busy.value) return;
  const n = ticket; busy.value = true; note.value = 'Fitting…'; error.value = '';
  const [route, run, frame] = props.target, plate = holdsPlate(box);
  try {
    const r = await post<{box: Box | null; includes_plate?: boolean; message: string}>(`/api/route/${route}/frame/${frame}/ai-fit`,
      {run_id: run, box: box.bbox_xyxy, include_plate: plate});
    if (n !== ticket) return;
    note.value = r.message;
    if (!r.box) return;
    if (plate && !r.includes_plate) throw new Error('The server must be updated to fit the sign and its plate together.');
    setBox(props.squareFit && !plate ? squareBox(r.box, data.picture.width, data.picture.height) : r.box, true);
  } catch (e) { if (n === ticket) { error.value = messageOf(e); note.value = ''; } }
  finally { if (n === ticket) busy.value = false; }
}
function point(e: PointerEvent): [number, number] | null {
  const matrix = svg.value?.getScreenCTM(), picture = saved.value?.picture;
  if (!matrix || !picture) return null;
  const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(matrix.inverse());
  return [Math.max(0, Math.min(picture.width, Math.round(p.x))), Math.max(0, Math.min(picture.height, Math.round(p.y)))];
}
function down(e: PointerEvent): void {
  if (busy.value || stale.value || remove.value || advancing.value || !bounds.value || drag.value || e.button !== 0) return;
  const origin = point(e);
  if (!origin) return;
  svg.value?.focus({preventScroll: true});
  drag.value = {pointer: e.pointerId, origin, box: [...bounds.value], moving: (e.target as Element).classList.contains('sct-box')};
  svg.value?.setPointerCapture(e.pointerId);
}
function draw(e: PointerEvent): void {
  const gesture = drag.value;
  if (!gesture || gesture.pointer !== e.pointerId || !saved.value || !bounds.value) return;
  const end = point(e);
  if (!end) return;
  const [x, y] = gesture.origin;
  let box: Box;
  if (gesture.moving) {
    const [left, top, right, bottom] = gesture.box;
    const dx = Math.max(-left, Math.min(saved.value.picture.width - right, end[0] - x));
    const dy = Math.max(-top, Math.min(saved.value.picture.height - bottom, end[1] - y));
    box = [left + dx, top + dy, right + dx, bottom + dy];
  } else {
    if (end[0] === x || end[1] === y) return;
    box = [Math.min(x,end[0]), Math.min(y,end[1]), Math.max(x,end[0]), Math.max(y,end[1])];
  }
  if (!sameBox(box, bounds.value)) setBox(box);
}
function finish(e: PointerEvent): void {
  if (drag.value?.pointer !== e.pointerId) return;
  if (e.type === 'pointerup') draw(e);
  drag.value = null;
}
function advance(changed: boolean): void {
  advancing.value = true;
  emit('next', changed);
}
async function save(): Promise<void> {
  const data = saved.value, box = draft.value;
  if (busy.value || advancing.value || stale.value || !data || !box) return;
  if (!remove.value && !form.value?.reportValidity()) return;
  if (!modified.value) { advance(false); return; }
  if (!reviewer.value.trim()) { error.value = 'Enter your reviewer name or sign in to save.'; return; }
  busy.value = true; error.value = '';
  try {
    const [route, run, frame, id] = props.target, r = data.review;
    const detections = r.detections.flatMap(d => d.detection_id !== id ? [d] : remove.value ? [] : [box]);
    const result = await post<SaveAnswer>(`/api/route/${route}/frame/${frame}`, {run_id: run, base_revision: r.revision, confirmed: true,
      reviewer: reviewer.value.trim(), detections, site_group: r.site_group, encounter_id: r.encounter_id,
      ...(r.country !== undefined ? {country: r.country} : {})});
    updateLegacyReview(result.review); advance(true);
  } catch (e) {
    error.value = messageOf(e); stale.value = e instanceof ApiError && e.status === 409;
    if (stale.value) forgetLegacyDetail();
  }
  finally { busy.value = false; }
}
</script>

<template>
  <dialog ref="dialog" class="sct-dialog" aria-labelledby="quick-title" @cancel.prevent="close" @keydown="shortcut">
    <header><div><h2 id="quick-title">Quick edit</h2><small v-if="saved">{{ describe(saved.box) }} · {{ target[2] }}</small></div>
      <button type="button" class="btn small" @click="close">Close</button></header>
    <form ref="form" @submit.prevent="save">
      <div v-if="saved && draft && scene && bounds" class="sct-edit-grid">
        <div>
          <svg ref="svg" class="sct-zoom" :class="{'sct-grabbing': drag?.moving}" :viewBox="scene.viewBox" role="img" tabindex="0" aria-label="Sign with editable box"
               @pointerdown="down" @pointermove="draw" @pointerup="finish" @pointercancel="finish" @lostpointercapture="finish">
            <image :href="scene.href" x="0" y="0" :width="saved.picture.width" :height="saved.picture.height" preserveAspectRatio="none"/>
            <rect class="sct-box" :x="bounds[0]" :y="bounds[1]" :width="bounds[2]-bounds[0]" :height="bounds[3]-bounds[1]"
                  fill="none" stroke="#ffdc00" stroke-width="2" vector-effect="non-scaling-stroke"/>
          </svg>
          <small>Drag inside the box to move it, or start outside to redraw.</small>
          <div class="sct-actions"><button type="button" class="btn small" :disabled="busy || stale || remove" @click="fit">Fit with AI</button>
            <button type="button" class="btn small" :disabled="busy || stale || remove || holdsPlate(draft)" aria-keyshortcuts="Q" @click="square">Square <kbd>Q</kbd></button>
            <button type="button" class="btn small" :disabled="busy || stale || remove" aria-keyshortcuts="Shift+ArrowUp" @click="resize(1)">Grow <kbd>⇧ ↑</kbd></button>
            <button type="button" class="btn small" :disabled="busy || stale || remove" aria-keyshortcuts="Shift+ArrowDown" @click="resize(-1)">Shrink <kbd>⇧ ↓</kbd></button></div>
        </div>
        <fieldset :disabled="busy || stale || remove">
          <label>Sign<select :value="label" @change="changeLabel">
            <optgroup label="Sign type"><option v-for="f in FAMILIES" :key="f.id" :value="f.id">{{ f.label }}</option></optgroup>
            <optgroup v-for="g in SIGN_KIND_GROUPS" :key="g.label" :label="g.label"><option v-for="k in g.kinds" :key="k.id" :value="'kind:'+k.id">{{ k.chip }}</option></optgroup>
          </select></label>
          <label v-if="hasSpeed">Speed ({{ draft.sign_unit === 'mph' ? 'mph' : 'km/h' }})<input type="number" min="5" max="250" step="1"
            :value="draft.sign_value ?? draft.value_kph ?? ''" placeholder="Unknown" @input="speed"></label>
          <label>Road<select v-model="draft.applicability"><option v-for="r in ROADS" :key="r.id" :value="r.id">{{ r.label }}</option></select></label>
          <label v-if="takesDistance(draft)">Plate<select :value="draft.supplementary_panel || 'unknown'" @change="draft.supplementary_panel = ($event.target as HTMLSelectElement).value; dropStaleKind(draft)">
            <option value="unknown">Unknown</option><option value="unresolved">Unresolved</option>
            <option v-for="p in PANELS" :key="p.id" :value="p.id">{{ p.label }}</option></select></label>
          <label v-if="takesDistance(draft)">Distance (m)<input type="number" min="1" :max="PLATE_DISTANCE_MAX_M" step="1" :value="draft.plate_distance_m ?? ''" @input="metres"></label>
          <label v-if="draft.family === 'maximum_speed' && draft.supplementary_panel === 'time'">Hours on plate
            <input type="text" placeholder="22-6" :value="draft.plate_hours ?? ''"
                   @input="draft.plate_hours = ($event.target as HTMLInputElement).value.trim() || null">
          </label>
        </fieldset>
      </div>
      <p v-if="busy" role="status">{{ note || 'Loading…' }}</p><p v-else-if="note" role="status">{{ note }}</p>
      <p v-if="error" role="alert">{{ error }} <button v-if="stale || !saved" type="button" class="btn small" data-own-enter @click="load()">Reload sign</button></p>
      <label v-if="local && !readLocal('nnslr-reviewer','')" class="sct-who">Reviewer<input v-model="reviewer" autocomplete="name"></label>
      <div class="sct-edit-footer">
        <a :href="driveLink" target="_blank" rel="noopener" data-full-navigation>Open in drive ↗</a>
        <button v-if="saved" type="button" class="btn small" :disabled="busy || stale" @click="remove = !remove">{{ remove ? 'Keep box' : 'Remove box' }}</button>
        <button v-if="discard" type="button" class="btn small" data-own-enter @click="$emit('close')">Discard and close</button>
        <button v-if="!modified && (!saved || stale)" type="button" class="btn small" data-own-enter @click="advance(false)">Skip</button>
        <button type="submit" class="btn primary" :disabled="busy || advancing || !saved || stale" aria-keyshortcuts="Enter">{{ remove ? 'Remove and next' : modified ? 'Save and next' : 'Next sign' }} <kbd aria-hidden="true">↵</kbd></button>
      </div>
    </form>
  </dialog>
</template>
