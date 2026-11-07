import type { createBrowserRouter } from "react-router-dom";

export function installAssetRecovery(
  router: Pick<ReturnType<typeof createBrowserRouter>, "state">,
): void {
  const entry = document.querySelector<HTMLScriptElement>(
    'script[type="module"][src]',
  );
  if (!entry) return;
  const build = entry.src;
  window.addEventListener("vite:preloadError", () => {
    try {
      // Persist before reloading so a broken build cannot cause a reload loop.
      if (window.sessionStorage.getItem("asset-recovery-build") === build) return;
      window.sessionStorage.setItem("asset-recovery-build", build);
    } catch {
      return;
    }
    const destination = router.state.navigation.location;
    if (destination) {
      window.location.replace(
        destination.pathname + destination.search + destination.hash,
      );
    } else {
      window.location.reload();
    }
  });
}
