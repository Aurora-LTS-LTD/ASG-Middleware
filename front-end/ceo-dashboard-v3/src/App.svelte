<script>
  import Overview from "./modules/Overview.svelte";
  import Customers from "./modules/Customers.svelte";
  import Pilot from "./modules/Pilot.svelte";
  import Finance from "./modules/Finance.svelte";
  import SystemHealth from "./modules/SystemHealth.svelte";
  import Audit from "./modules/Audit.svelte";
  import CopilotPlaceholder from "./modules/CopilotPlaceholder.svelte";

  // Hash router (history API doesn't work from file:// in WKWebView).
  const routes = [
    { id: "overview", label: "Executive Overview", comp: Overview },
    { id: "customers", label: "Customers", comp: Customers },
    { id: "pilot", label: "Pilot Operations", comp: Pilot },
    { id: "finance", label: "Finance", comp: Finance },
    { id: "health", label: "System Health", comp: SystemHealth },
    { id: "audit", label: "Audit / Activity", comp: Audit },
    { id: "copilot", label: "AI Copilot", comp: CopilotPlaceholder, disabled: true, tag: "v3.4" },
  ];

  let current = (location.hash || "#overview").slice(1);
  function go(id) { location.hash = id; }
  function onHash() { current = (location.hash || "#overview").slice(1); }

  $: route = routes.find((r) => r.id === current) || routes[0];
</script>

<svelte:window on:hashchange={onHash} />

<div class="layout">
  <aside class="sidebar">
    <div class="brand">Aurora LTS<small>Command Center · v3.0</small></div>
    <nav class="nav">
      {#each routes as r}
        <button
          class:active={route.id === r.id}
          class:disabled={r.disabled}
          on:click={() => !r.disabled && go(r.id)}
          title={r.disabled ? "Planned for v3.4" : r.label}
        >
          {r.label}{#if r.tag}<span class="tag">{r.tag}</span>{/if}
        </button>
      {/each}
    </nav>
    <div class="muted" style="font-size:11px;padding:8px 10px">Thin admin shell · all actions via Admin API</div>
  </aside>

  <main class="main">
    <svelte:component this={route.comp} />
  </main>
</div>
