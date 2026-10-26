/* order.js — the customer order page.
   Plain ES5-ish JavaScript on purpose: it must run in any browser on the table
   phone without a build step.  No template literals, so the file can be served
   as a static asset. */

var TABLE = window.TABLE_CODE;
var TOKEN_KEY = "robot_token_" + TABLE;
var cart = [];
var current = null;
var pollTimer = null;

function api(url) { return url; }

function money(n) { return "$" + (Math.round(n * 100) / 100).toFixed(2); }

function getToken() {
  try { return window.localStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
}

function setToken(t) {
  try { window.localStorage.setItem(TOKEN_KEY, t); } catch (e) { /* private mode */ }
}

function send(url, options) {
  options = options || {};
  options.headers = options.headers || {};
  var token = getToken();
  if (token) { options.headers["Authorization"] = "Bearer " + token; }
  if (options.body) { options.headers["Content-Type"] = "application/json"; }
  return fetch(url, options).then(function (res) {
    if (res.status === 401) { return null; }
    return res.json().then(function (data) { return { ok: res.ok, data: data }; });
  });
}

function newSession() {
  return fetch(api("/customer/session"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ table_code: TABLE })
  }).then(function (res) {
    return res.json().then(function (data) {
      if (!res.ok) { throw new Error(data.detail || "Cannot start a session"); }
      setToken(data.token);
      return data.token;
    });
  });
}

function ensureToken() {
  var token = getToken();
  if (token) { return Promise.resolve(token); }
  return newSession();
}

/* ── Menu ──────────────────────────────────────────────────────────────── */

function loadMenu() {
  return fetch(api("/customer/menu")).then(function (res) { return res.json(); })
    .then(function (data) {
      var items = (data && data.menu) || [];
      document.getElementById("menuHint").textContent = items.length + " items";
      var grid = document.getElementById("menuGrid");
      grid.innerHTML = "";
      items.forEach(function (item) {
        var card = document.createElement("div");
        card.className = "card item";
        var top = document.createElement("div");
        top.className = "row between";
        var name = document.createElement("div");
        name.className = "name";
        name.textContent = item.name;
        var price = document.createElement("div");
        price.className = "price";
        price.textContent = money(item.price);
        top.appendChild(name);
        top.appendChild(price);
        card.appendChild(top);
        if (item.description) {
          var desc = document.createElement("div");
          desc.className = "muted";
          desc.textContent = item.description;
          card.appendChild(desc);
        }
        var row = document.createElement("div");
        row.className = "row between";
        var count = document.createElement("span");
        count.className = "muted";
        count.textContent = "0";
        var add = document.createElement("button");
        add.className = "btn";
        add.textContent = "Add";
        add.onclick = function () { addToCart(item, count); };
        row.appendChild(count);
        row.appendChild(add);
        card.appendChild(row);
        grid.appendChild(card);
      });
      if (!items.length) {
        document.getElementById("menuHint").textContent = "The kitchen has not published a menu yet.";
      }
    });
}

/* ── Cart ──────────────────────────────────────────────────────────────── */

function addToCart(item, countEl) {
  var found = null;
  for (var i = 0; i < cart.length; i++) {
    if (cart[i].menu_id === item.id) { found = cart[i]; break; }
  }
  if (found) { found.qty += 1; } else { found = { menu_id: item.id, qty: 1, name: item.name, price: item.price }; cart.push(found); }
  if (countEl) { countEl.textContent = String(found.qty); }
  renderCart();
}

