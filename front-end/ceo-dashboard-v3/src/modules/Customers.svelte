<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { dt } from "../lib/format.js";

  let loading = true, error = null, rows = [], q = "", filterStatus = "";
  let showCreate = false, creating = false, createErr = null;
  let form = { display_name: "", legal_structure: "osek_patur", tax_id: "", business_email: "", is_pilot: true };

  let detail = null, detailLoading = false, actionMsg = null, timeline = [];

  async function load() {
    loading = true; error = null;
    try {
      const qs = new URLSearchParams();
      if (q) qs.set("q", q);
      if (filterStatus) qs.set("status", filterStatus);
      rows = (await api.customers(qs.toString())).customers;
    } catch (e) { error = e.message; }
    finally { loading = false; }
  }
  onMount(load);

  async function create() {
    creating = true; createErr = null;
    try {
      await api.createCustomer(form);
      showCreate = false;
      form = { display_name: "", legal_structure: "osek_patur", tax_id: "", business_email: "", is_pilot: true };
      await load();
    } catch (e) { createErr = e.message; }
    finally { creating = false; }
  }

  async function open(id) {
    detailLoading = true; detail = null; actionMsg = null; timeline = [];
    try {
      detail = await api.customer(id);
      try { timeline = (await api.timeline(id)).timeline; } catch (_) { timeline = []; }
    }
    catch (e) { actionMsg = e.message; }
    finally { detailLoading = false; }
  }

  async function kycApprove() {
    if (!confirm("Approve KYC for this customer?")) return;
    try { await api.kycApprove(detail.id); actionMsg = "KYC approved."; await open(detail.id); await load(); }
    catch (e) { actionMsg = e.message; }
  }
  async function kycReject() {
    const r = prompt("Rejection reason:"); if (!r) return;
    try { await api.kycReject(detail.id, r); actionMsg = "KYC rejected."; await open(detail.id); await load(); }
    catch (e) { actionMsg = e.message; }
  }
  async function kycRequestDocs() {
    const msg = prompt("What documents do you need from the customer?"); if (!msg) return;
    try { await api.kycRequestDocs(detail.id, msg); actionMsg = "Documents requested."; await open(detail.id); }
    catch (e) { actionMsg = e.message; }
  }

  async function doSuspend() {
    if (!confirm("Suspend this customer? This halts their services.")) return;
    try { await api.suspendCustomer(detail.id); actionMsg = "Suspended."; await open(detail.id); await load(); }
    catch (e) { actionMsg = e.message; }
  }
  async function doArchive() {
    if (!confirm("Archive (soft-delete) this customer?")) return;
    try { await api.archiveCustomer(detail.id); actionMsg = "Archived."; await open(detail.id); await load(); }
    catch (e) { actionMsg = e.message; }
  }

  let noteBody = "", noteAction = "";
  async function addNote() {
    if (!noteBody.trim()) return;
    try { await api.addNote(detail.id, { body: noteBody, next_action: noteAction || null }); noteBody = ""; noteAction = ""; await open(detail.id); }
    catch (e) { actionMsg = e.message; }
  }

  const statusPill = (s) => ({ active: "ok", suspended: "warn", closed: "muted" }[s] || "muted");
  const kycPill = (s) => ({ approved: "ok", rejected: "err", under_review: "warn" }[s] || "muted");
</script>

<h1>Customers</h1>
<div class="row" style="margin-bottom:14px;gap:8px">
  <input placeholder="Search name / tax id…" bind:value={q} on:keydown={(e)=>e.key==='Enter'&&load()} style="max-width:280px" />
  <select bind:value={filterStatus} on:change={load} style="max-width:160px">
    <option value="">All statuses</option><option value="active">Active</option>
    <option value="suspended">Suspended</option><option value="closed">Closed</option>
  </select>
  <button class="btn ghost" on:click={load}>Refresh</button>
  <div style="flex:1"></div>
  <button class="btn" on:click={() => (showCreate = true)}>+ New Customer</button>
</div>

