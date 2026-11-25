<script setup lang="ts">
import {computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, useTemplateRef, watch} from 'vue';
import {api, ApiError, messageOf, post} from '../common/api';
import type {AuthState} from '../common/auth-types';
import {readLocal} from '../review/dom';
import {describe, dropStaleKind, FAMILIES, familyHasValue, holdsPlate, isPainted, PANELS, PLATE_DISTANCE_MAX_M,
  ROADS, SIGN_KIND_GROUPS, takesDistance, togglePaintedBox} from '../review/catalog';
import {sceneScale, zoomView} from '../review/overlay';
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
const viewAnchor = ref<Box | null>(null);
const scene = computed(() => {
  const d = saved.value;
  if (!d) return null;
  const v = zoomView(viewAnchor.value ?? d.box.bbox_xyxy, d.picture.width, d.picture.height);
  return {href: '/route/' + props.target[0] + '/' + d.picture.preview_path, v, viewBox: `${v.x} ${v.y} ${v.width} ${v.height}`};
});
const bounds = computed(() => draft.value?.bbox_xyxy);
const grips = ['n','s','w','e','nw','ne','sw','se'] as const;
type Grip = typeof grips[number];
type Drag = {pointer: number; origin: [number, number]; box: Box; mode: Grip | 'move' | 'draw'};
const drag = shallowRef<Drag | null>(null);
const viewport = shallowRef({width: 0, height: 0});
watch(svg, (element, _, cleanup) => {
  if (!element) return;
  const measure = () => { viewport.value = element.getBoundingClientRect(); };
  measure();
  const observer = new ResizeObserver(measure);
  observer.observe(element); cleanup(() => observer.disconnect());
}, {flush:'post'});
const handles = computed(() => {
  const box = bounds.value, v = scene.value?.v;
  if (!box || !v) return [];
  const [left,top,right,bottom] = box, w = right-left, h = bottom-top;
  const k = sceneScale(viewport.value, v.width, v.height);
  const size = Math.min(7/k, w/4, h/4), hitX = Math.min(22/k, w/2), hitY = Math.min(22/k, h/2);
  return grips.map(grip => {
    const horizontal = grip.includes('w') || grip.includes('e'), vertical = grip.includes('n') || grip.includes('s');
    const x = grip.includes('w') ? left : grip.includes('e') ? right : (left+right)/2;
    const y = grip.includes('n') ? top : grip.includes('s') ? bottom : (top+bottom)/2;
    return {grip, x, y, size, width: horizontal ? hitX : w, height: vertical ? hitY : h};
  });
});
const advancing = ref(false);
let ticket = 0;
onBeforeUnmount(() => { ++ticket; forgetLegacyDetail(); });
async function load(autoFit = false): Promise<void> {
  const n = ++ticket;
  saved.value = null; draft.value = null; stale.value = false; remove.value = false; discard.value = false;
  drag.value = null; viewAnchor.value = null; advancing.value = false;
  note.value = ''; error.value = ''; busy.value = true;
  try {
    const data = await detail(props.target);
    if (ticket !== n) return;
    saved.value = data; draft.value = structuredClone(data.box);
    await nextTick();
    if (ticket === n && dialog.value?.open) svg.value?.focus({preventScroll: true});
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
const motorwayDirection = 'kind:motorway_direction';
function changeLabel(value: string): void {
  if (value === label.value) return;
  const d = draft.value!;
  if (d.family === 'road_marking_candidate' && (value.startsWith('kind:') || value === 'other_sign')) d.painted = true;
  d.family = value.startsWith('kind:') ? 'other_sign' : value;
  d.sign_type = value.startsWith('kind:') ? value.slice(5) : null;
  if (!familyHasValue(d.family)) { d.value_kph = null; d.sign_value = null; d.value_state = 'unknown'; }
  dropStaleKind(d);
}
function number(e: Event): number | null {
  const el = e.target as HTMLInputElement;
  return el.value === '' ? null : el.valueAsNumber;
}
function setSpeed(v: number | null): void {
  const d = draft.value!;
  d.value_kph = v; d.sign_value = v; d.value_state = v === null ? 'unknown' : 'reviewed';
}
function metres(e: Event): void {
  const d = draft.value!, v = number(e);
  if (v !== null) d.supplementary_panel = 'distance';
  d.plate_distance_m = v; dropStaleKind(d);
}
function setBox(b: Box, source: 'hand' | 'fit' | 'ai_fit' = 'hand'): void {
  const d = draft.value!;
  d.bbox_xyxy = b; d.box_source = source; delete d.check_position;
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
  const field = e.target instanceof Element && e.target.closest('input,select,textarea,[contenteditable="true"]');
  if (e.key === '?' && hasSpeed.value && (!field || field.id === 'quick-speed')) setSpeed(null);
  else if (field) return;
  else if (e.key.toLowerCase() === 'm') changeLabel(motorwayDirection);
  else if (e.key.toLowerCase() === 'c') {
    if (!e.repeat) void fit(e.shiftKey ? 'ai' : 'local');
  }
  else if (!e.shiftKey && e.key.toLowerCase() === 'q') square();
  else if (e.shiftKey && e.key === 'ArrowUp') resize(1);
  else if (e.shiftKey && e.key === 'ArrowDown') resize(-1);
  else return;
  e.preventDefault();
  e.stopPropagation();
}
async function fit(mode: 'local' | 'ai' = 'ai'): Promise<void> {
  const data = saved.value, box = draft.value;
  if (!data || !box || busy.value || stale.value || advancing.value || remove.value) return;
  const [route, run, frame] = props.target, plate = holdsPlate(box);
  if (mode === 'local' && plate) {
    note.value = 'The box includes a plate. Use Fit with AI to fit both together.'; return;
  }
  const n = ticket; busy.value = true; note.value = 'Fitting…'; error.value = '';
  try {
    if (mode === 'local') {
      const r = await post<{boxes: (Box | null)[]; ms: number}>(`/api/route/${route}/frame/${frame}/fit`,
        {run_id: run, boxes: [box.bbox_xyxy]});
      if (n !== ticket) return;
      const fitted = r.boxes[0];
      if (!fitted || fitted.every((v, i) => Math.abs(v - box.bbox_xyxy[i]!) < 1)) {
        note.value = 'No better box found.'; return;
      }
      setBox(fitted, 'fit'); note.value = `Box fitted · ${r.ms} ms`; return;
    }
    const r = await post<{box: Box | null; includes_plate?: boolean; message: string}>(`/api/route/${route}/frame/${frame}/ai-fit`,
      {run_id: run, box: box.bbox_xyxy, include_plate: plate});
    if (n !== ticket) return;
    note.value = r.message;
    if (!r.box) return;
    if (plate && !r.includes_plate) throw new Error('The server must be updated to fit the sign and its plate together.');
    setBox(props.squareFit && !plate ? squareBox(r.box, data.picture.width, data.picture.height) : r.box, 'ai_fit');
  } catch (e) { if (n === ticket) { error.value = messageOf(e); note.value = ''; } }
  finally { if (n === ticket) busy.value = false; }
}
function point(e: PointerEvent): [number, number] | null {
  const matrix = svg.value?.getScreenCTM(), picture = saved.value?.picture;
  if (!matrix || !picture) return null;
  const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(matrix.inverse());
  // One screen pixel can be a small fraction of a frame pixel in the zoom.
  return [Math.max(0, Math.min(picture.width, p.x)), Math.max(0, Math.min(picture.height, p.y))];
}
function down(e: PointerEvent): void {
  if (busy.value || stale.value || remove.value || advancing.value || !bounds.value || drag.value || e.button !== 0) return;
  const origin = point(e);
  if (!origin) return;
  svg.value?.focus({preventScroll: true});
  const target = e.target as Element, grip = grips.find(g => g === target.getAttribute('data-grip'));
  drag.value = {pointer: e.pointerId, origin, box: [...bounds.value], mode: grip ?? (target.classList.contains('sct-box') ? 'move' : 'draw')};
  svg.value?.setPointerCapture(e.pointerId);
}
function draw(e: PointerEvent): void {
  const gesture = drag.value;
  if (!gesture || gesture.pointer !== e.pointerId || !saved.value || !bounds.value) return;
  const end = point(e);
  if (!end) return;
  const [x, y] = gesture.origin;
  let box: Box;
  if (gesture.mode === 'move') {
    const [left, top, right, bottom] = gesture.box;
    const dx = Math.max(-left, Math.min(saved.value.picture.width - right, end[0] - x));
    const dy = Math.max(-top, Math.min(saved.value.picture.height - bottom, end[1] - y));
    box = [left + dx, top + dy, right + dx, bottom + dy];
  } else if (gesture.mode === 'draw') {
    if (Math.abs(end[0] - x) < 1 || Math.abs(end[1] - y) < 1) return;
    box = [Math.min(x,end[0]), Math.min(y,end[1]), Math.max(x,end[0]), Math.max(y,end[1])];
  } else {
    const [left, top, right, bottom] = gesture.box, dx = end[0]-x, dy = end[1]-y;
    const minW = Math.min(1, right-left), minH = Math.min(1, bottom-top);
    box = [
      gesture.mode.includes('w') ? Math.max(0, Math.min(right-minW, left+dx)) : left,
      gesture.mode.includes('n') ? Math.max(0, Math.min(bottom-minH, top+dy)) : top,
      gesture.mode.includes('e') ? Math.min(saved.value.picture.width, Math.max(left+minW, right+dx)) : right,
      gesture.mode.includes('s') ? Math.min(saved.value.picture.height, Math.max(top+minH, bottom+dy)) : bottom,
    ];
  }
  if (box.some((value, i) => value !== bounds.value![i])) setBox(box);
}
function finish(e: PointerEvent): void {
  if (drag.value?.pointer !== e.pointerId) return;
  if (e.type === 'pointerup') draw(e);
  drag.value = null;
  const b = bounds.value, v = scene.value?.v;
  if (b && v && (b[0]<v.x || b[1]<v.y || b[2]>v.x+v.width || b[3]>v.y+v.height)) viewAnchor.value = [...b];
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
    <header><div><h2 id="quick-title">Quick edit</h2><small v-if="draft">{{ describe(draft) }} · {{ target[2] }}</small></div>
      <button type="button" class="btn small" @click="close">Close</button></header>
    <form ref="form" @submit.prevent="save">
      <div v-if="saved && draft && scene && bounds" class="sct-edit-grid">
        <div>
          <svg ref="svg" class="sct-zoom" :class="{'sct-grabbing': drag?.mode === 'move'}" :data-grip="drag?.mode" :viewBox="scene.viewBox" role="img" tabindex="0" aria-label="Sign with editable box"
               @pointerdown="down" @pointermove="draw" @pointerup="finish" @pointercancel="finish" @lostpointercapture="finish">
            <image :href="scene.href" x="0" y="0" :width="saved.picture.width" :height="saved.picture.height" preserveAspectRatio="none"/>
            <rect class="sct-box" :x="bounds[0]" :y="bounds[1]" :width="bounds[2]-bounds[0]" :height="bounds[3]-bounds[1]"
                  fill="none" stroke="#ffdc00" stroke-width="2" vector-effect="non-scaling-stroke"/>
            <g v-for="h in handles" :key="h.grip">
              <rect :x="h.x-h.size/2" :y="h.y-h.size/2" :width="h.size" :height="h.size" fill="#ffdc00" stroke="#282000"
                    stroke-width="1" vector-effect="non-scaling-stroke" pointer-events="none"/>
              <rect :data-grip="h.grip" :x="h.x-h.width/2" :y="h.y-h.height/2" :width="h.width" :height="h.height" fill="transparent"/>
            </g>
          </svg>
          <small>Drag an edge or corner to resize, the centre to move, or outside to redraw.</small>
          <div class="sct-actions">
            <button type="button" class="btn small" :disabled="busy || stale || remove || holdsPlate(draft)" aria-keyshortcuts="C"
                    :title="holdsPlate(draft) ? 'The box includes a plate; use Fit with AI for both.' : 'Fit the selected box locally'"
                    @click="fit('local')">Fit box <kbd>C</kbd></button>
            <button type="button" class="btn small" :disabled="busy || stale || remove" aria-keyshortcuts="Shift+C" @click="fit()">Fit with AI <kbd>⇧ C</kbd></button>
            <button type="button" class="btn small" :disabled="busy || stale || remove || holdsPlate(draft)" aria-keyshortcuts="Q" @click="square">Square <kbd>Q</kbd></button>
            <button type="button" class="btn small" :disabled="busy || stale || remove" aria-keyshortcuts="Shift+ArrowUp" @click="resize(1)">Grow <kbd>⇧ ↑</kbd></button>
            <button type="button" class="btn small" :disabled="busy || stale || remove" aria-keyshortcuts="Shift+ArrowDown" @click="resize(-1)">Shrink <kbd>⇧ ↓</kbd></button></div>
        </div>
        <fieldset :disabled="busy || stale || remove">
          <div><div class="sct-sign-heading"><label for="quick-sign">Sign</label>
            <button class="btn small" type="button" aria-keyshortcuts="M" :aria-pressed="label === motorwayDirection"
                    @click="changeLabel(motorwayDirection)">Motorway direction <kbd>M</kbd></button></div>
            <select id="quick-sign" :value="label" @change="changeLabel(($event.target as HTMLSelectElement).value)">
            <optgroup label="Sign type"><option v-for="f in FAMILIES" :key="f.id" :value="f.id">{{ f.label }}</option></optgroup>
            <optgroup v-for="g in SIGN_KIND_GROUPS" :key="g.label" :label="g.label"><option v-for="k in g.kinds" :key="k.id" :value="'kind:'+k.id">{{ k.chip }}</option></optgroup>
            </select></div>
          <label class="sct-painted"><input type="checkbox" :checked="isPainted(draft)" @change="togglePaintedBox(draft)"> Painted on the road</label>
          <div v-if="hasSpeed"><label for="quick-speed">Speed ({{ draft.sign_unit === 'mph' ? 'mph' : 'km/h' }})</label>
            <div class="sct-speed-entry"><input id="quick-speed" type="number" min="5" max="250" step="1"
              :value="draft.sign_value ?? draft.value_kph ?? ''" placeholder="Unknown" @input="setSpeed(number($event))">
              <button type="button" class="btn small" aria-keyshortcuts="?" aria-label="Number not readable" title="Number not readable (?)"
                      :aria-pressed="draft.sign_value == null && draft.value_kph == null" @click="setSpeed(null)">?</button></div>
          </div>
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
