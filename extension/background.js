// Envoyer à Inspi : clic droit sur une image, un texte sélectionné ou un lien.
// Lecture de l'auteur et récupération des octets dans la page adaptées de wiki-inspi (MIT, © 2026 Cynthia Ortu).
const DEFAULT_PORT = 18765;
const MAX_BYTES = 25 * 1024 * 1024;

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    const mk = (id, title, contexts) => chrome.contextMenus.create({ id, title, contexts }, () => void chrome.runtime.lastError);
    mk("inspi-main", "Envoyer à Inspi", ["page", "image", "video", "link", "frame"]);
    mk("inspi-frame", "Envoyer l'image affichée de la vidéo", ["page", "video", "frame"]);
    mk("inspi-txt", "Envoyer le texte à Inspi", ["selection"]);
    mk("inspi-link", "Envoyer le lien à Inspi", ["link"]);
  });
  autoPair().then((ok) => { if (!ok) chrome.runtime.openOptionsPage(); });
});

// Appairage automatique : le serveur Inspi donne son jeton à une extension qui l'appelle depuis ce PC.
async function autoPair() {
  const { port } = await chrome.storage.local.get("port");
  try {
    const r = await fetch(`http://127.0.0.1:${port || DEFAULT_PORT}/api/extension/token`, { method: "POST" });
    if (!r.ok) return false;
    const { token } = await r.json();
    if (token) { await chrome.storage.local.set({ token }); return true; }
  } catch (e) {}
  return false;
}
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
chrome.runtime.onStartup.addListener(() => { chrome.storage.local.get("token", ({ token }) => { if (!token) autoPair(); }); });
chrome.runtime.onMessage.addListener((msg, _s, reply) => { if (msg === "autoPair") { autoPair().then(reply); return true; } });

function notify(ok, message) {
  chrome.notifications.create({ type: "basic", iconUrl: "icon128.png",
    title: ok ? "Inspi : enregistré" : "Inspi : échec", message: String(message).slice(0, 300) });
}

// ---- exécuté DANS la page (doit être autonome) : octets de l'image avec les cookies de la page
async function pageFetchBytes(srcUrl, maxBytes) {
  try {
    const r = await fetch(srcUrl, { credentials: "include" });
    if (!r.ok) return { error: "HTTP " + r.status };
    const blob = await r.blob();
    if (blob.size > maxBytes) return { error: "image trop lourde" };
    const dataUrl = await new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = () => rej(fr.error); fr.readAsDataURL(blob); });
    return { dataUrl };
  } catch (e) { return { error: String(e) }; }
}

// ---- exécuté DANS la page : auteur / site / texte alternatif
function pageMetadata(srcUrl) {
  const meta = (sel) => (document.querySelector(sel) || {}).content || "";
  const host = location.hostname;
  let author = "";
  if (/(^|\.)pinterest\.[a-z.]+$/i.test(host)) {
    const c = document.querySelector('[data-test-id="creator-profile-name"]');
    if (c) author = c.textContent.replace(/\s+/g, " ").trim();
  }
  author = author || meta('meta[name="author"]') || meta('meta[property="article:author"]');
  const nameOf = (v) => !v ? "" : Array.isArray(v) ? nameOf(v[0]) : typeof v === "string" ? v : (v.name || "");
  const walk = (o) => {
    if (!o || typeof o !== "object") return "";
    if (Array.isArray(o)) { for (const x of o) { const r = walk(x); if (r) return r; } return ""; }
    return nameOf(o.creator) || nameOf(o.author) || walk(o["@graph"]);
  };
  if (!author) for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try { const a = walk(JSON.parse(s.textContent)); if (a) { author = a; break; } } catch (e) {}
  }
  if (/(^|\.)pinterest\.[a-z.]+$/i.test(host) && /^pinterest$/i.test(author.trim())) author = "";
  if (!author && /(^|\.)behance\.net$/.test(host)) {
    const owner = document.querySelector('.Project-owners a, [class*="ProjectInfo-owner"] a, a[class*="Owner"]');
    author = owner ? owner.textContent.trim() : (meta('meta[property="og:title"]').split(" by ").slice(1).pop() || "");
  }
  if (!author && /(^|\.)are\.na$/.test(host)) {
    const u = document.querySelector('[class*="Attribution"] a, a[href^="/"][class*="user" i]');
    if (u) author = u.textContent.trim();
  }
  if (!author && /(^|\.)wikimedia\.org$/.test(host)) {
    const label = [...document.querySelectorAll("#fileinfotable td, td.fileinfo-paramfield")].find((c) => /^(author|artist|creator|auteur|artiste)\b/i.test(c.textContent.trim()) && c.textContent.trim().length < 30);
    const cell = label && label.nextElementSibling;
    if (cell) author = cell.textContent.replace(/\s+/g, " ").trim().slice(0, 120);
  }
  let alt = "";
  if (srcUrl) { const img = [...document.images].find((i) => i.currentSrc === srcUrl || i.src === srcUrl); if (img) alt = img.alt || ""; }
  return { author: author.slice(0, 200), site_name: meta('meta[property="og:site_name"]').slice(0, 200),
           title: document.title || "", alt: alt.slice(0, 500), og_image: meta('meta[property="og:image"]') };
}

