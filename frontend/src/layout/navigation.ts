import { translate, type Locale, type MessageKey } from "../i18n/core";
import {
  ChartNoAxesCombined,
  Database,
  FlaskConical,
  Funnel,
  House,
  Activity,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export interface NavigationItem {
  label: string;
  shortLabel: string;
  path: string;
  matchPrefix: string;
  Icon: LucideIcon;
}

const NAVIGATION_DEFINITIONS: readonly (Omit<NavigationItem, "label" | "shortLabel"> & {label: MessageKey; shortLabel: MessageKey})[] = [
  {
    label: "navigation.overview",
    shortLabel: "navigation.overviewShort",
    path: "/overview",
    matchPrefix: "/overview",
    Icon: House,
  },
  {
    label: "navigation.options",
    shortLabel: "navigation.optionsShort",
    path: "/options",
    matchPrefix: "/options",
    Icon: Activity,
  },
  {
    label: "navigation.screen",
    shortLabel: "navigation.screenShort",
    path: "/screen",
    matchPrefix: "/screen",
    Icon: Funnel,
  },
  {
    label: "navigation.research",
    shortLabel: "navigation.researchShort",
    path: "/research/US.SPY",
    matchPrefix: "/research/",
    Icon: ChartNoAxesCombined,
  },
  {
    label: "navigation.backtests",
    shortLabel: "navigation.backtestsShort",
    path: "/backtests",
    matchPrefix: "/backtests",
    Icon: FlaskConical,
  },
  {
    label: "navigation.health",
    shortLabel: "navigation.healthShort",
    path: "/health",
    matchPrefix: "/health",
    Icon: Database,
  },
];

export function isNavigationItemActive(pathname: string, item: NavigationItem) {
  return pathname === item.matchPrefix || pathname.startsWith(item.matchPrefix);
}

export function getNavigationItems(locale: Locale = "zh-CN"): readonly NavigationItem[] {
 return NAVIGATION_DEFINITIONS.map(item => ({...item, label: translate(locale,item.label), shortLabel: translate(locale,item.shortLabel)}));
}
export const NAVIGATION_ITEMS = getNavigationItems();
