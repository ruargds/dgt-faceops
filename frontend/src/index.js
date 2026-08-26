import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
// Antes do CSS e do render: o atributo data-tema tem de estar no <html>
// quando a primeira regra de cor for avaliada.
import "./tema";
import "@xterm/xterm/css/xterm.css";
import "./styles.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
