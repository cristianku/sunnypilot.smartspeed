<script setup lang="ts">
import {computed, ref, shallowRef, useTemplateRef, watch} from 'vue';
import {api, ApiError, messageOf, post} from '../common/api';
import type {ActionName, ActionState, Job, PanelStatus} from '../common/panel-types';
import {plural} from '../common/format';
import {toast} from '../composables/toast';
import DriveReviewDrive from './DriveReviewDrive.vue';
import JobProgress from './JobProgress.vue';
import Segmented from './Segmented.vue';
import {clockOf, valueAt, type Series} from './drive-review';
import {jobOf} from './jobs';
import {MIN_BLOCK_NS, speedAt, speedIndex, type SpeedPoint, type SpeedRow} from './speed-review';
import {driveInterventionScope, goTo, tabDrive, tabIntervention} from './tabs';

type Choice = 'driver' | 'block' | 'skip';
interface Clip {
  event: string; route: string; kind: 'gas' | 'brake' | 'cancel'; at_ns: number; second: number; source: 'openpilot' | 'cruise';
  decision: Choice | 'pending'; start_ns: number | null; end_ns: number | null; kph: number | null;
  reviewer: string | null; revision: number; active: number;
}
interface Detail {
  span: [number, number]; revision: number; speed: SpeedPoint[]; owners: Series;
  vision: SpeedPoint[]; cruise: SpeedPoint[]; pre_roll_ns: number;
}
const props = defineProps<{readOnly: boolean; status?: PanelStatus | null; drives?: SpeedRow[]; driveQueue?: string}>();
const emit = defineEmits<{run: [name: ActionName, state: ActionState]; stop: [job: Job]; details: [job: Job]}>();
const labels = {gas: 'Accelerator', brake: 'Brake', cancel: 'Cancel'};
const decisions = {pending: 'To review', driver: 'Your speed', block: 'Speed block', skip: 'Skipped'};
const groups = [{key: 'pending', label: 'To review'}, {key: 'reviewed', label: 'Reviewed'}] as const;
const group = ref<'pending' | 'reviewed'>('pending');
const clips = shallowRef<Clip[] | null>(null);
const error = ref('');
type Scope = 'all' | 'eligible' | 'not_scanned' | 'historical' | 'undated' | 'dashcam';
const scope = ref<Scope>('all');
const scopeNotes: Partial<Record<Scope, string>> = {
  historical: 'This drive was recorded before intervention review started. Only new assisted drives are reviewed.',
  undated: 'There is no GPS recording date for this drive yet.',
  not_scanned: 'Read the drive logs to check for interventions.',
  dashcam: 'This was a dashcam drive. Interventions are reviewed on assisted drives.',
};
const queueRoute = computed(() => props.driveQueue ?? (driveInterventionScope.value ? tabDrive.value : null));
let request = 0;
async function loadQueue(): Promise<void> {
  const id = ++request, route = queueRoute.value;
  try {
    const url = '/api/speed-review/interventions' + (route ? '?' + new URLSearchParams({drive: route}) : '');
    const answer = await api<{scope?: Scope; events: Clip[]}>(url);
    if (id !== request) return;
    if (!Array.isArray(answer.events)) throw new Error('Could not read the intervention list.');
    // A running server from before the drive filter returns the global queue. Never open another drive's clip here.
    clips.value = route ? answer.events.filter(c => c.route === route) : answer.events;
    scope.value = route && (!answer.scope || answer.scope === 'all') ? 'eligible' : answer.scope ?? 'all';
    error.value = '';
  } catch (e) { if (id === request) error.value = messageOf(e); }
}
watch(queueRoute, () => { clips.value = null; error.value = ''; void loadQueue(); }, {immediate: true});
const job = computed(() => jobOf(props.status ?? null, ['interventions']));
const action = computed(() => props.status?.actions.interventions);
watch(() => job.value && job.value.id + ':' + job.value.state, () => {
  // A short search may already be finished on the first status update after clicking Find.
  if (job.value && job.value.state !== 'running') void loadQueue();
});
const labelByRoute = computed(() => new Map((props.drives ?? []).map(d => [d.route, [d.when, d.title].filter(Boolean).join(' · ')])));
const filtered = computed(() => (clips.value ?? []).filter(c => (c.decision === 'pending') === (group.value === 'pending')));
const batch = props.driveQueue ? 12 : 60;
const limit = ref(batch);
watch(group, () => { limit.value = batch; });
const clip = computed(() => clips.value?.find(c => c.route === tabDrive.value && c.event === tabIntervention.value) ?? null);
const allDrivesUrl = '/speed-review?intervention=queue';
const queueUrl = computed(() => queueRoute.value ? '/speed-review?' + new URLSearchParams({drive: queueRoute.value, intervention: 'queue'}) : allDrivesUrl);
const href = (c: Clip) => '/speed-review?' + new URLSearchParams({drive: c.route, intervention: c.event,
  ...(queueRoute.value ? {scope: 'drive'} : {})});
