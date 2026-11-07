import {
  anomalyScore,
  marketReturnValue,
  type MarketPoint,
  type MarketReturnHorizon,
} from "./overview-terminal";

export const REPRESENTATIVE_COUNT = 36;
const BASE_SLOTS_PER_SECTOR = 4;
const ROWS_PER_BLOCK = 2;
const TARGET_LINES = 3;

export interface SectorGroup {
  points: MarketPoint[];
  sector: string;
  total: number;
}

export interface PackedBlock {
  column: number;
  group: SectorGroup;
  line: number;
  span: number;
}

export interface PackedMap {
  blocks: PackedBlock[];
  columns: number;
  lines: number;
  meta: { column: number; line: number; span: number };
}

function rankWithinSector(
  points: readonly MarketPoint[],
  horizon: MarketReturnHorizon,
  atrCutoff: number | null,
) {
  return [...points].sort(
    (left, right) =>
      anomalyScore(right, atrCutoff) - anomalyScore(left, atrCutoff) ||
      Math.abs(marketReturnValue(right, horizon) ?? 0) -
        Math.abs(marketReturnValue(left, horizon) ?? 0) ||
      left.symbol.localeCompare(right.symbol),
  );
}

export function groupBySector(points: readonly MarketPoint[]) {
  const groups = new Map<string, MarketPoint[]>();
  for (const point of points) {
    const members = groups.get(point.sector) ?? [];
    members.push(point);
    groups.set(point.sector, members);
  }
  return groups;
}

export function packSectorRows(groups: readonly SectorGroup[]): PackedMap {
  const ordered = groups
    .flatMap((group) =>
      Array.from({ length: Math.ceil(group.points.length / 9) }, (_, i) => ({
        ...group,
        points: group.points.slice(i * 9, (i + 1) * 9),
      })),
    )
    .sort((a, b) => b.total - a.total || a.sector.localeCompare(b.sector));
  const blocks: PackedBlock[] = [],
    used = new Set<SectorGroup>();
  let line = 0,
    lastWidth = 0;
  for (const group of ordered) {
    if (used.has(group)) continue;
    let width = group.points.length;
    blocks.push({ group, column: 1, line, span: width });
    used.add(group);
    for (const other of ordered) {
      if (used.has(other) || width + other.points.length > 9) continue;
      blocks.push({ group: other, column: width + 1, line, span: other.points.length });
      width += other.points.length;
      used.add(other);
    }
    lastWidth = width;
    line++;
  }
  return {
    columns: 9,
    lines: line,
    blocks,
    meta: {
      line: Math.max(0, line - 1),
      column: lastWidth + 1,
      span: Math.max(0, 9 - lastWidth),
    },
  };
}

/**
 * Every sector first receives up to four slots; slots a small sector cannot
 * use are handed out one at a time to the sectors with the most members left.
 */
export function selectRepresentatives(
  points: readonly MarketPoint[],
  horizon: MarketReturnHorizon,
  atrCutoff: number | null,
  count = REPRESENTATIVE_COUNT,
): SectorGroup[] {
  const groups = [...groupBySector(points).entries()].map(([sector, members]) => ({
    members: rankWithinSector(members, horizon, atrCutoff),
    quota: Math.min(BASE_SLOTS_PER_SECTOR, members.length),
    sector,
  }));
  let remaining = Math.min(count, points.length);
  remaining -= groups.reduce((sum, group) => sum + group.quota, 0);
  if (remaining < 0) {
    const ordered = [...groups].sort(
      (left, right) => left.members.length - right.members.length,
    );
    while (remaining < 0) {
      const target = ordered.find((group) => group.quota > 1) ?? ordered[0];
      if (target === undefined) break;
      target.quota -= 1;
      remaining += 1;
    }
  }
  while (remaining > 0) {
    const candidates = groups
      .filter((group) => group.members.length > group.quota)
      .sort(
        (left, right) =>
          right.members.length - right.quota - (left.members.length - left.quota) ||
          left.sector.localeCompare(right.sector),
      );
    if (candidates.length === 0) break;
    for (const group of candidates) {
      if (remaining === 0) break;
      group.quota += 1;
      remaining -= 1;
    }
  }
  return groups
    .filter((group) => group.quota > 0)
    .map((group) => ({
      points: group.members.slice(0, group.quota),
      sector: group.sector,
      total: group.members.length,
    }));
}

export function blockSpan(group: SectorGroup) {
  return Math.max(1, Math.ceil(group.points.length / ROWS_PER_BLOCK));
}

/**
 * Packs sector blocks onto one shared column grid so every cell on the map has
 * the same width and height. The legend tile takes whatever its line leaves.
 */
export function packSectorBlocks(groups: readonly SectorGroup[]): PackedMap {
  const items = [
    ...groups.map((group) => ({ group, span: blockSpan(group) })),
    { group: null, span: 1 },
  ];
  const totalSpan = items.reduce((sum, item) => sum + item.span, 0);
  const widest = Math.max(...items.map((item) => item.span));
  const columns = Math.max(widest, Math.ceil(totalSpan / TARGET_LINES));
  const ordered = [...items].sort(
    (left, right) =>
      right.span - left.span ||
      (left.group === null ? 1 : right.group === null ? -1 : 0) ||
      (right.group?.total ?? 0) - (left.group?.total ?? 0),
  );
  const used: number[] = [];
  const blocks: PackedBlock[] = [];
  let meta: PackedMap["meta"] = { column: 1, line: 0, span: 1 };
  for (const item of ordered) {
    let line = used.findIndex((width) => width + item.span <= columns);
    if (line === -1) {
      line = used.length;
      used.push(0);
    }
    const column = (used[line] ?? 0) + 1;
    used[line] = (used[line] ?? 0) + item.span;
    if (item.group === null) {
      meta = { column, line, span: item.span };
    } else {
      blocks.push({ column, group: item.group, line, span: item.span });
    }
  }
  for (const block of blocks) {
    if (block.line === meta.line && block.column > meta.column) {
      block.column -= meta.span;
    }
  }
  const span = meta.span + columns - (used[meta.line] ?? columns);
  meta = { column: columns - span + 1, line: meta.line, span };
  return { blocks, columns, lines: used.length, meta };
}