function renderCart() {
  var count = 0;
  var total = 0;
  cart.forEach(function (line) {
    count += line.qty;
    total += line.price * line.qty;
  });
  document.getElementById("cartCount").textContent = count + (count === 1 ? " item" : " items");
  document.getElementById("cartTotal").textContent = money(total);
  var box = document.getElementById("cartLines");
  box.innerHTML = "";
  cart.forEach(function (line, index) {
    var row = document.createElement("div");
    row.className = "row between";
    var left = document.createElement("div");
    left.textContent = line.qty + " x " + line.name;
    var right = document.createElement("div");
    right.className = "muted";
    right.textContent = money(line.price * line.qty);
    var rm = document.createElement("button");
    rm.className = "btn ghost";
    rm.textContent = "remove";
    rm.onclick = function () {
      cart.splice(index, 1);
      renderCart();
    };
    row.appendChild(left);
    row.appendChild(right);
    row.appendChild(rm);
    box.appendChild(row);
  });
}

/* ── Orders ────────────────────────────────────────────────────────────── */

function showStatus(order, text) {
  current = order;
  var box = document.getElementById("statusBox");
  box.classList.remove("hide");
  document.getElementById("statusTitle").textContent = order.kind === "call" ? "Robot on its way" : "Order " + order.code;
  var badge = document.getElementById("statusBadge");
  badge.textContent = order.status;
  badge.className = "badge " + order.status;
  document.getElementById("statusText").textContent = text;
  startPolling();
}

function startPolling() {
  if (pollTimer) { window.clearTimeout(pollTimer); }
  pollTimer = window.setTimeout(poll, 3000);
}

function poll() {
  if (!current) { return; }
  ensureToken().then(function () {
    return send(api("/customer/orders/" + encodeURIComponent(current.code)));
  }).then(function (res) {
    if (!res || !res.ok) { return; }
    var order = res.data.order;
    var badge = document.getElementById("statusBadge");
    badge.textContent = order.status;
    badge.className = "badge " + order.status;
    document.getElementById("statusText").textContent = statusText(order);
    if (order.status === "delivered" || order.status === "cancelled") { return; }
    startPolling();
  }).catch(function () { /* offline — try again later */ });
}

function statusText(order) {
  if (order.status === "placed") { return "The kitchen has received your request."; }
  if (order.status === "accepted") { return "The kitchen accepted your order."; }
  if (order.status === "preparing") { return "Your food is being prepared."; }
  if (order.status === "ready") { return "Your order is ready — the robot is coming."; }
  if (order.status === "delivering") { return "The robot is on its way to your table."; }
  if (order.status === "delivered") { return "Enjoy!"; }
  if (order.status === "cancelled") { return "This request was cancelled." + (order.error ? " " + order.error : ""); }
  return "";
}

document.getElementById("callBtn").onclick = function () {
  var btn = this;
  btn.disabled = true;
  ensureToken().then(function () {
    return send(api("/customer/calls"), { method: "POST", body: JSON.stringify({}) });
  }).then(function (res) {
    btn.disabled = false;
    if (!res || !res.ok) {
      window.alert((res && res.data && res.data.detail) || "Could not call the robot");
      return;
    }
    showStatus(res.data.order, res.data.dispatched ? "The robot is coming to your table." : "The robot is busy — the kitchen has your request.");
  }).catch(function () {
    btn.disabled = false;
    window.alert("Could not reach the robot");
  });
};

document.getElementById("orderBtn").onclick = function () {
  if (!cart.length) { window.alert("Your order is empty"); return; }
  var btn = this;
  btn.disabled = true;
  var payload = {
    items: cart.map(function (line) { return { menu_id: line.menu_id, qty: line.qty }; }),
    note: document.getElementById("note").value
  };
  ensureToken().then(function () {
    return send(api("/customer/orders"), { method: "POST", body: JSON.stringify(payload) });
  }).then(function (res) {
    btn.disabled = false;
    if (!res || !res.ok) {
      window.alert((res && res.data && res.data.detail) || "Could not send the order");
      return;
    }
    cart = [];
    renderCart();
    showStatus(res.data.order, "The kitchen has received your order.");
  }).catch(function () {
    btn.disabled = false;
    window.alert("Could not reach the kitchen");
  });
};

loadMenu();
renderCart();
