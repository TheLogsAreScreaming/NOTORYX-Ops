import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "maplibre-gl/dist/maplibre-gl.css";
import "uplot/dist/uPlot.min.css";
import "./styles.css";
import App from "./App";

try {
  const theme = localStorage.getItem("notoryx-ops-theme");
  if (theme) document.documentElement.dataset.theme = theme;
} catch {
  /* storage blocked: follow the system theme */
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
