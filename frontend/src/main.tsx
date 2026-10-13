import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { UpdateBanner } from "./UpdateBanner";
import "./styles.css";
import "./responsive.css";
import { installTapHint } from "./tapHint";
import { watchStockLabels } from "./WarehouseColumns";

installTapHint();
watchStockLabels();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <UpdateBanner />
    <App />
  </StrictMode>,
);