function toB64(buf) {
  const bytes = new Uint8Array(buf); let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

async function getBytes(info, tab) {
  const frameIds = info.frameId !== undefined ? [info.frameId] : [0];
  let pageErr = "";
  try { // 1) dans la page : cookies, blob:, data:
    const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id, frameIds }, func: pageFetchBytes, args: [info.srcUrl, MAX_BYTES] });
    if (res && res.result && res.result.dataUrl) return (res.result.dataUrl.split(",")[1] || "");
    pageErr = res && res.result && res.result.error || "";
  } catch (e) { pageErr = String(e); }
  if (/^blob:/.test(info.srcUrl)) throw new Error("image blob illisible (" + pageErr + ")");
  // 2) depuis l'extension (contourne CORS)
  const r = await fetch(info.srcUrl, { credentials: "include" });
  if (!r.ok) throw new Error("HTTP " + r.status);
  const buf = await r.arrayBuffer();
  if (buf.byteLength > MAX_BYTES) throw new Error("image trop lourde");
  return toB64(buf);
}

async function send(body) {
  let { token, port } = await chrome.storage.local.get(["token", "port"]);
  if (!token && await autoPair()) ({ token } = await chrome.storage.local.get("token"));
  if (!token) { chrome.runtime.openOptionsPage(); throw new Error("Inspi ne tourne pas : lance l'appli (icône Inspi du bureau)"); }
  const call = (tok) => fetch(`http://127.0.0.1:${port || DEFAULT_PORT}/api/capture-json`, {
      method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer " + tok },
      body: JSON.stringify({ source: "extension", ...body }) });
  let r;
  try {
    r = await call(token);
    if (r.status === 401 && await autoPair()) {  // jeton changé côté PC : on se reconnecte
      ({ token } = await chrome.storage.local.get("token"));
      r = await call(token);
    }
  } catch (e) { throw new Error("Inspi ne tourne pas sur ce PC : lance l'appli (icône Inspi du bureau)"); }
  const js = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(js.error || "HTTP " + r.status);
  return js;
}

function ask(tab, frameId, msg) {
  return new Promise((resolve) => {
    chrome.tabs.sendMessage(tab.id, msg, { frameId: frameId || 0 }, (r) => { void chrome.runtime.lastError; resolve(r || null); });
  });
}

async function ensureContent(tab, frameId) {
  // pages ouvertes avant l'installation de l'extension : on injecte le script à la volée
  const pong = await ask(tab, frameId, { cmd: "target" });
  if (pong !== null) return pong;
  try { await chrome.scripting.executeScript({ target: { tabId: tab.id, frameIds: [frameId || 0] }, files: ["content.js"] }); } catch (e) {}
  return null;
}

