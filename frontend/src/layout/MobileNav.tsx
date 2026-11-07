import { Link, useLocation } from "react-router-dom";

import { isNavigationItemActive, NAVIGATION_ITEMS } from "./navigation";

export function MobileNav() {
  const { pathname } = useLocation();

  return (
    <nav
      aria-label="移动端主要导航"
      className="mobile-nav"
      data-layout="bottom"
      data-width="viewport"
    >
      {NAVIGATION_ITEMS.map((item) => {
        const active = isNavigationItemActive(pathname, item);

        return (
          <Link
            aria-current={active ? "page" : undefined}
            aria-label={active ? `${item.label}，当前页面` : item.label}
            className={`mobile-nav__link${active ? " mobile-nav__link--active" : ""}`}
            key={item.label}
            to={item.path}
          >
            <item.Icon aria-hidden="true" size={18} strokeWidth={1.5} />
            <span>{item.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