function next(): void {
  const rows = clips.value ?? [];
  const index = clip.value ? rows.indexOf(clip.value) : -1;
  const other = [...rows.slice(index + 1), ...rows.slice(0, index)].find(c => c.decision === 'pending');
  if (other) goTo('speedreview', '', other.route, other.event, !!queueRoute.value);
  else goTo('speedreview', '', queueRoute.value, 'queue');
}

const player = useTemplateRef<InstanceType<typeof DriveReviewDrive>>('player');
const detail = shallowRef<Detail | null>(null);
const detailError = ref('');
const playerT0 = ref<number | null>(null);
const at = ref(0);
const ns = computed(() => Math.round((playerT0.value ?? 0) + at.value * 1e9));
const second = (t: number) => (t - (detail.value?.span[0] ?? 0)) / 1e9;
let generation = 0;
async function loadDrive(route: string | null): Promise<void> {
  const id = ++generation;
  detail.value = null;
  detailError.value = '';
  if (!route) return;
  try {
    const data = await api<Detail>('/api/speed-review/drives/' + encodeURIComponent(route) + '/interventions');
    if (!Array.isArray(data.span)) throw new Error('Could not read the recorded speeds.');
    if (generation === id) detail.value = data;
  } catch (e) { if (generation === id) detailError.value = messageOf(e); }
}
watch(() => clip.value?.route ?? null, route => { playerT0.value = null; at.value = 0; void loadDrive(route); }, {immediate: true});
function follow(cursor: number, t0: number): void { playerT0.value = t0; at.value = cursor; }
const start = ref<number | null>(null);
const end = ref<number | null>(null);
const kph = ref<number | null>(null);
const correcting = ref(false);
const busy = ref(false);
const issue = ref('');
const writing = computed(() => !!detail.value && !props.readOnly && !busy.value);
const valid = computed(() => start.value !== null && end.value !== null && end.value - start.value >= MIN_BLOCK_NS);
const dashboard = computed(() => speedAt(detail.value?.speed ?? [], ns.value));
const recorded = computed(() => speedAt(detail.value?.vision ?? [], ns.value));
const cruise = computed(() => speedAt(detail.value?.cruise ?? [], ns.value));
const owner = computed(() => valueAt(detail.value?.owners, ns.value));
const ownerName = computed(() => owner.value === 'driver' ? 'Driver' : owner.value === 'openpilot' ? 'openpilot'
  : owner.value === 'cruise' ? 'Cruise' : 'Unknown');
const humanInterval = computed(() => {
  if (!detail.value || !valid.value || start.value === null || end.value === null) return false;
  const {owners, speed} = detail.value;
  return valueAt(owners, start.value) === 'driver' && speedAt(speed, start.value) !== null
    && !owners.some(([t,v]) => t > start.value! && t < end.value! && v !== 'driver')
    && !speed.some(([t,v]) => t > start.value! && t < end.value! && v === null);
});
watch([() => clip.value?.event, () => clip.value?.revision, () => tabDrive.value], () => {
  start.value = clip.value?.start_ns ?? null;
  end.value = clip.value?.end_ns ?? null;
  correcting.value = clip.value?.decision === 'block';
  kph.value = clip.value?.kph ?? null;
  issue.value = '';
}, {immediate: true});
let positioned = '';
watch([clip, detail, playerT0, player], () => {
  const c = clip.value, d = detail.value;
  if (!c || !d || playerT0.value !== d.span[0] || !player.value) { positioned = ''; return; }
  const key = c.route + '/' + c.event;
  if (positioned !== key) {
    positioned = key;
    player.value.pause();
    player.value.seek(Math.max(0, second(c.at_ns - d.pre_roll_ns)));
  }
}, {flush: 'post'});
function mark(which: 'start' | 'end'): void {
  if (!detail.value || playerT0.value !== detail.value.span[0]) return;
  player.value?.pause();
  const t = Math.min(detail.value.span[1], Math.max(detail.value.span[0], ns.value));
  if (which === 'start') start.value = t;
  else end.value = t;
  issue.value = '';
}
async function save(choice: Choice): Promise<void> {
  const c = clip.value, d = detail.value;
  if (!c || !d || !writing.value || (choice !== 'skip' && !valid.value)) return;
  busy.value = true;
  issue.value = '';
  const start_ns = choice === 'skip' ? null : start.value, end_ns = choice === 'skip' ? null : end.value;
  const speed = choice === 'block' ? kph.value : null;
  try {
    const answer = await post<{revision: number}>('/api/speed-review/drives/' + c.route + '/interventions', {
      event: c.event, decision: choice, start_ns, end_ns, kph: speed, base_revision: d.revision, event_revision: c.revision,
    });
    clips.value = (clips.value ?? []).map(item => item === c
      ? {...c, decision: choice, start_ns, end_ns, kph: speed, revision: answer.revision} : item);
    if (tabDrive.value === c.route) detail.value = {...d, revision: answer.revision};
    toast(choice === 'skip' ? 'Skipped.' : 'Reviewed interval saved.');
    if (tabDrive.value === c.route && tabIntervention.value === c.event) next();
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      await Promise.all([loadQueue(), loadDrive(tabDrive.value)]);
    }
    issue.value = messageOf(e);
  } finally { busy.value = false; }
}

