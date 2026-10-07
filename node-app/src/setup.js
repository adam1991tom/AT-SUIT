const q = new URLSearchParams(location.search);
const $ = (id) => document.getElementById(id);
$("server").value = q.get("server") || "";
$("code").value = q.get("code") || "";
$("name").value = q.get("name") || "";
$("msg").textContent = q.get("message") || "";
$("f").onsubmit = async (e) => {
  e.preventDefault();
  $("go").disabled = true;
  $("err").textContent = "";
  const r = await window.setup.enrol({ server: $("server").value, code: $("code").value, name: $("name").value });
  if (r.ok) { $("go").textContent = "Added. Opening the workspace…"; return; }
  $("err").textContent = r.error || "That didn't work.";
  $("go").disabled = false;
};
