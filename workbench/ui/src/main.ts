/** Entry point. Loaded as an ES module from the loopback adapter (`/app/main.js`). */
import { App } from "./app.js";

const root = document.getElementById("app");
const live = document.getElementById("live");
if (root && live) {
  void new App(root, live).start();
}
