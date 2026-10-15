const $ = (id) => document.getElementById(id);
window.setup.prefill().then((q) => {
  $("server").value = q.server || "";
  $("code").value = q.code || "";
  $("name").value = q.name || "";
  $("msg").textContent = q.message || "";
});
$("f").onsubmit = async (e) => {
  e.preventDefault();
  $("go").disabled = true;
  $("err").textContent = "";
  const r = await window.setup.enrol({ server: $("server").value, code: $("code").value, name: $("name").value });
  if (r.ok) { $("go").textContent = "Added. Opening the workspace…"; return; }
  $("err").textContent = r.error || "That didn't work.";
  $("go").disabled = false;
};
