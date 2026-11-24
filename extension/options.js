const $ = id => document.getElementById(id);
chrome.storage.local.get(["token", "port"], ({ token, port }) => { $("token").value = token || ""; $("port").value = port || 18765; if (token) test(); });
$("save").onclick = async () => {
  await chrome.storage.local.set({ token: $("token").value.trim(), port: parseInt($("port").value, 10) || 18765 });
  test();
};
$("auto").onclick = async () => {
  await chrome.storage.local.set({ port: parseInt($("port").value, 10) || 18765 });
  const ok = await chrome.runtime.sendMessage("autoPair");
  if (!ok) { $("msg").textContent = "✗ Inspi ne répond pas : lance l'appli (icône Inspi du bureau) puis réessaie."; return; }
  chrome.storage.local.get("token", ({ token }) => { $("token").value = token; test(); });
};
$("test").onclick = () => test();
async function test() {
  const { token, port } = await chrome.storage.local.get(["token", "port"]);
  try {
    const r = await fetch(`http://127.0.0.1:${port || 18765}/api/status`, { headers: { Authorization: "Bearer " + token } });
    const j = await r.json();
    $("msg").textContent = r.ok ? `✓ Connecté à Inspi · IA : ${j.ai_label || j.model} (${j.ollama ? "prête" : j.ollama_msg})` : "✗ " + (j.error || r.status);
  } catch (e) { $("msg").textContent = "✗ Inspi ne tourne pas sur ce PC"; }
}
