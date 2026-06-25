// Tiny Chart.js wrapper as a Svelte action: <canvas use:chart={config}>.
// Auto-creates, updates on config change, and destroys on unmount.
import {
  Chart, LineController, BarController, LineElement, BarElement, PointElement,
  CategoryScale, LinearScale, Tooltip, Legend, Filler,
} from "chart.js";

Chart.register(
  LineController, BarController, LineElement, BarElement, PointElement,
  CategoryScale, LinearScale, Tooltip, Legend, Filler
);

export function chart(node, config) {
  let c = new Chart(node, config);
  return {
    update(next) {
      c.data = next.data;
      if (next.options) c.options = next.options;
      c.update();
    },
    destroy() { c.destroy(); },
  };
}
