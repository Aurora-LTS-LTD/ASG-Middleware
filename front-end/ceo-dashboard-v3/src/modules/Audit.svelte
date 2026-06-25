<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { dt } from "../lib/format.js";
  let loading = true, error = null, events = [], fAction = "", fSeverity = "";
  async function load() {
    loading = true; error = null;
    try {
      const qs = new URLSearchParams();
      if (fAction) qs.set("action", fAction);
      if (fSeverity) qs.set("severity", fSeverity);
      events = (await api.auditEvents(qs.toString())).events;
    } catch (e) { error = e.message; }
    finally { loading = false; }
  }
  onMount(load);
  const sevPill = (s) => ({ critical: "err", warning: "warn", info: "muted" }[s] || "muted");
  let expanded = null;
</script>

<h1>Audit / Activity</h1>
<div class="row" style="margin-bottom:14px;gap:8px">
  <input placeholder="Filter by action (e.g. customer.suspend)" bind:value={fAction} on:keydown={(e)=>e.key==='Enter'&&load()} style="max-width:280px" />
  <select bind:value={fSeverity} on:change={load} style="max-width:150px">
    <option value="">All severity</option><option value="info">info</option><option value="warning">warning</option><option value="critical">critical</option>
  </select>
  <button class="btn ghost" on:click={load}>Refresh</button>
</div>

{#if loading}<div class="spinner">Loading…</div>
{:else if error}<div class="err-banner">{error}</div>
{:else}
  <div class="panel" style="padding:0">
    <table>
      <thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Entity</th><th>Severity</th></tr></thead>
      <tbody>
        {#each events as e}
          <tr style="cursor:pointer" on:click={() => (expanded = expanded === e.id ? null : e.id)}>
            <td class="muted">{dt(e.created_at)}</td>
            <td>{e.actor_role || "—"}{#if e.actor_user_id} #{e.actor_user_id}{/if}</td>
            <td>{e.action}</td>
            <td class="muted">{e.entity_type || ""}{#if e.entity_id} #{e.entity_id}{/if}</td>
            <td><span class="pill {sevPill(e.severity)}">{e.severity}</span></td>
          </tr>
          {#if expanded === e.id}
            <tr><td colspan="5" style="background:rgba(0,0,0,0.2)">
              <div class="muted" style="font-size:12px">before: <code>{JSON.stringify(e.before)}</code></div>
              <div class="muted" style="font-size:12px">after: <code>{JSON.stringify(e.after)}</code></div>
            </td></tr>
          {/if}
        {/each}
        {#if !events.length}<tr><td colspan="5" class="placeholder" style="padding:24px;text-align:center">No audit events match.</td></tr>{/if}
      </tbody>
    </table>
  </div>
{/if}
