// Shows which server the app is trying (passed as ?server=...).
const server = new URLSearchParams(location.search).get("server") || "";
const where = document.getElementById("where");
if (server) { where.textContent = "Server: "; const c = document.createElement("code"); c.textContent = server; where.appendChild(c); }
