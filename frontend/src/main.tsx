import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { applyDocumentLocale, I18nProvider, readLocale } from "./i18n/context";

applyDocumentLocale(readLocale());

const rootElement = document.querySelector<HTMLDivElement>("#root");

if (rootElement === null) {
  throw new Error("root_element_missing");
}

createRoot(rootElement).render(
  <StrictMode>
    <I18nProvider><App /></I18nProvider>
  </StrictMode>,
);
