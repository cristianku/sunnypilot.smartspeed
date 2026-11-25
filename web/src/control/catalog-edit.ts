import {api} from '../common/api';
import type {Box, Detection, FrameRow, ReviewsAnswer, ServerReview} from '../review/types';

export interface CatalogItem {
  route: string; run: string; frame: string; box: Detection; width: number; height: number;
  kind: 'human' | 'ai'; title: string | null; editor: string;
}
export type SignRef = [route: string, run: string, frame: string, id: string];
export const refOf = (i: CatalogItem): SignRef => [i.route, i.run, i.frame, i.box.detection_id!];
export const sameRef = (a: SignRef, b: SignRef): boolean => a.every((v, i) => b[i] === v);
type Picture = Pick<FrameRow, 'width' | 'height' | 'preview_path' | 'frame_key'>;
let legacyRows: {route: string; run: string; frames: Map<string, Picture>; reviews: Map<string, ServerReview>} | null = null;
export function forgetLegacyDetail(): void { legacyRows = null; }
export function updateLegacyReview(review: ServerReview): void {
  if (legacyRows?.route === review.route_id && legacyRows.run === review.run_id) legacyRows.reviews.set(review.frame_key, review);
}

export async function detail([route, run, frame, id]: SignRef): Promise<{review: ServerReview; picture: Picture; box: Detection}> {
  const base = '/api/route/' + route;
  const cached = legacyRows?.route === route && legacyRows.run === run ? legacyRows : null;
  const cachedReview = cached?.reviews.get(frame);
  const result = cached && cachedReview ? {run_id: run, reviews: [cachedReview], frame: cached.frames.get(frame)}
    : await api<Pick<ReviewsAnswer, 'run_id' | 'reviews'> & {frame?: Picture}>(base + '/reviews?frame=' + encodeURIComponent(frame));
  const review = result.reviews.find(r => r.frame_key === frame);
  const box = review?.detections.find(d => d.detection_id === id);
  if (result.run_id !== run || !review?.confirmed || review.needs_person || !box) throw new Error('This sign is no longer in the same confirmed report.');
  let picture = result.frame;
  // A server still serving the previous release returns all reviews without the frame dimensions.
  if (!picture) {
    if (legacyRows?.route !== route || legacyRows.run !== run) {
      const data = await api<{run_id: string; frames: Picture[]}>(base + '/page');
      if (data.run_id !== run) throw new Error('The report changed. Reopen the catalog.');
      legacyRows = {route, run, reviews: new Map(result.reviews.map(r => [r.frame_key, r])),
        frames: new Map(data.frames.map(({frame_key, width, height, preview_path}) => [frame_key,{frame_key,width,height,preview_path}]))};
    }
    picture = legacyRows.frames.get(frame);
  }
  if (!picture) throw new Error('The sign picture is no longer available.');
  return {review, box, picture};
}

export function squareBox(box: Box, w: number, h: number): Box {
  const [x, y, right, bottom] = box;
  const side = Math.max(right - x, bottom - y);
  if (side > Math.min(w, h)) throw new Error('A square containing this sign will not fit in the frame.');
  const left = Math.max(0, Math.min(w - side, (x + right - side) / 2));
  const top = Math.max(0, Math.min(h - side, (y + bottom - side) / 2));
  return [left, top, left + side, top + side];
}
