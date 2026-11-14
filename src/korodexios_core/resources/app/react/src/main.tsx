import React from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { initAuth } from "./kdx";
import "./overrides";
import "./styles.css";

// Who we are is settled once, before the first request: "#token=", the embedding page, a kept
// token, or the session cookie (src/kdx/auth.ts).
void initAuth().then(() => {
  createRoot(document.getElementById("root") as HTMLElement).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
});
