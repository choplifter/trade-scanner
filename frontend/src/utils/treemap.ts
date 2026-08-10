/** Squarified treemap layout (Bruls, Huizing & van Wijk, 2000) -- lays out
 * items as area-proportional rectangles that stay close to square, instead
 * of the long thin slivers a naive slice-and-dice layout produces. Pure
 * layout math, no DOM/React -- reused as-is regardless of what's rendered
 * inside each rect. */

export interface TreemapRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

function worstRatio(row: number[], length: number): number {
  const sum = row.reduce((a, b) => a + b, 0);
  const max = Math.max(...row);
  const min = Math.min(...row);
  const lengthSq = length * length;
  const sumSq = sum * sum;
  return Math.max((lengthSq * max) / sumSq, sumSq / (lengthSq * min));
}

function layoutRow(
  row: number[],
  rect: TreemapRect,
  horizontal: boolean,
): { rects: TreemapRect[]; rect: TreemapRect } {
  const sum = row.reduce((a, b) => a + b, 0);
  const rects: TreemapRect[] = [];

  if (horizontal) {
    // Row fills a strip the full width of `rect`, from the top.
    const rowHeight = sum / rect.w;
    let x = rect.x;
    for (const value of row) {
      const w = value / rowHeight;
      rects.push({ x, y: rect.y, w, h: rowHeight });
      x += w;
    }
    return { rects, rect: { x: rect.x, y: rect.y + rowHeight, w: rect.w, h: rect.h - rowHeight } };
  }

  // Row fills a strip the full height of `rect`, from the left.
  const rowWidth = sum / rect.h;
  let y = rect.y;
  for (const value of row) {
    const h = value / rowWidth;
    rects.push({ x: rect.x, y, w: rowWidth, h });
    y += h;
  }
  return { rects, rect: { x: rect.x + rowWidth, y: rect.y, w: rect.w - rowWidth, h: rect.h } };
}

/** `values` must already be scaled so they sum to `rect.w * rect.h` --
 * see computeTreemap, which handles that scaling for real data. */
function squarify(values: number[], rect: TreemapRect): TreemapRect[] {
  const result: TreemapRect[] = [];
  let remaining = values;
  let currentRect = rect;
  let row: number[] = [];

  while (remaining.length > 0) {
    const horizontal = currentRect.w >= currentRect.h;
    const length = horizontal ? currentRect.w : currentRect.h;
    const next = remaining[0];
    const rowWithNext = [...row, next];

    if (row.length === 0 || worstRatio(row, length) >= worstRatio(rowWithNext, length)) {
      row = rowWithNext;
      remaining = remaining.slice(1);
    } else {
      const laid = layoutRow(row, currentRect, horizontal);
      result.push(...laid.rects);
      currentRect = laid.rect;
      row = [];
    }
  }
  if (row.length > 0) {
    const horizontal = currentRect.w >= currentRect.h;
    result.push(...layoutRow(row, currentRect, horizontal).rects);
  }
  return result;
}

/** Lays out `items` (by `value`, e.g. dollar volume) into a `width` x
 * `height` treemap. Non-positive values are dropped -- a treemap tile with
 * zero or negative area is meaningless. Returns items paired with their
 * pixel rect, largest first. */
export function computeTreemap<T extends { value: number }>(
  items: T[],
  width: number,
  height: number,
): (T & TreemapRect)[] {
  const positive = items.filter((item) => item.value > 0);
  if (positive.length === 0 || width <= 0 || height <= 0) return [];

  const totalValue = positive.reduce((sum, item) => sum + item.value, 0);
  const scale = (width * height) / totalValue;
  const sorted = [...positive].sort((a, b) => b.value - a.value);
  const scaledValues = sorted.map((item) => item.value * scale);
  const rects = squarify(scaledValues, { x: 0, y: 0, w: width, h: height });

  return sorted.map((item, i) => ({ ...item, ...rects[i] }));
}
