<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { ils } from "../lib/format.js";
  import { chart } from "../lib/charts.js";
  let loading = true, error = null, f = null;
  onMount(async () => {
    try { f = await api.financeSummary(); }
    catch (e) { error = e.message; }
    finally { loading = false; }
  });
  $: cfg = f && {
    type: "bar",
    data: {
      labels: ["Revenue", "Expenses", "Profit"],
      datasets: [{ data: [f.revenue_this_month, f.expenses_this_month, f.profit_this_month],
        backgroundColor: ["#34d399", "#f87171", "#7c3aed"], borderRadius: 6 }],
    },
    options: { plugins: { legend: { display: false } }, scales: { x: { ticks: { color: "#8a90a6" } }, y: { ticks: { color: "#8a90a6" }, grid: { color: "rgba(255,255,255,0.05)" } } } },
  };
</script>

<h1>Finance</h1>
{#if loading}<div class="spinner">Loading…</div>
{:else if error}<div class="err-banner">{error}</div>
{:else}
  <div class="grid cols-4">
    <div class="panel kpi"><div class="label">Revenue (this month)</div><div class="value">{ils(f.revenue_this_month)}</div></div>
    <div class="panel kpi"><div class="label">Expenses (this month)</div><div class="value">{ils(f.expenses_this_month)}</div></div>
    <div class="panel kpi"><div class="label">Profit (this month)</div><div class="value">{ils(f.profit_this_month)}</div></div>
    <div class="panel kpi"><div class="label">MRR / ARR</div><div class="value">{ils(f.mrr)}</div><div class="sub">ARR {ils(f.arr)}</div></div>
  </div>
  <div class="panel" style="margin-top:16px">
    <h2 style="margin-top:0">This month</h2>
    <canvas use:chart={cfg} height="120"></canvas>
    {#if f.data_thin}<div class="placeholder" style="margin-top:10px">Early-stage data. Monthly trends, runway and forecasting land in v3.2 (Finance &amp; Growth).</div>{/if}
  </div>
{/if}
