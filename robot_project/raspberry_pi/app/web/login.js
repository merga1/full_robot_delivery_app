/* login.js — sign in for the kitchen dashboard. */

var TOKEN_KEY = "staff_token";

document.getElementById("loginBtn").onclick = function () {
  var btn = this;
  var username = document.getElementById("user").value;
  var password = document.getElementById("pass").value;
  btn.disabled = true;
  document.getElementById("msg").textContent = "";
  fetch("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: username, password: password })
  }).then(function (res) {
    return res.json().then(function (data) {
      if (!res.ok) { throw new Error(data.detail || "Wrong username or password"); }
      try { window.localStorage.setItem(TOKEN_KEY, data.access_token); } catch (e) { }
      window.location.href = "/kitchen";
    });
  }).catch(function (err) {
    btn.disabled = false;
    document.getElementById("msg").textContent = err.message;
  });
};

document.getElementById("pass").addEventListener("keydown", function (ev) {
  if (ev.key === "Enter") { document.getElementById("loginBtn").click(); }
});
