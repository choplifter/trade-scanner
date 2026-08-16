import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
// react-grid-layout's own placeholder/handle styles, imported before
// styles.css so the token overrides at the end of that file win.
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
