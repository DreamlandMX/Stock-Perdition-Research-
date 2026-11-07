import { translate } from "../i18n/core";
import { useI18n } from "../i18n/context";
import { Link, useLocation } from "react-router-dom";
import { isNavigationItemActive, getNavigationItems } from "./navigation";
import { SNAPSHOT_IDENTITY_KEYS } from "../app/snapshot-refresh";
export interface SidebarProps {
    variant: "sidebar" | "rail";
}
function useSnapshotSearch() {
    const { pathname, search } = useLocation();
    const sourceParams = new URLSearchParams(search);
    const snapshotParams = new URLSearchParams();
    for (const key of SNAPSHOT_IDENTITY_KEYS) {
        const value = sourceParams.get(key);
        if (value !== null)
            snapshotParams.set(key, value);
    }
    return { pathname, snapshotSearch: snapshotParams.toString() };
}
export function TopNavigation() {
    const { locale } = useI18n();
    const { pathname, snapshotSearch } = useSnapshotSearch();
    return (<nav aria-label={translate(locale, "common.shared.primaryNavigation")} className="primary-navigation primary-navigation--top" data-layout="top">
      {getNavigationItems(locale).map((item) => {
            const active = isNavigationItemActive(pathname, item);
            return (<Link aria-current={active ? "page" : undefined} aria-label={active ? translate(locale, "common.shared.currentPage", { label: String(item.label) }) : item.label} className={`top-nav-link${active ? " top-nav-link--active" : ""}`} key={item.path} to={`${item.path}${snapshotSearch ? `?${snapshotSearch}` : ""}`}>
            <item.Icon aria-hidden="true" size={16} strokeWidth={1.75}/>
            <span>{item.label}</span>
          </Link>);
        })}
    </nav>);
}
export function Sidebar({ variant }: SidebarProps) {
    const { locale } = useI18n();
    const { pathname, snapshotSearch } = useSnapshotSearch();
    const width = variant === "sidebar" ? 152 : 64;
    return (<aside className={`sidebar sidebar--${variant}`}>
      <div className="product-mark" aria-label="Signal Lab">
        <span aria-hidden="true">{variant === "sidebar" ? "Signal Lab" : "SL"}</span>
      </div>
      <nav aria-label={translate(locale, "common.shared.primaryNavigation")} className="primary-navigation" data-layout={variant} data-width={width}>
        {getNavigationItems(locale).map((item) => {
            const active = isNavigationItemActive(pathname, item);
            const label = variant === "sidebar" ? item.label : active ? item.shortLabel : "";
            const accessibleName = active ? translate(locale, "common.shared.currentPage", { label: String(item.label) }) : item.label;
            return (<Link aria-current={active ? "page" : undefined} aria-label={accessibleName} className={`nav-link${active ? " nav-link--active" : ""}`} key={item.path} to={`${item.path}${snapshotSearch ? `?${snapshotSearch}` : ""}`}>
              <item.Icon aria-hidden="true" size={20} strokeWidth={1.5}/>
              {label === "" ? null : <span>{label}</span>}
            </Link>);
        })}
      </nav>
    </aside>);
}