// Step curves break across missing log signals. Never substitute cruise for a missing vision proposal.
const curves = computed(() => ({speed: detail.value?.speed ?? [], vision: detail.value?.vision ?? [], cruise: detail.value?.cruise ?? []}));
const scale = computed(() => Object.values(curves.value).reduce((n, points) => points.reduce((m,p) => Math.max(m,p[1] ?? 0),n),50));
function path(points: SpeedPoint[], from: number, to: number, x: (v: number) => number): string {
  const t0 = detail.value?.span[0] ?? 0;
  let line = '', open = false;
  for (let i = Math.max(0, speedIndex(points, t0 + from * 1e9)); i < points.length; i++) {
    const [t, v] = points[i]!, time = second(t);
    if (time > to) break;
    const px = x(Math.max(from, time)).toFixed(1);
    if (open) line += 'H' + px;
    if (v !== null) {
      line += (open ? 'V' : 'M' + px + ',') + (52 - 46 * v / scale.value).toFixed(1);
    }
    open = v !== null;
  }
  return open ? line + 'H' + x(to).toFixed(1) : line;
}
</script>

<template>
  <section v-if="driveQueue || !tabDrive" :class="driveQueue ? 'iv-drive' : 'card sr-drives'" :aria-labelledby="driveQueue ? 'sr-card-title' : 'iv-title'">
    <div class="ds-head"><h3 v-if="driveQueue" id="sr-card-title">Interventions on this drive</h3><h2 v-else id="iv-title">Manual interventions</h2>
      <a v-if="driveQueue" class="link-button" :href="allDrivesUrl">All drives ›</a>
      <button v-else type="button" class="btn small" :disabled="!action?.allowed || readOnly || job?.state === 'running'"
              :title="action?.reason || 'Find pedal overrides and cancellations in the scanned logs'"
              @click="action && emit('run', 'interventions', action)">Find interventions</button></div>
    <JobProgress v-if="job && job.state !== 'ok'" :job="job" @details="emit('details', $event)" @stop="emit('stop', $event)"/>
    <p v-else-if="job" class="import-last">Search complete · <button class="link-button" @click="emit('details', job)">Details</button></p>
    <div v-if="!driveQueue || scope === 'eligible'" class="sr-cars"><Segmented v-model="group" :options="groups" label="Intervention status"/></div>
    <p v-if="error" class="empty">{{ error }} <button class="link-button" @click="loadQueue">Try again</button></p>
    <p v-else-if="clips === null" class="empty">Loading interventions…</p>
    <p v-else-if="scopeNotes[scope]" class="dr-hint">{{ scopeNotes[scope] }}</p>
    <p v-else-if="!filtered.length" class="empty">{{ group === 'reviewed' ? 'No interventions reviewed yet.'
      : clips.length ? 'Every intervention is reviewed.' : driveQueue ? 'No interventions on this drive. Only newly recorded assisted drives are included.'
      : 'No interventions to review yet. Only newly recorded assisted drives are included.' }}</p>
    <template v-else><p class="dr-hint">{{ plural(filtered.length, 'intervention') }}</p><ul class="rt-list">
      <li v-for="c in filtered.slice(0,limit)" :key="c.route+c.event" class="rt-drive">
        <a class="rt-open" :href="href(c)"><div class="rt-name"><strong>{{ labels[c.kind] }} · {{ clockOf(c.second) }}</strong>
           <span v-if="!driveQueue">{{ labelByRoute.get(c.route) || c.route }}</span></div>
          <p class="dr-drive-state">{{ c.source === 'openpilot' ? 'openpilot longitudinal' : 'Cruise' }} · {{ decisions[c.decision] }}</p>
          <span class="data-chev rt-go" aria-hidden="true">›</span></a></li></ul>
       <button v-if="filtered.length > limit" type="button" class="btn small" @click="limit += batch">Show more</button></template>
  </section>
  <section v-else-if="!clip || detailError" class="card">
    <a class="link-button" :href="queueUrl">‹ Interventions</a>
    <p>{{ detailError || error || (clips === null ? 'Loading…' : 'This intervention is no longer in the queue.') }}</p>
  </section>
  <DriveReviewDrive v-else :key="clip.route" ref="player" :route="clip.route" :read-only="readOnly"
                    tab="speedreview" back-label="Interventions" back-item="queue" :back-drive="queueRoute" :rates="[1,2,4]" sharp
                   start-zoom="one" simple-lanes :car-kph="dashboard" @time="follow">
    <template #lane="{from,to,x,w,width}"><div v-if="detail" class="dr-lane sr-lane">
      <span class="dr-lane-name">Speeds<small>km/h</small></span>
      <svg :width="width" height="58" :viewBox="`0 0 ${width} 58`" aria-hidden="true">
        <rect v-if="valid && start !== null && end !== null && second(end)>from && second(start)<to"
              :x="x(Math.max(from,second(start)))" y="1" :width="w(Math.max(from,second(start)),Math.min(to,second(end)))"
              height="55" class="sr-block"/>
        <path v-for="(points, key) in curves" :key="key" :class="['rg-line','iv-'+key]" :d="path(points,from,to,x)"/>
        <line v-if="second(clip.at_ns)>=from && second(clip.at_ns)<=to" class="sr-block-start" y1="0" y2="58"
              :x1="x(second(clip.at_ns))" :x2="x(second(clip.at_ns))"/>
      </svg></div></template>
    <template #side><section class="sr-card" aria-labelledby="iv-event">
      <h3 id="iv-event">{{ labels[clip.kind] }} · {{ clockOf(clip.second) }}</h3>
      <p class="dr-hint">Before the intervention: {{ clip.source === 'openpilot' ? 'openpilot longitudinal' : 'cruise' }}.
        <template v-if="clip.decision !== 'pending'">{{ decisions[clip.decision] }} · {{ clip.reviewer }}.</template>
        <template v-if="!clip.active">Not found in the latest scan; your review is kept.</template></p>
      <div class="iv-values"><span>Your speed <b>{{ dashboard ?? '–' }}</b></span>
        <span>Vision proposal <b>{{ recorded ?? '–' }}</b></span><span>Cruise set <b>{{ cruise ?? '–' }}</b></span></div>
      <p class="dr-hint">km/h · Speed controlled by: {{ ownerName }}</p>
      <div class="sr-tools"><button class="link-button" @click="player?.seek(clip.second)">Go to intervention</button>
        <button class="link-button" :disabled="busy" @click="next">Next intervention ›</button></div>
      <div class="sr-new"><strong>Select the interval to learn</strong>
        <div class="sr-tools"><button class="btn small" :disabled="!writing" @click="mark('start')">From here · {{ start === null ? '–' : clockOf(second(start)) }}</button>
          <button class="btn small" :disabled="!writing" @click="mark('end')">To here · {{ end === null ? '–' : clockOf(second(end)) }}</button></div>
        <p v-if="start !== null && end !== null && !valid" class="dr-hint">Choose an end at least one second after the start.</p>
        <p v-else-if="valid && !humanInterval" class="dr-hint">Part of this interval is machine-controlled or unknown. Adjust it or set a speed block.</p>
        <button type="button" class="btn primary" :disabled="!writing || !humanInterval" @click="save('driver')">Use my speed</button>
        <button v-if="!correcting" type="button" class="btn small" :disabled="!writing || !valid"
                @click="correcting = true; kph = dashboard">Correct with a block</button>
        <label v-else>Block speed (km/h)
          <input v-model.number="kph" class="sr-kph-value" type="number" min="1" max="200" step="1" :disabled="!writing">
          <button type="button" class="btn" :disabled="!writing || !valid || !Number.isInteger(kph) || !kph || kph<1 || kph>200"
                  @click="save('block')">Save speed block</button></label>
      </div>
      <button type="button" class="btn small" :disabled="!writing" @click="save('skip')">Skip · nothing to learn</button>
      <p v-if="issue" role="alert" class="dr-hint">{{ issue }}</p>
    </section></template>
  </DriveReviewDrive>
</template>
