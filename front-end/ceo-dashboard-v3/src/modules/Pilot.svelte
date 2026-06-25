<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { dt } from "../lib/format.js";
  let loading = true, error = null, board = [];
  onMount(async () => {
    try { board = (await api.pilot()).pilot; }
    catch (e) { error = e.message; }
    finally { loading = false; }
  });
  const stepPill = (s) => ({ Active: "ok", KYC: "warn", Payment: "warn", Suspended: "err" }[s] || "muted");
</script>

<h1>Pilot Operations</h1>
<p class="muted" style="margin-top:-8px">The pilot cohort — who's in, who's stuck, and the next blocking step.</p>
{#if loading}<div class="spinner">Loading…</div>
{:else if error}<div class="err-banner">{error}</div>
{:else if !board.length}<div class="panel placeholder">No pilot businesses yet. Mark customers as "pilot" to populate this board.</div>
{:else}
  <div class="panel" style="padding:0">
    <table>
      <thead><tr><th>Business</th><th>Owner</th><th>Status</th><th>KYC</th><th>Subscription</th><th>Blocking step</th><th>Open notes</th><th>Joined</th></tr></thead>
      <tbody>
        {#each board as b}
          <tr>
            <td>{b.display_name}</td><td>{b.owner_name || "—"}</td><td>{b.status}</td><td>{b.kyc_status}</td>
            <td>{b.subscription_status || "—"}</td>
            <td><span class="pill {stepPill(b.blocking_step)}">{b.blocking_step}</span></td>
            <td>{b.open_notes || ""}</td><td class="muted">{dt(b.created_at)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}