async function cropScreenshot(tab, rect, dpr) {
  // capture de l'onglet recadrée sur l'image ou la vidéo (quand la page bloque la lecture des pixels)
  const url = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
  const bmp = await createImageBitmap(await (await fetch(url)).blob());
  const x = Math.max(0, Math.round(rect.x * dpr)), y = Math.max(0, Math.round(rect.y * dpr));
  const w = Math.min(bmp.width - x, Math.round(rect.w * dpr)), h = Math.min(bmp.height - y, Math.round(rect.h * dpr));
  const c = new OffscreenCanvas(Math.max(1, w), Math.max(1, h));
  c.getContext("2d").drawImage(bmp, x, y, w, h, 0, 0, w, h);
  const blob = await c.convertToBlob({ type: "image/png" });
  return toB64(await blob.arrayBuffer());
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (!tab) return;
  const frameId = info.frameId || 0;
  let meta = { title: tab.title || "", author: "", site_name: "", alt: "" };
  try {
    const [m] = await chrome.scripting.executeScript({ target: { tabId: tab.id, frameIds: [0] }, func: pageMetadata, args: [info.srcUrl || ""] });
    if (m && m.result) meta = m.result;
  } catch (e) { /* page protégée : on garde le titre de l'onglet */ }
  let ctx = { url: info.pageUrl || tab.url || "", title: meta.title, author: meta.author, site_name: meta.site_name, alt: meta.alt };
  try {
    let js;
    if (info.menuItemId === "inspi-txt") {
      js = await send({ ...ctx, text: info.selectionText });
    } else if (info.menuItemId === "inspi-link") {
      js = await send({ ...ctx, text: info.linkUrl, url: info.linkUrl, title: "" });
    } else {
      let target = await ensureContent(tab, frameId);
      if (target && target.meta) ctx = { ...ctx, ...Object.fromEntries(Object.entries(target.meta).filter(([, v]) => v)) };
      if (target && target.alt && !ctx.alt) ctx.alt = target.alt;
      if (info.menuItemId === "inspi-frame") {
        if (!target || target.kind !== "video") throw new Error("pas de vidéo sous le curseur");
        let fr = await ask(tab, frameId, { cmd: "frame" });
        const b64 = fr && fr.b64 ? fr.b64 : await cropScreenshot(tab, target.rect, target.dpr);
        js = await send({ ...ctx, image_b64: b64, filename: "image-video.jpg" });
      } else if (target && target.kind === "video") {
        notify(true, "Enregistrement de la vidéo pendant sa lecture (60 s max). Clique sur le bandeau pour arrêter plus tôt.");
        const r = await ask(tab, frameId, { cmd: "record", maxSec: 60 });
        if (r && r.b64) {
          js = await send({ ...ctx, video_b64: r.b64, filename: r.mime && r.mime.includes("mp4") ? "video.mp4" : "video.webm" });
        } else {
          // enregistrement impossible : on garde au moins l'image affichée
          const b64 = await cropScreenshot(tab, target.rect, target.dpr);
          js = await send({ ...ctx, image_b64: b64, filename: "image-video.png", note: "Vidéo non enregistrable (" + ((r && r.error) || "erreur") + ") : image affichée gardée." });
        }
      } else if ((target && target.kind === "image" && target.src) || info.srcUrl) {
        const src = (target && target.kind === "image" && target.src) || info.srcUrl;
        const http = /^https?:\/\//i.test(src);
        try {
          const b64 = await getBytes({ ...info, srcUrl: src }, tab);
          js = await send({ ...ctx, image_b64: b64, image_url: http ? src : "", filename: (src.split(/[?#]/)[0].split("/").pop() || "image").slice(0, 80) });
        } catch (e) {
          if (target && target.rect) {
            js = await send({ ...ctx, image_b64: await cropScreenshot(tab, target.rect, target.dpr), filename: "image.png", image_url: http ? src : "" });
          } else if (http) {
            js = await send({ ...ctx, image_url: src });
          } else throw e;
        }
      } else if (info.linkUrl) {
        js = await send({ ...ctx, text: info.linkUrl, url: info.linkUrl, title: "" });
      } else {
        js = await send({ ...ctx, image_url: meta.og_image || "", text: ctx.url });
      }
    }
    notify(true, js.message || "Enregistré" + (ctx.author ? ` · ${ctx.author}` : ""));
  } catch (e) {
    notify(false, e && e.message ? e.message : String(e));
  }
});
