import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";

import { reportApiConnectivity } from "../app/connectivity";

// jsdom has no viewport scrolling; browser acceptance checks actual restoration.
window.scrollTo = () => {};

afterEach(() => {
  window.sessionStorage.clear();
  reportApiConnectivity("online");
});
