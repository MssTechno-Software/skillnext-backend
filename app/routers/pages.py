from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(include_in_schema=False)

PAGE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Set your password - Skill Next</title>
  <style>
    body { font-family: system-ui, sans-serif; background:#f4f5f7; display:flex;
           justify-content:center; padding:60px 16px; }
    .card { background:#fff; padding:32px; border-radius:12px; width:100%;
            max-width:380px; box-shadow:0 2px 12px rgba(0,0,0,.08); }
    h2 { margin-top:0; }
    input { width:100%; padding:10px; margin:6px 0 14px; box-sizing:border-box;
            border:1px solid #ccc; border-radius:6px; }
    button { width:100%; padding:11px; border:0; border-radius:6px;
             background:#4f46e5; color:#fff; font-size:15px; cursor:pointer; }
    #msg { margin-top:14px; font-size:14px; }
    .err { color:#c0392b; } .ok { color:#1e8449; }
  </style>
</head>
<body>
  <div class="card">
    <h2>Set your password</h2>
    <p id="hello"></p>
    <form id="form" style="display:none">
      <label>New password</label>
      <input type="password" id="pw" required minlength="8">
      <label>Confirm password</label>
      <input type="password" id="pw2" required minlength="8">
      <button type="submit">Set password</button>
    </form>
    <div id="msg"></div>
  </div>

<script>
  const token = new URLSearchParams(location.search).get("token");
  const msg = document.getElementById("msg");
  const form = document.getElementById("form");

  function show(text, ok) {
    msg.textContent = text;
    msg.className = ok ? "ok" : "err";
  }

  async function init() {
    if (!token) { show("Missing token.", false); return; }
    const r = await fetch("/api/auth/verify-token?token=" + encodeURIComponent(token));
    const d = await r.json();
    if (!r.ok) { show(d.message, false); return; }
    document.getElementById("hello").textContent = "Hi " + d.data.firstName + " (" + d.data.email + ")";
    form.style.display = "block";
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = await fetch("/api/auth/set-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: token,
        password: document.getElementById("pw").value,
        confirmPassword: document.getElementById("pw2").value
      })
    });
    const d = await r.json();
    if (r.ok) { form.style.display = "none"; show(d.message + " Redirecting to login...", true); setTimeout(() => location.href = "/login", 1500); }
    else if (d.errors) { show(d.errors[0].message.replace("Value error, ", ""), false); }
    else { show(d.message, false); }
  });

  init();
</script>
</body>
</html>"""


@router.get("/set-password", response_class=HTMLResponse)
def set_password_page():
    return PAGE


LOGIN_PAGE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Log in - Skill Next</title>
  <style>
    body { font-family: system-ui, sans-serif; background:#f4f5f7; display:flex;
           justify-content:center; padding:60px 16px; }
    .card { background:#fff; padding:32px; border-radius:12px; width:100%;
            max-width:380px; box-shadow:0 2px 12px rgba(0,0,0,.08); }
    h2 { margin-top:0; }
    input { width:100%; padding:10px; margin:6px 0 14px; box-sizing:border-box;
            border:1px solid #ccc; border-radius:6px; }
    button { width:100%; padding:11px; border:0; border-radius:6px;
             background:#4f46e5; color:#fff; font-size:15px; cursor:pointer; }
    #msg { margin-top:14px; font-size:14px; }
    .err { color:#c0392b; } .ok { color:#1e8449; }
  </style>
</head>
<body>
  <div class="card">
    <h2>Log in</h2>
    <form id="form">
      <label>Email</label>
      <input type="email" id="email" required>
      <label>Password</label>
      <input type="password" id="pw" required>
      <button type="submit">Log in</button>
    </form>
    <div id="msg"></div>
  </div>

<script>
  const msg = document.getElementById("msg");
  function show(text, ok) { msg.textContent = text; msg.className = ok ? "ok" : "err"; }

  document.getElementById("form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: document.getElementById("email").value,
        password: document.getElementById("pw").value
      })
    });
    const d = await r.json();
    if (r.ok) {
      localStorage.setItem("access_token", d.data.accessToken);
      show("Welcome, " + d.data.user.firstName + "! Login successful.", true);
    } else if (d.errors) {
      show(d.errors[0].message.replace("Value error, ", ""), false);
    } else {
      show(d.message, false);
    }
  });
</script>
</body>
</html>"""


@router.get("/login", response_class=HTMLResponse)
def login_page():
    return LOGIN_PAGE