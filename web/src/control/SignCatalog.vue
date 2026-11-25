<script setup lang="ts">
import {computed, onBeforeUnmount, ref, shallowRef, watch} from 'vue';
import {api, messageOf} from '../common/api';
import {plural, thousands} from '../common/format';
import {describe, familyOf, signKindOf} from '../review/catalog';
import type {Detection} from '../review/types';
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
  const counts = {...current.value?.groups};
  // Keep the chosen value if the last matching box was just corrected.
  if (group.value && counts[group.value] === undefined) counts[group.value] = 0;
  const options = Object.entries(counts).map(([key, count]) => ({key, count, label: groupLabel(key)}));
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
  loading.value = true;
  try {
    const result = await api<Catalog>('/api/speed-review/sign-catalog?' + params);
    if (mine !== request) return;
    current.value = result;
    const url = new URL(location.href);
    if (group.value) url.searchParams.set('catalog', group.value);
    else url.searchParams.delete('catalog');
    if (result.page > 1) url.searchParams.set('page', String(result.page));
    else url.searchParams.delete('page');
    history.replaceState(history.state, '', url.pathname + url.search);
  } catch (problem) {
    if (mine === request) error.value = messageOf(problem);
  } finally {
    if (mine === request) loading.value = false;
  }
}
watch([group, page], load, {immediate: true});

function choose(event: Event): void {
  page.value = 1;
  group.value = (event.target as HTMLSelectElement).value;
}

function changePage(delta: number): void {
  if (!current.value) return;
  page.value = current.value.page + delta;
}

function thumbnail(item: Item): string {
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
</script>

<template>
  <SectionHead tab="signcatalog"/>
  <section class="card sct-card" aria-labelledby="sign-catalog-title">
    <div class="ds-head">
      <h2 id="sign-catalog-title">Sign catalog</h2>
      <span v-if="current && group && !loading" class="ds-when">{{ plural(current.groups[group] ?? 0, 'confirmed box') }}</span>
    </div>
    <div class="sct-actions">
      <label for="sign-catalog-group">Label</label>
      <select id="sign-catalog-group" :value="group" @change="choose">
        <option value="">Choose a sign</option>
        <optgroup v-for="section in choices.filter((entry) => entry.options.length)" :key="section.label" :label="section.label">
          <option v-for="option in section.options" :key="option.key" :value="option.key">{{ option.label }} · {{ thousands(option.count) }}</option>
        </optgroup>
      </select>
      <span v-if="group && current?.items.length && !loading">Smallest boxes first</span>
    </div>
    <p v-if="error" class="empty" role="alert">{{ error }} <button type="button" class="link-button" @click="load">Retry</button></p>
    <p v-else-if="loading" class="empty" role="status">Loading signs…</p>
    <p v-else-if="!group" class="empty">Choose a label to see its confirmed signs across all drives.</p>
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
