/* kitchen.js — the kitchen dashboard.
   Shows the open orders, lets the kitchen move them through the status
   machine and sends the robot when the food is ready. */

var TOKEN_KEY = "staff_token";
var pollTimer = null;

function getToken() {
  try { return window.localStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
}

function setToken(t) {
  try { window.localStorage.setItem(TOKEN_KEY, t); } catch (e) { }
}

function api(url) { return url; }

function send(url, options) {
  options = options || {};
  options.headers = options.headers || {};
  var token = getToken();
  if (token) { options.headers["Authorization"] = "Bearer " + token; }
  if (options.body) { options.headers["Content-Type"] = "application/json"; }
  return fetch(url, options).then(function (res) {
    return res.json().catch(function () { return {}; }).then(function (data) {
      return { ok: res.ok, status: res.status, data: data };
    });
  });
}

function needLogin() {
  window.location.href = "/kitchen/login";
}

function money(n) { return "$" + (Math.round(n * 100) / 100).toFixed(2); }

function el(tag, className, text) {
  var node = document.createElement(tag);
  if (className) { node.className = className; }
  if (text !== undefined && text !== null) { node.textContent = text; }
  return node;
}

/* ── Orders ────────────────────────────────────────────────────────────── */

function loadOrders() {
  return send(api("/kitchen/api/orders")).then(function (res) {
    if (res.status === 401) { needLogin(); return null; }
    if (!res.ok) { return null; }
    renderOrders(res.data.orders || []);
    return res.data.orders || [];
  });
}

function renderOrders(orders) {
  document.getElementById("openHint").textContent = orders.length + " open";
  var box = document.getElementById("orders");
  box.innerHTML = "";
  if (!orders.length) {
    box.appendChild(el("div", "card muted", "No open orders"));
    return;
  }
  orders.forEach(function (order) {
    var card = el("div", "card order");
    var top = el("div", "row between");
    var left = el("div", "row");
    left.appendChild(el("strong", null, order.kind === "call" ? "Call the robot" : "Order " + order.code));
    left.appendChild(el("span", "badge", "Table " + (order.table_code || "-")));
    if (order.kind === "call") { left.appendChild(el("span", "badge", "call")); }
    var badge = el("span", "badge " + order.status, order.status);
    top.appendChild(left);
    top.appendChild(badge);
    card.appendChild(top);

    if (order.items && order.items.length) {
      var lines = order.items.map(function (i) { return i.qty + " x " + i.name; }).join(", ");
      card.appendChild(el("div", "muted", lines));
    }
    if (order.note) { card.appendChild(el("div", "muted", "Note: " + order.note)); }
    if (order.error) { card.appendChild(el("div", "muted", order.error)); }
    card.appendChild(el("div", "muted", money(order.total || 0)));

    var actions = el("div", "row");
    if (order.status === "placed") { actions.appendChild(actionBtn("Accept", "btn", order, "advance")); }
    if (order.status === "accepted") { actions.appendChild(actionBtn("Start preparing", "btn", order, "advance")); }
    if (order.status === "preparing") { actions.appendChild(actionBtn("Ready", "btn", order, "advance")); }
    if (order.status === "ready") { actions.appendChild(actionBtn("Send the robot", "btn green", order, "dispatch")); }
    if (order.status === "delivering") { actions.appendChild(actionBtn("Delivered", "btn", order, "advance")); }
    actions.appendChild(actionBtn("Cancel", "btn red", order, "cancel"));
    card.appendChild(actions);
    box.appendChild(card);
  });
}

function actionBtn(text, className, order, action) {
  var btn = el("button", className, text);
  btn.onclick = function () {
    btn.disabled = true;
    var url = action === "advance"
      ? api("/kitchen/api/orders/" + order.id + "/advance")
      : api("/kitchen/api/orders/" + order.id + "/" + action);
    send(url, { method: "POST", body: "{}" }).then(function (res) {
      btn.disabled = false;
      if (!res.ok) {
        window.alert((res.data && res.data.detail) || "Could not update the order");
      }
      loadOrders();
    });
  };
  return btn;
}

/* ── Menu & tables ─────────────────────────────────────────────────────── */

function loadMenu() {
  return send(api("/kitchen/api/menu")).then(function (res) {
    if (res.status === 401) { needLogin(); return; }
    var items = (res.data && res.data.menu) || [];
    document.getElementById("menuHint").textContent = items.length + " items";
    var rows = document.getElementById("menuRows");
    rows.innerHTML = "";
    items.forEach(function (item) {
      var tr = el("tr");
      tr.appendChild(el("td", null, item.name));
      tr.appendChild(el("td", null, item.category));
      tr.appendChild(el("td", null, money(item.price)));
      var last = el("td", null, item.available ? "yes" : "sold out");
      tr.appendChild(last);
      rows.appendChild(tr);
    });
  });
}

function loadTables() {
  return send(api("/kitchen/api/tables")).then(function (res) {
    if (res.status === 401) { needLogin(); return; }
    var tables = (res.data && res.data.tables) || [];
    document.getElementById("tableHint").textContent = tables.length + " tables";
    var rows = document.getElementById("tableRows");
    rows.innerHTML = "";
    tables.forEach(function (t) {
      var tr = el("tr");
      tr.appendChild(el("td", null, t.code));
      tr.appendChild(el("td", null, t.name));
      tr.appendChild(el("td", null, t.destination || "-"));
      tr.appendChild(el("td", null, String(t.seats)));
      tr.appendChild(el("td", null, t.active ? "yes" : "no"));
      rows.appendChild(tr);
    });
  });
}

/* ── Boot ──────────────────────────────────────────────────────────────── */

document.getElementById("refreshBtn").onclick = function () { loadOrders(); loadMenu(); loadTables(); };
document.getElementById("logoutBtn").onclick = function () {
  try { window.localStorage.removeItem(TOKEN_KEY); } catch (e) { }
  window.location.href = "/kitchen/login";
};

function tick() {
  var now = new Date();
  document.getElementById("clock").textContent = now.toLocaleTimeString();
}
setInterval(tick, 1000);
tick();

if (!getToken()) { needLogin(); }
else {
  loadOrders();
  loadMenu();
  loadTables();
  pollTimer = setInterval(loadOrders, 5000);
}
