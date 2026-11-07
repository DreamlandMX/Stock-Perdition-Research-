import { translate } from "../i18n/core";
import { useI18n } from "../i18n/context";
import { createBrowserRouter, redirect, ScrollRestoration } from "react-router-dom";
import type { RouteObject } from "react-router-dom";
import { AppShell } from "../layout/AppShell";
import { RouteErrorBoundary } from "./RouteErrorState";
import { installAssetRecovery } from "./asset-recovery";
async function loadOverviewRoute() {
    const { OverviewRouteOutlet } = await import("./RouteOutlets");
    return { Component: OverviewRouteOutlet };
}
async function loadScreenRoute() {
    const { ScreenRouteOutlet } = await import("./ScreenRouteOutlet");
    return { Component: ScreenRouteOutlet };
}
async function loadOptionsRoute() {
    const { OptionsRouteOutlet } = await import("./OptionsRouteOutlet");
    return { Component: OptionsRouteOutlet };
}
async function loadResearchRoute() {
    const { ResearchRouteOutlet } = await import("./ResearchRouteOutlet");
    return { Component: ResearchRouteOutlet };
}
async function loadBacktestsRoute() {
    const { BacktestsRouteOutlet } = await import("./BacktestsRouteOutlet");
    return { Component: BacktestsRouteOutlet };
}
async function loadHealthRoute() {
    const { HealthRouteOutlet } = await import("./HealthRouteOutlet");
    return { Component: HealthRouteOutlet };
}
function InitialRouteFallback() {
    const { locale } = useI18n();
    return (<div aria-label={translate(locale, "common.shared.loadingResearchPage")} className="initial-route-fallback" role="status">{" " + (translate(locale, "common.shared.loadingResearchPage2") + " ")}</div>);
}
function AppRoot() {
    return (<>
      <AppShell />
      <ScrollRestoration />
    </>);
}
export const APP_ROUTES: RouteObject[] = [
    {
        path: "/",
        Component: AppRoot,
        ErrorBoundary: RouteErrorBoundary,
        HydrateFallback: InitialRouteFallback,
        children: [
            { index: true, loader: () => redirect("/overview") },
            { path: "overview", lazy: loadOverviewRoute },
            { path: "options", lazy: loadOptionsRoute },
            { path: "screen", lazy: loadScreenRoute },
            { path: "research/:symbol", lazy: loadResearchRoute },
            { path: "backtests", lazy: loadBacktestsRoute },
            { path: "health", lazy: loadHealthRoute },
        ],
    },
];
export function createAppRouter() {
    const router = createBrowserRouter(APP_ROUTES);
    installAssetRecovery(router);
    return router;
}
