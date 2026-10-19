import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { HashRouter } from "react-router-dom"; // rutas con # : funciona en cualquier hosting estático (§8.3)
import { queryClient } from "./api/client";
import App from "./App";
import "@fontsource-variable/bodoni-moda/opsz.css";   // tipografías propias (OFL-1.1): la web no depende de Google Fonts
import "@fontsource/atkinson-hyperlegible/400.css";
import "@fontsource/atkinson-hyperlegible/700.css";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <HashRouter>
        <App />
      </HashRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
