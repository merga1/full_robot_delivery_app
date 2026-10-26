(function () {
  const api = RestaurantAPI;
  const $ = (id) => document.getElementById(id);
  const state = { menu: [], tables: [], socket: null };
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const imageFor = (item) => item.image_url || "/assets/logo.png";

  function show(error, target) { api.showError(target || $("error"), error); }
  function message(id, text, good) { const element = $(id); element.textContent = text; element.className = good ? "success" : "error"; }
  function metric(label, value) { return '<div class="card"><div class="metric-label">' + label + '</div><div class="metric">' + (value ?? 0) + '</div></div>'; }

  async function load() {
    try {
      const data = await Promise.all([
        api.request("/admin/overview"), api.request("/orders?limit=200"), api.request("/robots"),
        api.request("/tables?include_inactive=true"), api.request("/menu?include_unavailable=true"), api.request("/admin/audit?limit=30")
      ]);
      const overview = data[0], orders = data[1], robots = data[2];
      state.tables = data[3]; state.menu = data[4];
      const o = overview.orders, r = overview.robots;
      $("metrics").innerHTML = metric("New orders", o.new) + metric("Preparing", o.preparing) + metric("Ready to load", o.ready_for_loading) + metric("Active deliveries", o.active_deliveries) + metric("Completed", o.completed) + metric("Online robots", r.online);
      renderOrders(orders); renderRobots(robots); renderMenu(); renderTables(); renderAudit(data[5]);
    } catch (error) { show(error); }
  }

  function renderOrders(rows) {
    const filter = $("order-filter").value;
    const visible = filter ? rows.filter((order) => order.status === filter) : rows;
    $("orders").innerHTML = visible.length ? visible.map((order) => {
      return '<div class="card order-card" style="margin:10px 0"><h3><span>' + esc(order.code) + ' · ' + esc(order.table_label) + '</span><span class="status ' + esc(order.status) + '">' + esc(order.status) + '</span></h3><p>' + (order.items || []).map((item) => item.qty + '× ' + esc(item.name)).join(", ") + '</p><div class="toolbar"><select data-robot-for="' + order.id + '"><option value="">Assign robot…</option></select><button data-action="assign" data-id="' + order.id + '">Assign</button><button data-action="load" data-id="' + order.id + '">Food loaded</button><button data-action="dispatch" data-id="' + order.id + '">Dispatch</button></div></div>';
    }).join("") : '<p class="muted">No matching orders.</p>';
    api.request("/robots").then((available) => document.querySelectorAll("select[data-robot-for]").forEach((select) => available.forEach((robot) => { const option = document.createElement("option"); option.value = robot.id; option.textContent = robot.name + " · " + robot.connection_status + " · " + (robot.battery_percent == null ? "N/A" : robot.battery_percent + "%"); select.appendChild(option); }))).catch(() => {});
  }

  function renderRobots(rows) {
    $("robots").innerHTML = rows.length ? rows.map((robot) => '<div class="card" style="margin:8px 0"><strong>' + esc(robot.name) + ' (' + esc(robot.robot_id) + ')</strong><p>' + esc(robot.connection_status) + ' · ' + esc(robot.availability) + ' · battery ' + (robot.battery_percent == null ? "N/A" : robot.battery_percent + "%") + '</p><button data-return="' + robot.id + '" ' + (robot.connection_status !== "ONLINE" ? "disabled" : "") + '>Return home</button></div>').join("") : '<p class="muted">No registered robots. A gateway appears here after its first authenticated connection.</p>';
  }

  function renderMenu() {
    $("menu-list").innerHTML = state.menu.length ? state.menu.map((item) => '<article class="catalog-item"><img src="' + esc(imageFor(item)) + '" alt="' + esc(item.name) + '" onerror="this.src=\'/assets/logo.png\';this.onerror=null"><div><h4>' + esc(item.name) + ' <span class="status ' + (item.available ? "READY_FOR_PICKUP" : "REJECTED") + '">' + (item.available ? "Visible" : "Hidden") + '</span></h4><div class="catalog-meta"><strong>' + Number(item.price).toFixed(2) + '</strong> · ' + esc(item.category) + ' · order ' + item.sort_order + '<br>' + esc(item.description || "No description") + '</div></div><div class="catalog-actions"><button class="secondary" data-edit-menu="' + item.id + '">Edit</button><button class="danger" data-delete-menu="' + item.id + '">Delete</button></div></article>').join("") : '<p class="muted">No menu items yet. Add the first food or drink.</p>';
  }

  function renderTables() {
    $("table-list").innerHTML = state.tables.length ? state.tables.map((table) => '<article class="table-card ' + (table.is_active ? "" : "inactive") + '"><div><h4>' + esc(table.display_name) + ' <span class="status ' + (table.is_active ? "READY_FOR_PICKUP" : "REJECTED") + '">' + (table.is_active ? "Active" : "Inactive") + '</span></h4><p>' + esc(table.table_number) + ' · ' + table.seats + ' seats · destination: ' + esc(table.destination_id || "not configured") + ' · QR: ' + esc(table.public_code) + '</p></div><div class="catalog-actions"><button class="secondary" data-edit-table="' + table.id + '">Edit</button><button class="danger" data-delete-table="' + table.id + '">Delete</button></div></article>').join("") : '<p class="muted">No tables registered.</p>';
  }

  function renderAudit(rows) { $("audit").innerHTML = rows.map((row) => '<p><small>' + new Date(row.created_at).toLocaleString() + '</small> · <strong>' + esc(row.actor) + '</strong> · ' + esc(row.action) + ' · ' + esc(row.resource_type) + ' ' + esc(row.resource_id || "") + '</p>').join("") || '<p class="muted">No audit events yet.</p>'; }

  function resetMenuForm() {
    $("menu-form").reset(); $("menu-id").value = ""; $("menu-sort").value = "0"; $("menu-available").checked = true; $("menu-image-preview").classList.add("hidden"); $("menu-form-kicker").textContent = "NEW MENU ITEM"; $("menu-form-title").textContent = "Add food or drink"; $("menu-save").textContent = "Add to menu"; $("menu-cancel").classList.add("hidden");
  }

  function editMenu(id) {
    const item = state.menu.find((row) => row.id === Number(id)); if (!item) return;
    $("menu-id").value = item.id; $("menu-name").value = item.name; $("menu-category").value = item.category; $("menu-price").value = item.price; $("menu-description").value = item.description || ""; $("menu-preparation").value = item.preparation_note || ""; $("menu-sort").value = item.sort_order || 0; $("menu-available").checked = item.available; $("menu-image-url").value = item.image_url || ""; $("menu-image").value = "";
    const preview = $("menu-image-preview"); preview.src = imageFor(item); preview.classList.remove("hidden"); $("menu-form-kicker").textContent = "EDIT MENU ITEM"; $("menu-form-title").textContent = "Update " + item.name; $("menu-save").textContent = "Save changes"; $("menu-cancel").classList.remove("hidden"); $("menu-form").scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function saveMenu(event) {
    event.preventDefault();
    try {
      let imageUrl = $("menu-image-url").value.trim() || null;
      const file = $("menu-image").files[0];
      if (file) imageUrl = (await api.upload("/uploads/menu-image", file)).image_url;
      const id = $("menu-id").value;
      const body = { name: $("menu-name").value, category: $("menu-category").value, price: Number($("menu-price").value), description: $("menu-description").value, preparation_note: $("menu-preparation").value || null, sort_order: Number($("menu-sort").value || 0), available: $("menu-available").checked, image_url: imageUrl };
      await api.request(id ? "/menu/" + id : "/menu", { method: id ? "PATCH" : "POST", body });
      message("menu-message", id ? "Menu item updated." : "Menu item added.", true); resetMenuForm(); await load();
    } catch (error) { show(error, $("menu-message")); }
  }

  function resetTableForm() {
    $("table-form").reset(); $("table-id").value = ""; $("table-seats").value = "4"; $("table-active").checked = true; $("table-form-kicker").textContent = "NEW TABLE"; $("table-form-title").textContent = "Add table"; $("table-save").textContent = "Save table"; $("table-cancel").classList.add("hidden");
  }

  function editTable(id) {
    const table = state.tables.find((row) => row.id === Number(id)); if (!table) return;
    $("table-id").value = table.id; $("table-number").value = table.table_number; $("table-name").value = table.display_name; $("table-seats").value = table.seats; $("destination").value = table.destination_id || ""; $("map-identifier").value = table.map_identifier || ""; $("table-notes").value = table.notes || ""; $("table-active").checked = table.is_active; $("table-form-kicker").textContent = "EDIT TABLE"; $("table-form-title").textContent = "Update " + table.display_name; $("table-save").textContent = "Save changes"; $("table-cancel").classList.remove("hidden"); $("table-form").scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function saveTable(event) {
    event.preventDefault();
    try {
      const id = $("table-id").value;
      const body = { table_number: $("table-number").value, display_name: $("table-name").value, seats: Number($("table-seats").value), destination_id: $("destination").value || null, map_identifier: $("map-identifier").value || null, notes: $("table-notes").value || null, is_active: $("table-active").checked };
      await api.request(id ? "/tables/" + id : "/tables", { method: id ? "PATCH" : "POST", body });
      message("table-message", id ? "Table updated." : "Table added.", true); resetTableForm(); await load();
    } catch (error) { show(error, $("table-message")); }
  }

  async function action(button) {
    const id = button.dataset.id, actionName = button.dataset.action; const select = document.querySelector('select[data-robot-for="' + id + '"]'); button.disabled = true;
    try { if (actionName === "assign") await api.request("/orders/" + id + "/assign-robot", { method: "POST", body: { robot_id: Number(select.value) } }); if (actionName === "load") await api.request("/orders/" + id + "/confirm-loaded", { method: "POST" }); if (actionName === "dispatch") await api.request("/orders/" + id + "/dispatch", { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) }, body: {} }); await load(); }
    catch (error) { show(error); } finally { button.disabled = false; }
  }

  async function deleteMenu(id) { if (!window.confirm("Delete this food or drink from the menu?")) return; try { await api.request("/menu/" + id, { method: "DELETE" }); await load(); } catch (error) { show(error); } }
  async function deleteTable(id) { if (!window.confirm("Delete this table? Existing orders will keep their history.")) return; try { await api.request("/tables/" + id, { method: "DELETE" }); await load(); } catch (error) { show(error); } }

  $("login-form").onsubmit = async (event) => { event.preventDefault(); try { const data = await api.login($("username").value, $("password").value); if (data.role !== "admin") throw new Error("Administrator role required"); $("login").classList.add("hidden"); $("app").classList.remove("hidden"); await load(); live(); } catch (error) { show(error); } };
  $("menu-form").onsubmit = saveMenu; $("menu-cancel").onclick = resetMenuForm; $("table-form").onsubmit = saveTable; $("table-cancel").onclick = resetTableForm;
  $("menu-image").onchange = () => { const file = $("menu-image").files[0]; if (!file) return; const preview = $("menu-image-preview"); preview.src = URL.createObjectURL(file); preview.classList.remove("hidden"); };
  $("reload").onclick = load; $("order-filter").onchange = load; $("logout").onclick = () => { api.logout(); location.reload(); };
  document.addEventListener("click", (event) => { const target = event.target; if (target.dataset.action) action(target); if (target.dataset.return) api.request("/robots/" + target.dataset.return + "/return-home", { method: "POST", body: {} }).then(load).catch(show); if (target.dataset.editMenu) editMenu(target.dataset.editMenu); if (target.dataset.deleteMenu) deleteMenu(target.dataset.deleteMenu); if (target.dataset.editTable) editTable(target.dataset.editTable); if (target.dataset.deleteTable) deleteTable(target.dataset.deleteTable); });
  function live() { if (state.socket) state.socket.close(); state.socket = api.websocket("admin"); state.socket.onopen = () => $("live").textContent = "Live"; state.socket.onmessage = () => load(); state.socket.onclose = () => { $("live").textContent = "Reconnecting…"; setTimeout(live, 3000); }; }
  if (api.token()) { $("login").classList.add("hidden"); $("app").classList.remove("hidden"); load(); live(); }
})();
