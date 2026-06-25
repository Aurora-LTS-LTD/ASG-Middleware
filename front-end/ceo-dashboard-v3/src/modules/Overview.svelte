<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { ils } from "../lib/format.js";
  import { chart } from "../lib/charts.js";

  let loading = true, error = null, d = null;

  onMount(async () => {
    try { d = await api.overview(); }
    catch (e) { error = e.message; }
    finally { loading = false; }
  });

  $: c = d && d.customers;
  $: f = d && d.finance;
  $: segConfig = c && {
    type: "bar",
    data: {
      labels: ["Total", "Active", "Pilot", "Paying", "Suspended"],
      datasets: [{
        data: [c.total, c.active, c.pilot, c.paying, c.suspended],
        backgroundColor: ["#7c3aed", "#34d399", "#14b8a6", "#60a5fa", "#f87171"],
        borderRadius: 6,
      }],
    },
    options: { plugins: { legend: { display: false } }, scales: { x: { ticks: { color: "#8a90a6" } }, y: { ticks: { color: "#8a90a6" }, grid: { color: "rgba(255,255,255,0.05)" } } } },
  };

  const sevColor = { critical: "err", warning: "warn", info: "muted" };
</script>

<h1>Executive Overview</h1>
{#if loading}<div class="spinner">Loading…</div>
{:else if error}<div class="err-banner">{error}</div>
{:else}
  <div class="grid cols-4">
    <div class="panel kpi"><div class="label">Total Customers</div><div class="value">{c.total}</div><div class="sub">{c.new_this_month} new this month</div></div>
    <div class="panel kpi"><div class="label">Active</div><div class="value">{c.active}</div></div>
    <div class="panel kpi"><div class="label">Pilot</div><div class="value">{c.pilot}</div></div>
    <div class="panel kpi"><div class="label">Paying</div><div class="value">{c.paying}</div><div class="sub">{c.suspended} suspended</div></div>
  </div>

  <div class="grid cols-4" style="margin-top:16px">
    <div class="panel kpi"><div class="label">Revenue (mo)</div><div class="value">{ils(f.revenue_this_month)}</div></div>
    <div class="panel kpi"><div class="label">Expenses (mo)</div><div class="value">{ils(f.expenses_this_month)}</div></div>
    <div class="panel kpi"><div class="label">Profit (mo)</div><div class="value">{ils(f.profit_this_month)}</div></div>
    <div class="panel kpi"><div class="label">MRR / ARR</div><div class="value">{ils(f.mrr)}</div><div class="sub">ARR {ils(f.arr)}{#if f.data_thin} · early data{/if}</div></div>
  </div>

  <div class="grid cols-2" style="margin-top:16px">
    <div class="panel">
      <h2 style="margin-top:0">Customer mix</h2>
      <canvas use:chart={segConfig} height="150"></canvas>
    </div>
    <div class="panel">
      <h2 style="margin-top:0">Critical alerts</h2>
      {#if !d.alerts.length}<div class="placeholder">No critical alerts.</div>
      {:else}
        {#each d.alerts as a}
          <div class="row" style="justify-content:space-between;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.05)">
            <span><span class="pill {sevColor[a.severity] || 'muted'}">{a.severity}</span> {a.title}</span>
          </div>
        {/each}
      {/if}
    </div>
  </div>
{/if}