{#if loading}<div class="spinner">Loading…</div>
{:else if error}<div class="err-banner">{error}</div>
{:else}
  <div class="panel" style="padding:0">
    <table>
      <thead><tr><th>Business</th><th>Tax ID</th><th>Owner</th><th>Status</th><th>KYC</th><th>Pilot</th><th>Plan</th><th>Joined</th></tr></thead>
      <tbody>
        {#each rows as r}
          <tr style="cursor:pointer" on:click={() => open(r.id)}>
            <td>{r.display_name}</td><td>{r.tax_id}</td><td>{r.owner_name || r.owner_email || "—"}</td>
            <td><span class="pill {statusPill(r.status)}">{r.status}</span></td>
            <td><span class="pill {kycPill(r.kyc_status)}">{r.kyc_status}</span></td>
            <td>{r.is_pilot ? "✓" : ""}</td>
            <td>{r.subscription ? r.subscription.plan + " (" + r.subscription.status + ")" : "—"}</td>
            <td class="muted">{dt(r.created_at)}</td>
          </tr>
        {/each}
        {#if !rows.length}<tr><td colspan="8" class="placeholder" style="padding:24px;text-align:center">No customers yet.</td></tr>{/if}
      </tbody>
    </table>
  </div>
{/if}

{#if showCreate}
  <div class="scrim" on:click|self={() => (showCreate = false)}>
    <div class="panel modal">
      <h2 style="margin-top:0">New Customer</h2>
      <label>Business name</label><input bind:value={form.display_name} />
      <label>Legal structure</label>
      <select bind:value={form.legal_structure}>
        <option value="osek_patur">Osek Patur</option><option value="osek_morshe">Osek Morshe</option><option value="chevra_baam">Chevra Baam</option>
      </select>
      <label>Tax ID</label><input bind:value={form.tax_id} />
      <label>Business email</label><input bind:value={form.business_email} />
      <label class="row" style="margin-top:12px"><input type="checkbox" bind:checked={form.is_pilot} style="width:auto" /> &nbsp;Pilot business</label>
      {#if createErr}<div class="err-banner" style="margin-top:10px">{createErr}</div>{/if}
      <div class="row" style="justify-content:flex-end;margin-top:16px">
        <button class="btn ghost" on:click={() => (showCreate = false)}>Cancel</button>
        <button class="btn" disabled={creating || !form.display_name || !form.tax_id} on:click={create}>{creating ? "Creating…" : "Create"}</button>
      </div>
    </div>
  </div>
{/if}

{#if detail || detailLoading}
  <div class="scrim" on:click|self={() => (detail = null)}>
    <div class="panel modal">
      {#if detailLoading}<div class="spinner">Loading…</div>
      {:else}
        <div class="row" style="justify-content:space-between"><h2 style="margin:0">{detail.display_name}</h2><span class="pill {statusPill(detail.status)}">{detail.status}</span></div>
        <div class="muted" style="font-size:12px">{detail.legal_structure} · {detail.tax_id} · joined {dt(detail.created_at)}</div>
        {#if actionMsg}<div class="err-banner" style="margin-top:10px">{actionMsg}</div>{/if}

        <div class="grid cols-3" style="margin-top:14px">
          <div class="panel kpi"><div class="label">KYC</div><div class="value" style="font-size:15px"><span class="pill {kycPill(detail.kyc_status)}">{detail.kyc_status}</span></div></div>
          <div class="panel kpi"><div class="label">Blocking step</div><div class="value" style="font-size:15px">{detail.blocking_step}</div></div>
          <div class="panel kpi"><div class="label">Invoices</div><div class="value" style="font-size:15px">{detail.invoices.count}</div><div class="sub">outstanding ₪{detail.invoices.outstanding}</div></div>
        </div>

        <h2>KYC</h2>
        <div class="row" style="gap:8px;flex-wrap:wrap">
          <span class="pill {kycPill(detail.kyc_status)}">{detail.kyc_status}</span>
          {#if detail.kyc_status !== 'approved'}<button class="btn ghost" on:click={kycApprove}>Approve</button>{/if}
          {#if detail.kyc_status !== 'rejected'}<button class="btn ghost" on:click={kycReject}>Reject</button>{/if}
          <button class="btn ghost" on:click={kycRequestDocs}>Request docs</button>
        </div>
        {#if detail.kyc_documents.length}
          <div class="muted" style="font-size:12px;margin-top:6px">{detail.kyc_documents.length} doc(s): {detail.kyc_documents.map(d => d.document_type + ' (' + d.status + ')').join(', ')}</div>
        {/if}

        <h2>Owner & integrations</h2>
        <div class="muted" style="font-size:13px">
          {detail.owner.full_name || "—"} · {detail.owner.email || "—"}<br/>
          WhatsApp {detail.integrations.whatsapp_linked ? "✓" : "✗"} · Telegram {detail.integrations.telegram_linked ? "✓" : "✗"} · Accountant {detail.integrations.accountant_linked ? "✓" : "✗"}
        </div>

        <h2>Pilot notes</h2>
        {#each detail.notes as n}
          <div style="padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.05);font-size:13px">{n.body}{#if n.next_action} <span class="muted">→ {n.next_action}</span>{/if}<div class="muted" style="font-size:11px">{dt(n.created_at)}</div></div>
        {/each}
        <div class="row" style="margin-top:8px;gap:8px">
          <input placeholder="Note…" bind:value={noteBody} />
          <input placeholder="Next action…" bind:value={noteAction} style="max-width:180px" />
          <button class="btn ghost" on:click={addNote}>Add</button>
        </div>

        <h2>Timeline</h2>
        {#if !timeline.length}<div class="placeholder">No activity yet.</div>
        {:else}
          <div style="max-height:180px;overflow:auto">
            {#each timeline as t}
              <div class="row" style="justify-content:space-between;padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.05);font-size:12px">
                <span><span class="muted">[{t.kind}]</span> {t.summary}</span><span class="muted">{dt(t.at)}</span>
              </div>
            {/each}
          </div>
        {/if}

        <div class="row" style="justify-content:flex-end;margin-top:18px;gap:8px">
          <button class="btn ghost" on:click={() => (detail = null)}>Close</button>
          {#if detail.status !== "suspended"}<button class="btn ghost" on:click={doSuspend}>Suspend</button>{/if}
          {#if !detail.archived}<button class="btn danger" on:click={doArchive}>Archive</button>{/if}
        </div>
      {/if}
    </div>
  </div>
{/if}
