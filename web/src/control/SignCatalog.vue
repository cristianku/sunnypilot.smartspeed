<script setup lang="ts">
import {computed, onBeforeUnmount, ref, shallowRef, watch} from 'vue';
import {api, messageOf, post} from '../common/api';
import {openCatalogFit} from '../common/catalog-fit';
import {plural, thousands} from '../common/format';
import {catalogAddress} from '../common/sign-link';
import {describe, familyOf, holdsPlate, signKindOf} from '../review/catalog';
import type {Box, Detection} from '../review/types';
import SectionHead from './SectionHead.vue';

interface Item {
  route: string;
  run: string;
  frame: string;
  box: Detection;
  width: number;
  height: number;
  kind: 'human' | 'ai';
  title: string | null;
  editor: string;
}
interface Catalog {
  groups: Record<string, number>;
  examples?: Record<string, Pick<Item, 'route' | 'run' | 'frame' | 'box'>>;
  items: Item[];
  page: number;
  pages: number;
}

const query = new URLSearchParams(location.search);
const group = ref(query.get('catalog')?.slice(0, 100) ?? '');
const page = ref(/^\d{1,6}$/.test(query.get('page') ?? '') ? Math.max(1, Number(query.get('page'))) : 1);
const current = shallowRef<Catalog | null>(null);
const loading = ref(false);
const error = ref('');
const fitting = shallowRef<Item | null>(null);
const fitNotice = shallowRef<{item: Item; text: string} | null>(null);
const square = ref(false);
let request = 0;
onBeforeUnmount(() => { ++request; });

function groupLabel(key: string): string {
  const [type, unit = '', value = ''] = key.split(':');
  if (type === 'speed') return unit === 'unknown' ? 'Number not readable' : value + (unit === 'mph' ? ' mph' : ' km/h');
  if (type === 'kind') return signKindOf(unit)?.label ?? 'Other sign';
  return familyOf(unit)?.label ?? unit;
}

const collator = new Intl.Collator('en', {numeric: true});
const choices = computed(() => {
  const options = Object.entries(current.value?.groups ?? {}).filter(([, count]) => count > 0)
    .map(([key, count]) => {
      const [type, unit, value] = key.split(':');
      return {key, count, label: groupLabel(key), speed: type === 'speed', mph: unit === 'mph',
        number: value ?? '?', chip: type === 'kind' ? signKindOf(unit)?.chip ?? groupLabel(key) : groupLabel(key),
        example: current.value?.examples?.[key]};
    });
  options.sort((a, b) => collator.compare(a.label, b.label));
  return [
    {label: 'Speed signs', options: options.filter(({key}) => key.startsWith('speed:'))},
    {label: 'Other signs', options: options.filter(({key}) => !key.startsWith('speed:'))},
  ];
});

async function load(): Promise<void> {
  const mine = ++request;
  const params = new URLSearchParams({group: group.value, page: String(page.value)});
  error.value = '';
  fitNotice.value = null;
  loading.value = true;
  try {
    const result = await api<Catalog>('/api/speed-review/sign-catalog?' + params);
    if (mine !== request) return;
    current.value = result;
    const selection = new URLSearchParams();
    if (group.value) selection.set('catalog', group.value);
    if (result.page > 1) selection.set('page', String(result.page));
    history.replaceState(history.state, '', catalogAddress(selection));
  } catch (problem) {
    if (mine === request) error.value = messageOf(problem);
  } finally {
    if (mine === request) loading.value = false;
  }
}
watch([group, page], load, {immediate: true});

function choose(key: string): void {
  page.value = 1;
  group.value = key;
}

function changePage(delta: number): void {
  if (!current.value) return;
  page.value = current.value.page + delta;
}

function thumbnail(item: Pick<Item, 'route' | 'run' | 'frame' | 'box'>): string {
  const args = new URLSearchParams({box: item.box.bbox_xyxy.join(','), run: item.run});
  return `/api/route/${item.route}/frame/${item.frame}/classifier-crop?` + args;
}

function edit(item: Item): string {
  const url = new URL(item.editor, location.origin);
  url.searchParams.set('frame', item.frame);
  url.searchParams.set('box', item.box.detection_id ?? '');
  url.searchParams.set('catalog', group.value);
  if (current.value && current.value.page > 1) url.searchParams.set('page', String(current.value.page));
  return url.pathname + url.search;
}

