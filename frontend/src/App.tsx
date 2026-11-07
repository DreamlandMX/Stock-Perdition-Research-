import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";

import { createAppRouter } from "./app/router";
import { appQueryClient } from "./app/query-client";
import { ErrorBoundary } from "./components/ErrorBoundary";
import "./styles/global.css";

const appRouter = createAppRouter();

export function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={appQueryClient}>
        <RouterProvider router={appRouter} />
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
