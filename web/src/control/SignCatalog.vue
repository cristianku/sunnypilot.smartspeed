<script setup lang="ts">
import {computed, onBeforeUnmount, ref, shallowRef, watch} from 'vue';
import {api, messageOf} from '../common/api';
import CatalogQuickEdit from './CatalogQuickEdit.vue';
import {refOf, sameRef, type CatalogItem as Item, type SignRef} from './catalog-edit';
import {plural, thousands} from '../common/format';
import {catalogAddress} from '../common/sign-link';
import {describe, familyOf, signKindOf} from '../review/catalog';
import SectionHead from './SectionHead.vue';

interface Catalog {
  groups: Record<string, number>;
  examples?: Record<string, Pick<Item, 'route' | 'run' | 'frame' | 'box'>>;
  items: Item[];
  page: number;
  pages: number;
  sequence?: SignRef[];
}

const query = new URLSearchParams(location.search);
const group = ref(query.get('catalog')?.slice(0, 100) ?? '');
const page = ref(/^\d{1,6}$/.test(query.get('page') ?? '') ? Math.max(1, Number(query.get('page'))) : 1);
const current = shallowRef<Catalog | null>(null);
const loading = ref(false);
const error = ref('');
const target = shallowRef<SignRef | null>(null);
const autoFit = ref(false);
let list: SignRef[] = [], position = 0, edited = false, legacyPage = 0;
const seen = new Set<string>();
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
  const params = new URLSearchParams({group: group.value, page: String(page.value), sequence: '1'});
  error.value = '';
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

function edit(item: SignRef): string {
  const url = new URL('/route/' + item[0] + '/', location.origin);
  url.searchParams.set('frame', item[2]);
  url.searchParams.set('box', item[3]);
  url.searchParams.set('catalog', group.value);
  if (current.value && current.value.page > 1) url.searchParams.set('page', String(current.value.page));
  return url.pathname + url.search;
}

function open(item: Item, fit = false): void {
  if (!current.value) return;
  list = current.value.sequence ?? current.value.items.map(refOf);
  position = list.findIndex(r => sameRef(r, refOf(item)));
  if (position < 0) return;
  seen.clear(); list.slice(0, position).forEach(r => seen.add(JSON.stringify(r)));
  legacyPage = current.value.sequence ? 0 : current.value.page;
  autoFit.value = fit; edited = false; target.value = list[position]!;
}
function closeEditor(): void {
  target.value = null;
  if (edited) void load();
}
let advancing = false;
async function nextSign(changed: boolean): Promise<void> {
  if (!target.value || advancing) return;
  edited ||= changed;
  seen.add(JSON.stringify(target.value));
  autoFit.value = false;
  if (++position < list.length) { target.value = list[position]!; return; }
  // With an older server, read the tail of the current page again: a relabel may have pulled
  // the next sign onto this page. New servers give a stable list of IDs at the first request.
  if (legacyPage) {
    const previous = target.value;
    advancing = true;
    try {
      do {
        const query = new URLSearchParams({group: group.value, page: String(legacyPage)});
        const result = await api<Catalog>('/api/speed-review/sign-catalog?' + query);
        if (target.value !== previous) return;
        list = result.items.map(refOf).filter(r => !seen.has(JSON.stringify(r)));
        if (list.length) { position = 0; target.value = list[0]!; return; }
        if (legacyPage >= result.pages) break;
        ++legacyPage;
      } while (true);
    } catch (e) { target.value = null; error.value = messageOf(e); return; }
    finally { advancing = false; }
  }
  closeEditor();
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
          <button type="button" class="sct-tile" @click="open(item)" :title="'Quick edit: ' + describe(item.box)">
            <img :src="thumbnail(item)" :alt="describe(item.box)" loading="lazy" decoding="async">
            <span class="sct-caption">
              <strong>{{ item.title ?? 'Drive ' + item.route.split('--')[0] }}</strong>
              <small>{{ Math.round(item.width) }} × {{ Math.round(item.height) }} px · {{ item.frame }}<template v-if="item.kind === 'ai'"> · AI</template></small>
            </span>
            <span class="sct-chevron" aria-hidden="true">›</span>
          </button>
          <button type="button" class="btn small sct-fit" @click="open(item,true)">Fit with AI</button>
        </li>
      </ul>
      <nav v-if="current.pages > 1" class="sct-pages" aria-label="Sign catalog pages">
        <button type="button" class="btn small" :disabled="current.page === 1" @click="changePage(-1)">‹ Previous</button>
        <span>Page {{ current.page }} of {{ current.pages }}</span>
        <button type="button" class="btn small" :disabled="current.page === current.pages" @click="changePage(1)">Next ›</button>
      </nav>
    </template>
  </section>
  <CatalogQuickEdit v-if="target" :target="target" :drive-link="edit(target)" :start-fit="autoFit" :square-fit="square"
                    @next="nextSign" @close="closeEditor"/>
</template>
