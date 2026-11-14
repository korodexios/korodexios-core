import { createApp } from "vue";

import App from "./App.vue";
import { initAuth } from "./kdx";
import "./overrides";
import "./styles.css";

// Who we are is settled once, before the first request: "#token=", the embedding page, a kept
// token, or the session cookie (src/kdx/auth.ts).
void initAuth().then(() => createApp(App).mount("#app"));
