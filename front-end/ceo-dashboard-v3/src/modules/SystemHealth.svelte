<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  let loading = true, error = null, health = null, config = null;
  onMount(async () => {
    try { [health, config] = await Promise.all([api.systemHealth(), api.systemConfig()]); }
    catch (e) { error = e.message; }
    finally { loading = false; }
  });
  const statusPill = (s) => ({ ok: "ok", simulated: "warn", error: "err" }[s] || "muted");
  const readyPill = (r) => ({ production: "ok", sandbox: "warn", mock: "warn", stub: "muted" }[r] || "muted");
</script>

<h1>System Health / Production Readiness</h1>
{#if loading}<div class="spinner">Loading…</div>
{:else if error}<div class="err-banner">{error}</div>
{:else}
  <div class="panel" style="margin-bottom:16px">
    <div class="row" style="justify-content:space-between">
      <h2 style="margin:0">Overall: <span class="pill {health.overall === 'ok' ? 'ok' : 'warn'}">{health.overall}</span></h2>
      <span class="muted" style="font-size:12px">{config.version} · rev {config.cloud_run_revision || "local"} · {config.runtime}</span>
    </div>
  </div>

  <div class="panel" style="padding:0">
    <table>
      <thead><tr><th>Service</th><th>Status</th><th>Mode</th><th>Readiness</th></tr></thead>
      <tbody>
        {#each health.services as s}
          <tr>
            <td>{s.label}</td>
            <td><span class="pill {statusPill(s.status)}">{s.status}</span></td>
            <td class="muted">{s.mode}</td>
            <td>{#if s.readiness}<span class="pill {readyPill(s.readiness)}">{s.readiness}</span>{/if}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  <h2>Production-readiness flags</h2>
  <div class="panel">
    <div class="grid cols-3">
      {#each Object.entries(config.flags) as [k, v]}
        <div class="row" style="justify-content:space-between"><span class="muted">{k}</span><span class="pill {readyPill(v === 'production' || v === 'gcs' || v === 'gcp' || v === 'bigquery' ? 'production' : v)}">{v}</span></div>
      {/each}
    </div>
    <div class="muted" style="font-size:11px;margin-top:12px">Step-up enforced: {config.step_up_enforced ? "yes" : "no"} · Kill switch: {config.autonomous_kill_switch ? "ON" : "off"} · (no secrets shown)</div>
  </div>
{/if}
