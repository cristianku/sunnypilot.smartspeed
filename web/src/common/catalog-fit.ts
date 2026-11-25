import type {Box} from '../review/types';

/** One proposal in this browser tab, handed to the normal frame editor for inspection and Confirm. */
export interface CatalogFit {
  route: string; run: string; frame: string; id: string; before: Box; after: Box; square: boolean;
}
const key = 'nnslr-catalog-fit';
export function openCatalogFit(editor: string, fit: CatalogFit): void {
  sessionStorage.setItem(key, JSON.stringify(fit));
  location.assign(editor + '#review-fit');
}
export function takeCatalogFit(): CatalogFit | null {
  if (location.hash !== '#review-fit') return null;
  history.replaceState(history.state, '', location.pathname + location.search);
  const text = sessionStorage.getItem(key);
  sessionStorage.removeItem(key);
  return text ? JSON.parse(text) as CatalogFit : null;
}