async function fit(item: Item): Promise<void> {
  const id = item.box.detection_id;
  if (!id || fitting.value) return;
  const mine = request;
  const plate = holdsPlate(item.box);
  const makeSquare = square.value && !plate;
  fitting.value = item;
  fitNotice.value = null;
  try {
    const result = await post<{box: Box | null; includes_plate?: boolean; message: string}>(
      `/api/route/${item.route}/frame/${item.frame}/ai-fit`,
      {run_id: item.run, box: item.box.bbox_xyxy, include_plate: plate});
    if (mine !== request) return;
    if (plate && !result.includes_plate) throw new Error('This server cannot yet fit the sign and its plate together. Open the crop to adjust it.');
    if (!result.box) { fitNotice.value = {item, text: result.message}; return; }
    openCatalogFit(edit(item), {route: item.route, run: item.run, frame: item.frame, id,
      before: item.box.bbox_xyxy, after: result.box, square: makeSquare});
  } catch (problem) {
    if (mine === request) fitNotice.value = {item, text: messageOf(problem)};
  } finally {
    fitting.value = null;
  }
}
</script>

<template>
  <SectionHead tab="signcatalog"/>
  <section class="card sct-card" aria-labelledby="sign-catalog-title">
    <div class="ds-head">
      <h2 id="sign-catalog-title">Sign catalog</h2>
      <span v-if="current && group && !loading" class="ds-when">{{ plural(current.groups[group] ?? 0, 'confirmed box') }}</span>
    </div>
    <div v-if="group" class="sct-actions">
      <button type="button" class="btn small" @click="choose('')">‹ All signs</button>
      <strong id="catalog-selection">{{ groupLabel(group) }}</strong>
      <span v-if="group && current?.items.length && !loading">Smallest boxes first</span>
      <label v-if="group && current?.items.length && !loading" title="Expand the proposed box to a square without cutting off the sign. Signs with a plate keep their shape.">
        <input v-model="square" type="checkbox"> Square fit
      </label>
    </div>
    <p v-if="error" class="empty" role="alert">{{ error }} <button type="button" class="link-button" @click="load">Retry</button></p>
    <p v-else-if="loading" class="empty" role="status">Loading signs…</p>
    <div v-else-if="!group" class="sign-editor sct-index">
      <p v-if="!choices.some(section => section.options.length)" class="empty">No signs have been confirmed yet.</p>
      <section v-for="section in choices.filter(s => s.options.length)" :key="section.label">
        <h3>{{ section.label }}</h3>
        <ul class="sct-labels">
          <li v-for="option in section.options" :key="option.key">
            <button type="button" class="chip recent sct-label" :data-group="option.key"
                    :aria-label="option.label + ' · ' + plural(option.count, 'confirmed box')" :title="option.label"
                    @click="choose(option.key)">
              <span v-if="option.speed" class="sign" :class="{us: option.mph, unknown: option.number === '?', wide: option.number.length > 2}">
                <template v-if="option.mph"><span class="us-top">SPEED<br>LIMIT</span><span class="us-num">{{ option.number }}</span></template>
                <template v-else>{{ option.number }}</template>
              </span>
              <template v-else><img v-if="option.example" :src="thumbnail(option.example)" loading="lazy" alt="">
                {{ option.chip }}</template>
              <span class="sct-count">{{ thousands(option.count) }}</span>
            </button>
          </li>
        </ul>
      </section>
    </div>
    <p v-else-if="!current?.items.length" class="empty">No confirmed signs with this label.</p>
    <template v-else>
      <ul class="sct-grid">
        <li v-for="item in current.items" :key="item.route + '/' + item.frame + '/' + item.box.detection_id">
          <a class="sct-tile" :href="edit(item)" data-full-navigation :title="describe(item.box)">
            <img :src="thumbnail(item)" :alt="describe(item.box)" loading="lazy" decoding="async">
            <span class="sct-caption">
              <strong>{{ item.title ?? 'Drive ' + item.route.split('--')[0] }}</strong>
              <small>{{ Math.round(item.width) }} × {{ Math.round(item.height) }} px · {{ item.frame }}<template v-if="item.kind === 'ai'"> · AI</template></small>
            </span>
            <span class="sct-chevron" aria-hidden="true">›</span>
          </a>
          <button type="button" class="btn small sct-fit" :disabled="!!fitting" @click="fit(item)"
                  title="Fit the whole sign using the surrounding image, then inspect the proposal in the editor.">
            {{ fitting === item ? 'Fitting…' : 'Fit with AI' }}
          </button>
          <small v-if="fitNotice?.item === item" class="sct-notice" role="status">{{ fitNotice.text }}</small>
        </li>
      </ul>
      <nav v-if="current.pages > 1" class="sct-pages" aria-label="Sign catalog pages">
        <button type="button" class="btn small" :disabled="current.page === 1" @click="changePage(-1)">‹ Previous</button>
        <span>Page {{ current.page }} of {{ current.pages }}</span>
        <button type="button" class="btn small" :disabled="current.page === current.pages" @click="changePage(1)">Next ›</button>
      </nav>
    </template>
  </section>
</template>
