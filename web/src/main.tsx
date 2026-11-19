import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "maplibre-gl/dist/maplibre-gl.css";
import "uplot/dist/uPlot.min.css";
import "@fontsource-variable/sora";
import "@fontsource-variable/jetbrains-mono";
import "./styles.css";
import App from "./App";
import { restoreTheme } from "./themes";

restoreTheme();

// Lets survey mode open in the field with no signal. Browsers allow this on https or this computer only.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      /* not a secure page: the dashboard still works, just not offline */
    });
  });
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
