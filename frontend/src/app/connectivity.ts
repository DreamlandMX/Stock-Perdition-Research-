import { useSyncExternalStore } from "react";

export type Connectivity = "online" | "offline";

let apiConnectivity: Connectivity = "online";
const apiListeners = new Set<() => void>();

export function reportApiConnectivity(connectivity: Connectivity) {
  if (apiConnectivity === connectivity) return;
  apiConnectivity = connectivity;
  apiListeners.forEach((listener) => listener());
}

function currentConnectivity(): Connectivity {
  if (typeof navigator === "undefined") return "online";
  return navigator.onLine === false || apiConnectivity === "offline"
    ? "offline"
    : "online";
}

function subscribe(listener: () => void) {
  if (typeof window === "undefined") return () => undefined;
  window.addEventListener("online", listener);
  window.addEventListener("offline", listener);
  apiListeners.add(listener);
  return () => {
    window.removeEventListener("online", listener);
    window.removeEventListener("offline", listener);
    apiListeners.delete(listener);
  };
}

export function useConnectivity() {
  return useSyncExternalStore(subscribe, currentConnectivity, () => "online");
}
