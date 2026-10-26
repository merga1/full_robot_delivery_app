(function () {
  const api = window.RestaurantAPI;
  const state = { menu: [], tables: [], cart: {}, order: null, token: "", socket: null, category: "All", query: "" };
  const $ = (id) => document.getElementById(id);
  const money = (v) => Number(v || 0).toFixed(2);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function error(e) { api.showError($("error"), e); $("error").classList.remove("hidden"); }

  function renderCategories() {
    const categories = ["All", ...new Set(state.menu.map((item) => item.category || "Menu"))];
    $("categories").innerHTML = categories.map((category) => `<button class="category-tab ${state.category === category ? "active" : ""}" data-category="${esc(category)}" role="tab" aria-selected="${state.category === category}">${esc(category)}</button>`).join("");
  }

  function renderMenu() {
    const root = $("menu");
    const query = state.query.trim().toLowerCase();
    const rows = state.menu.filter((item) => {
      const categoryMatch = state.category === "All" || (item.category || "Menu") === state.category;
      const textMatch = !query || `${item.name} ${item.description || ""} ${item.category || ""}`.toLowerCase().includes(query);
      return categoryMatch && textMatch;
    });
    if (!rows.length) { root.innerHTML = '<div class="empty-state"><span>☕</span><strong>No menu items found</strong><p class="muted">Try another category or search term.</p></div>'; return; }
    root.innerHTML = rows.map((item) => {
      const qty = state.cart[item.id] || 0;
      return `<article class="menu-item-card">
        <div class="menu-image-wrap"><img class="menu-image" src="${esc(item.image_url || "/assets/logo.png")}" alt="${esc(item.name)}" onerror="this.src='/assets/logo.png';this.onerror=null"><span class="menu-category">${esc(item.category || "Menu")}</span></div>
        <div class="menu-item-body"><div class="menu-item-title"><h3>${esc(item.name)}</h3><strong class="price">${money(item.price)}</strong></div>
        <p class="muted menu-description">${esc(item.description || "Prepared fresh to order")}</p>
        <div class="quantity-row"><span class="muted">Add to order</span><div class="quantity-control"><button class="quantity-button secondary" data-minus="${item.id}" aria-label="Remove one ${esc(item.name)}">−</button><strong>${qty}</strong><button class="quantity-button" data-plus="${item.id}" aria-label="Add one ${esc(item.name)}">+</button></div></div></div>
      </article>`;
    }).join("");
  }

  function renderTotal() {
    const count = state.menu.reduce((sum, item) => sum + (state.cart[item.id] || 0), 0);
    const total = state.menu.reduce((sum, item) => sum + Number(item.price || 0) * (state.cart[item.id] || 0), 0);
    $("cart-count").textContent = String(count);
    $("cart-summary").textContent = count ? `${count} item${count === 1 ? "" : "s"} selected` : "Your basket is empty";
    $("total").textContent = money(total);
    $("place").disabled = count === 0;
  }

  function changeQuantity(id, delta) {
    state.cart[id] = Math.max(0, Math.min(50, (state.cart[id] || 0) + delta));
    renderMenu(); renderTotal();
  }

  function renderTables() {
    const select = $("table"); select.innerHTML = "";
    if (!state.tables.length) { select.innerHTML = '<option value="">No tables available</option>'; return; }
    state.tables.forEach((table) => { const option = document.createElement("option"); option.value = table.id; option.textContent = `${table.display_name} · ${table.seats} seats`; select.appendChild(option); });
  }

  function renderOrder() {
    const order = state.order; if (!order) return;
    $("tracking").classList.remove("hidden"); $("order-code").textContent = order.code;
    $("order-status").innerHTML = `<span class="status ${esc(order.status)}">${esc(order.customer_status || order.status)}</span> ${esc(order.customer_status_detail || "")}`;
    $("timeline").innerHTML = (order.timeline || []).map((step) => `<div class="${step.state === "done" || step.state === "current" ? "done" : ""}"><strong>${esc(step.label)}</strong><span class="muted">${esc(step.state)}</span></div>`).join("");
    $("mission").textContent = order.mission ? `Robot mission: ${order.mission.status} · ${order.mission.destination_label}` : "The kitchen has not assigned a robot yet.";
    $("collect").classList.toggle("hidden", !["ARRIVED_AT_TABLE", "AWAITING_COLLECTION"].includes(order.status));
  }

  async function refresh() {
    if (!state.order) return;
    state.order = await api.request(`/orders/track/${encodeURIComponent(state.order.code)}?token=${encodeURIComponent(state.token)}`);
    renderOrder();
  }

  function connectLive() {
    if (!state.order || state.socket) return;
    try {
      state.socket = api.websocket("customer", { codes: state.order.code });
      state.socket.onopen = () => $("connection").textContent = "Live updates connected";
      state.socket.onmessage = () => refresh().catch(error);
      state.socket.onclose = () => { state.socket = null; $("connection").textContent = "Live updates offline · polling"; setTimeout(connectLive, 4000); };
    } catch (_) { $("connection").textContent = "Polling status"; }
  }

  async function place() {
    const items = state.menu.filter((item) => state.cart[item.id]).map((item) => ({ menu_id: item.id, qty: state.cart[item.id] }));
    if (!items.length) return error(new Error("Choose at least one menu item."));
    const table = $("table");
    try {
      const data = await api.request("/orders", { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) }, body: { table_id: Number(table.value), items, note: $("note").value, customer_name: $("customer-name").value } });
      state.order = data; state.token = data.tracking_token || "";
      localStorage.setItem("restaurant_order", JSON.stringify({ code: data.code, token: state.token }));
      renderOrder(); connectLive(); $("tracking").scrollIntoView({ behavior: "smooth" });
    } catch (e) { error(e); }
  }

  async function collect() {
    try { state.order = await api.request(`/orders/track/${encodeURIComponent(state.order.code)}/confirm-collection?token=${encodeURIComponent(state.token)}`, { method: "POST" }); renderOrder(); }
    catch (e) { error(e); }
  }

  $("categories").onclick = (event) => { const button = event.target.closest("[data-category]"); if (!button) return; state.category = button.dataset.category; renderCategories(); renderMenu(); };
  $("menu").onclick = (event) => { const plus = event.target.closest("[data-plus]"); const minus = event.target.closest("[data-minus]"); if (plus) changeQuantity(Number(plus.dataset.plus), 1); if (minus) changeQuantity(Number(minus.dataset.minus), -1); };
  $("menu-search").oninput = (event) => { state.query = event.target.value; renderMenu(); };
  $("place").onclick = place; $("refresh").onclick = () => refresh().catch(error); $("collect").onclick = collect;

  async function boot() {
    try {
      const [menu, tables] = await Promise.all([api.request("/menu"), api.request("/tables")]);
      state.menu = menu; state.tables = tables; renderTables(); renderCategories(); renderMenu(); renderTotal();
      const requestedTable = new URLSearchParams(window.location.search).get("table");
      if (requestedTable) { const match = state.tables.find((table) => table.public_code === requestedTable); if (match) $("table").value = String(match.id); }
      const saved = JSON.parse(localStorage.getItem("restaurant_order") || "null");
      if (saved && saved.code && saved.token) { state.order = await api.request(`/orders/track/${encodeURIComponent(saved.code)}?token=${encodeURIComponent(saved.token)}`); state.token = saved.token; renderOrder(); connectLive(); }
    } catch (e) { error(e); }
  }
  boot();
})();
