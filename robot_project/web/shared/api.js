(function () {
  "use strict";

  const configured = window.__SMART_RESTAURANT_API__ || window.localStorage.getItem("restaurant_api_base");
  const base = (configured || (window.location.origin + "/api/v1")).replace(/\/$/, "");
  const tokenKey = "restaurant_access_token";

  function token() { return window.localStorage.getItem(tokenKey) || ""; }
  function saveAuth(data) {
    if (data && data.access_token) window.localStorage.setItem(tokenKey, data.access_token);
    if (data && data.refresh_token) window.localStorage.setItem("restaurant_refresh_token", data.refresh_token);
    if (data && data.role) window.localStorage.setItem("restaurant_role", data.role);
  }
  function clearAuth() {
    window.localStorage.removeItem(tokenKey);
    window.localStorage.removeItem("restaurant_refresh_token");
    window.localStorage.removeItem("restaurant_role");
  }
  function authHeaders(extra) {
    const h = Object.assign({ "Accept": "application/json" }, extra || {});
    const t = token();
    if (t) h.Authorization = "Bearer " + t;
    return h;
  }
  async function request(path, options) {
    const opts = Object.assign({ method: "GET" }, options || {});
    opts.headers = authHeaders(Object.assign({}, opts.body ? { "Content-Type": "application/json" } : {}, opts.headers || {}));
    if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
    const response = await fetch(base + path, opts);
    let data = {};
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok) {
      const message = data.detail || data.message || ("Request failed (" + response.status + ")");
      const error = new Error(message); error.status = response.status; error.payload = data; throw error;
    }
    return data;
  }
  async function login(username, password) {
    const data = await request("/auth/login", { method: "POST", body: { username: username, password: password } });
    saveAuth(data); return data;
  }
  async function upload(path, file) {
    const form = new FormData(); form.append("file", file);
    const response = await fetch(base + path, { method: "POST", headers: authHeaders(), body: form });
    let data = {};
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok) {
      const error = new Error(data.detail || data.message || ("Upload failed (" + response.status + ")"));
      error.status = response.status; error.payload = data; throw error;
    }
    return data;
  }
  function logout() { clearAuth(); }
  function websocket(channel, query) {
    const wsBase = base.replace(/^http/i, "ws") + "/ws/" + channel;
    const params = new URLSearchParams(query || {});
    if (token()) params.set("token", token());
    const socket = new WebSocket(wsBase + (params.toString() ? "?" + params.toString() : ""));
    return socket;
  }
  function showError(target, error) {
    const el = typeof target === "string" ? document.querySelector(target) : target;
    if (el) { el.textContent = error && error.message ? error.message : String(error); el.className = "error"; }
  }
  window.RestaurantAPI = {
    base: base, request: request, login: login, logout: logout, token: token,
    websocket: websocket, upload: upload, showError: showError, role: function () { return localStorage.getItem("restaurant_role") || ""; }
  };
})();

