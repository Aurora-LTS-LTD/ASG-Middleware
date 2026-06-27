<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { dt } from "../lib/format.js";

  let loading = true, error = null, rows = [], fStatus = "";
  let showCreate = false, creating = false, createErr = null;
  let form = { subject: "", body: "", priority: "normal", category: "technical" };
  let detail = null, detailLoading = false, actionMsg = null, msgBody = "";

  async function load() {
    loading = true; error = null;
    try {
      const qs = new URLSearchParams();
      if (fStatus) qs.set("status", fStatus);
      rows = (await api.tickets(qs.toString())).tickets;
    } catch (e) { error = e.message; } finally { loading = false; }
  }
  onMount(load);

  async function create() {
    creating = true; createErr = null;
    try { await api.createTicket(form); showCreate = false; form = { subject:"", body:"", priority:"normal", category:"technical" }; await load(); }
    catch (e) { createErr = e.message; } finally { creating = false; }
  }
  async function open(id) {
    detailLoading = true; detail = null; actionMsg = null;
    try { detail = await api.ticket(id); } catch (e) { actionMsg = e.message; } finally { detailLoading = false; }
  }
  async function setStatus(s) {
    try { await api.editTicket(detail.id, { status: s }); await open(detail.id); await load(); }
    catch (e) { actionMsg = e.message; }
  }
  async function setPriority(p) {
    try { await api.editTicket(detail.id, { priority: p }); await open(detail.id); await load(); }
    catch (e) { actionMsg = e.message; }
  }
  async function addMsg() {
    if (!msgBody.trim()) return;
    try { await api.addTicketMessage(detail.id, { body: msgBody, is_internal: true }); msgBody = ""; await open(detail.id); }
    catch (e) { actionMsg = e.message; }
  }

  const stPill = (s) => ({ open:"warn", in_progress:"warn", waiting:"muted", resolved:"ok", closed:"muted" }[s] || "muted");
  const prPill = (p) => ({ critical:"err", high:"warn", normal:"muted", low:"muted" }[p] || "muted");
  const STATUSES = ["open","in_progress","waiting","resolved","closed"];
  const PRIORITIES = ["low","normal","high","critical"];
</script>

<h1>Support</h1>
<div class="row" style="margin-bottom:14px;gap:8px">
  <select bind:value={fStatus} on:change={load} style="max-width:170px">
    <option value="">All statuses</option>{#each STATUSES as s}<option value={s}>{s}</option>{/each}
  </select>
  <button class="btn ghost" on:click={load}>Refresh</button>
  <div style="flex:1"></div>
  <button class="btn" on:click={() => (showCreate = true)}>+ New Ticket</button>
</div>

{#if loading}<div class="spinner">Loading…</div>
{:else if error}<div class="err-banner">{error}</div>
{:else}
  <div class="panel" style="padding:0">
    <table>
      <thead><tr><th>#</th><th>Subject</th><th>Customer</th><th>Priority</th><th>Status</th><th>Category</th><th>Created</th></tr></thead>
      <tbody>
        {#each rows as t}
          <tr style="cursor:pointer" on:click={() => open(t.id)}>
            <td class="muted">{t.id}</td><td>{t.subject}</td><td>{t.organization_name || "—"}</td>
            <td><span class="pill {prPill(t.priority)}">{t.priority}</span></td>
            <td><span class="pill {stPill(t.status)}">{t.status}</span></td>
            <td class="muted">{t.category || ""}</td><td class="muted">{dt(t.created_at)}</td>
          </tr>
        {/each}
        {#if !rows.length}<tr><td colspan="7" class="placeholder" style="padding:24px;text-align:center">No tickets.</td></tr>{/if}
      </tbody>
    </table>
  </div>
{/if}

{#if showCreate}
  <div class="scrim" on:click|self={() => (showCreate = false)}>
    <div class="panel modal">
      <h2 style="margin-top:0">New Ticket</h2>
      <label>Subject</label><input bind:value={form.subject} />
      <label>Details</label><textarea rows="3" bind:value={form.body}></textarea>
      <div class="row" style="gap:10px">
        <div style="flex:1"><label>Priority</label><select bind:value={form.priority}>{#each PRIORITIES as p}<option>{p}</option>{/each}</select></div>
        <div style="flex:1"><label>Category</label><select bind:value={form.category}><option>technical</option><option>billing</option><option>tax</option><option>onboarding</option><option>other</option></select></div>
      </div>
      {#if createErr}<div class="err-banner" style="margin-top:10px">{createErr}</div>{/if}
      <div class="row" style="justify-content:flex-end;margin-top:16px">
        <button class="btn ghost" on:click={() => (showCreate = false)}>Cancel</button>
        <button class="btn" disabled={creating || !form.subject} on:click={create}>{creating ? "Creating…" : "Create"}</button>
      </div>
    </div>
  </div>
{/if}

{#if detail || detailLoading}
  <div class="scrim" on:click|self={() => (detail = null)}>
    <div class="panel modal">
      {#if detailLoading}<div class="spinner">Loading…</div>
      {:else}
        <div class="row" style="justify-content:space-between"><h2 style="margin:0">#{detail.id} {detail.subject}</h2><span class="pill {stPill(detail.status)}">{detail.status}</span></div>
        <div class="muted" style="font-size:12px">{detail.organization_name || "—"} · {detail.category} · {detail.source} · {dt(detail.created_at)}</div>
        {#if detail.body}<p style="font-size:13px">{detail.body}</p>{/if}
        {#if actionMsg}<div class="err-banner" style="margin-top:8px">{actionMsg}</div>{/if}

        <div class="row" style="gap:10px;margin-top:8px">
          <div style="flex:1"><label>Status</label><select value={detail.status} on:change={(e)=>setStatus(e.target.value)}>{#each STATUSES as s}<option>{s}</option>{/each}</select></div>
          <div style="flex:1"><label>Priority</label><select value={detail.priority} on:change={(e)=>setPriority(e.target.value)}>{#each PRIORITIES as p}<option>{p}</option>{/each}</select></div>
        </div>

        <h2>Internal notes</h2>
        {#each detail.messages as m}
          <div style="padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.05);font-size:13px">{m.body}<div class="muted" style="font-size:11px">{dt(m.created_at)}{m.is_internal ? " · internal" : ""}</div></div>
        {/each}
        <div class="row" style="margin-top:8px;gap:8px">
          <input placeholder="Add internal note…" bind:value={msgBody} on:keydown={(e)=>e.key==='Enter'&&addMsg()} />
          <button class="btn ghost" on:click={addMsg}>Add</button>
        </div>

        <div class="row" style="justify-content:flex-end;margin-top:16px">
          <button class="btn ghost" on:click={() => (detail = null)}>Close</button>
        </div>
      {/if}
    </div>
  </div>
{/if}
