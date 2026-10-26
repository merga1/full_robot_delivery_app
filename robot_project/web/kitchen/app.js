(function () {
  const api = RestaurantAPI, $ = (id) => document.getElementById(id); let socket = null;
  const statuses = ["PENDING", "ACCEPTED", "PREPARING", "READY_FOR_PICKUP", "READY_FOR_DISPATCH", "LOADED", "OUT_FOR_DELIVERY", "ARRIVED_AT_TABLE", "COLLECTION_CONFIRMED", "DELIVERED", "REJECTED", "CANCELLED", "DELIVERY_FAILED"];
  function render(rows) {
    const root = $("orders"); root.innerHTML = "";
    if (!rows.length) { root.innerHTML = '<div class="card"><strong>No open orders.</strong><p class="muted">New customer orders will appear here.</p></div>'; return; }
    rows.forEach((o) => {
      const card = document.createElement("article"); card.className = "card order-card";
      const items = (o.items || []).map((i) => `<li>${i.qty} × ${i.name}${i.customizations ? ` — ${i.customizations}` : ""}${i.preparation_note ? ` <em>${i.preparation_note}</em>` : ""}</li>`).join("");
      card.innerHTML = `<h3><span>Order ${o.code}</span><span class="status ${o.status}">${o.status}</span></h3><p><strong>Table:</strong> ${o.table_label} · <strong>Received:</strong> ${new Date(o.created_at).toLocaleString()}</p><ul class="items">${items || "<li>Robot call / no food items</li>"}</ul><p class="muted">${o.note || "No special instructions"}</p><div class="toolbar actions"></div>`;
      const actions = card.querySelector(".actions");
      const action = (label, path, body) => { const b = document.createElement("button"); b.textContent = label; b.onclick = async () => { b.disabled = true; try { await api.request(`/orders/${o.id}/${path}`, { method: "POST", body: body || {} }); await load(); } catch (e) { api.showError($("login-error"), e); } finally { b.disabled = false; } }; actions.appendChild(b); };
      if (o.status === "PENDING") { action("Accept", "accept"); const reject = document.createElement("button"); reject.className = "danger"; reject.textContent = "Reject"; reject.onclick = async () => { const reason = prompt("Why is this order rejected?"); if (!reason) return; try { await api.request(`/orders/${o.id}/reject`, { method: "POST", body: { reason } }); await load(); } catch (e) { api.showError($("login-error"), e); } }; actions.appendChild(reject); }
      if (o.status === "ACCEPTED") action("Start preparing", "start-preparation");
      if (o.status === "PREPARING") action("Mark ready", "ready");
      if (o.status === "READY_FOR_PICKUP") action("Ready for dispatch", "ready-for-dispatch");
      if (["PENDING", "ACCEPTED", "PREPARING", "READY_FOR_PICKUP"].includes(o.status)) { const c = document.createElement("button"); c.className = "danger"; c.textContent = "Cancel"; c.onclick = async () => { const reason = prompt("Reason for cancellation?") || "Kitchen cancellation"; try { await api.request(`/orders/${o.id}/cancel`, { method: "POST", body: { reason } }); await load(); } catch (e) { api.showError($("login-error"), e); } }; actions.appendChild(c); }
      root.appendChild(card);
    });
  }
  async function load() { try { render(await api.request("/orders?limit=200")); } catch (e) { api.showError($("login-error"), e); } }
  function live() { if (socket) socket.close(); try { socket = api.websocket("kitchen"); socket.onopen = () => $("live").textContent = "Live"; socket.onmessage = () => load(); socket.onclose = () => { $("live").textContent = "Reconnecting…"; setTimeout(live, 3000); }; } catch (_) {} }
  $("login-form").onsubmit = async (ev) => { ev.preventDefault(); try { const data = await api.login($("username").value, $("password").value); if (!["kitchen", "admin", "manager"].includes(data.role)) throw new Error("This account is not a kitchen role"); $("login").classList.add("hidden"); $("board").classList.remove("hidden"); await load(); live(); } catch (e) { api.showError($("login-error"), e); } };
  $("reload").onclick = load; $("logout").onclick = () => { api.logout(); location.reload(); };
  if (api.token()) { $("login").classList.add("hidden"); $("board").classList.remove("hidden"); load(); live(); }
})();

