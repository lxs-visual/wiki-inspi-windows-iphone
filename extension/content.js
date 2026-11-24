// Inspi : repère l'image ou la vidéo sous le curseur au clic droit, même cachée sous un calque
// (Instagram, Pinterest, Behance…), lit le profil Instagram et enregistre les vidéos pendant leur lecture.
(() => {
  if (window.__inspiReady) return;
  window.__inspiReady = true;
  let last = null; // { el, kind, x, y }

  function visible(el) {
    const r = el.getBoundingClientRect();
    return r.width > 40 && r.height > 40;
  }

  function findMedia(x, y) {
    const stack = document.elementsFromPoint(x, y);
    let img = null;
    for (const el of stack) {
      if (el.tagName === "VIDEO" && visible(el)) return { el, kind: "video" };
      if (!img && el.tagName === "IMG" && visible(el)) img = el;
    }
    if (img) return { el: img, kind: "image" };
    // vidéo ou image placée dans le même bloc mais pas exactement sous le curseur (calques Instagram)
    for (const el of stack.slice(0, 6)) {
      const v = el.querySelector && el.querySelector("video");
      if (v && visible(v)) return { el: v, kind: "video" };
      const i = el.querySelector && el.querySelector("img");
      if (i && visible(i) && i.naturalWidth > 150) return { el: i, kind: "image" };
    }
    return null;
  }

  document.addEventListener("contextmenu", (e) => {
    const m = findMedia(e.clientX, e.clientY);
    last = m ? { ...m, x: e.clientX, y: e.clientY } : null;
  }, true);

  // ---------- Instagram : profil et lien du post ----------
  function igMeta(el) {
    if (!/(^|\.)instagram\.com$/.test(location.hostname)) return {};
    const box = (el && (el.closest("article") || el.closest('[role="dialog"]') || el.closest("main"))) || document;
    const RESERVED = new Set(["explore", "reels", "reel", "p", "stories", "direct", "accounts", "about", "legal", "tv", "audio"]);
    let user = "";
    for (const a of box.querySelectorAll('a[href^="/"]')) {
      const m = (a.getAttribute("href") || "").match(/^\/([A-Za-z0-9._]{1,30})\/?$/);
      const txt = (a.textContent || "").trim();
      if (m && !RESERVED.has(m[1].toLowerCase()) && (txt === m[1] || txt === "" || txt.startsWith(m[1]))) { user = m[1]; break; }
    }
    if (!user) {
      const t = (document.querySelector('meta[property="og:title"]') || {}).content || "";
      const m = t.match(/\(@([A-Za-z0-9._]+)\)/) || t.match(/@([A-Za-z0-9._]+)/);
      if (m) user = m[1];
    }
    let post = "";
    const link = box.querySelector('a[href*="/p/"], a[href*="/reel/"], a[href*="/reels/"]');
    if (/\/(p|reel|reels)\//.test(location.pathname)) post = location.origin + location.pathname;
    else if (link) post = new URL(link.getAttribute("href"), location.origin).href.split("?")[0];
    return user ? { author: "@" + user, author_url: `https://www.instagram.com/${user}/`, url: post || location.href, site_name: "Instagram" }
                : (post ? { url: post, site_name: "Instagram" } : {});
  }

  // ---------- enregistrement d'une vidéo pendant sa lecture ----------
  function overlay(text) {
    let o = document.getElementById("__inspi_rec");
    if (!o) {
      o = document.createElement("div");
      o.id = "__inspi_rec";
      o.style.cssText = "position:fixed;top:16px;right:16px;z-index:2147483647;background:#111;color:#fff;font:600 14px system-ui;padding:10px 16px;border-radius:999px;box-shadow:0 6px 30px rgba(0,0,0,.4);cursor:pointer;border:2px solid #ff5c35";
      document.documentElement.appendChild(o);
    }
    o.textContent = text;
    return o;
  }
  const fmt = (s) => Math.floor(s / 60) + ":" + String(Math.floor(s % 60)).padStart(2, "0");

  function toB64(blob) {
    return new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(String(fr.result).split(",")[1] || ""); fr.onerror = () => rej(fr.error); fr.readAsDataURL(blob); });
  }

  async function record(v, maxSec) {
    // 1) fichier direct (pas un flux « blob: ») : on récupère l'original, en pleine qualité
    const src = v.currentSrc || v.src || (v.querySelector("source") || {}).src || "";
    if (/^https?:/.test(src)) {
      try {
        const r = await fetch(src, { credentials: "include" });
        if (r.ok) {
          const b = await r.blob();
          if (b.size > 0 && b.size < 500 * 1024 * 1024 && /video|octet/.test(b.type || "video")) return { b64: await toB64(b), mime: b.type || "video/mp4", direct: true };
        }
      } catch (e) { /* on enregistre la lecture */ }
    }
    // 2) enregistrement de la lecture (Instagram, flux découpés…)
    let stream;
    try { stream = v.captureStream ? v.captureStream() : v.mozCaptureStream(); }
    catch (e) { return { error: "cette vidéo bloque l'enregistrement (" + e.name + ")" }; }
    const types = ["video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm"];
    const mime = types.find((t) => window.MediaRecorder && MediaRecorder.isTypeSupported(t));
    if (!mime) return { error: "enregistrement vidéo non pris en charge par ce navigateur" };
    const rec = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 8000000 });
    const chunks = [];
    rec.ondataavailable = (e) => e.data && e.data.size && chunks.push(e.data);
    const total = isFinite(v.duration) && v.duration > 0 ? Math.min(v.duration, maxSec) : maxSec;
    try { v.currentTime = 0; } catch (e) {}
    try { await v.play(); } catch (e) {}
    rec.start(500);
    const t0 = performance.now();
    return await new Promise((resolve) => {
      let done = false;
      const o = overlay(`● Inspi enregistre 0:00 / ${fmt(total)} · clic pour arrêter`);
      const stop = () => {
        if (done) return; done = true;
        clearInterval(tick); v.removeEventListener("ended", stop); o.onclick = null;
        rec.onstop = async () => {
          o.textContent = "Inspi : envoi de la vidéo…";
          const blob = new Blob(chunks, { type: mime.split(";")[0] });
          setTimeout(() => o.remove(), 1500);
          if (!blob.size) return resolve({ error: "rien n'a été enregistré" });
          resolve({ b64: await toB64(blob), mime: "video/webm", seconds: Math.round((performance.now() - t0) / 1000) });
        };
        rec.stop();
      };
      const tick = setInterval(() => {
        const s = (performance.now() - t0) / 1000;
        o.textContent = `● Inspi enregistre ${fmt(s)} / ${fmt(total)} · clic pour arrêter`;
        if (s >= total + 0.3) stop();
      }, 250);
      o.onclick = stop;
      v.addEventListener("ended", stop);
    });
  }

  function frameOf(v) {
    try {
      const c = document.createElement("canvas");
      c.width = v.videoWidth; c.height = v.videoHeight;
      c.getContext("2d").drawImage(v, 0, 0);
      return { b64: c.toDataURL("image/jpeg", 0.93).split(",")[1] };
    } catch (e) { return { error: "tainted" }; }
  }

  chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
    if (msg.cmd === "target") {
      if (!last || !last.el.isConnected) return reply(null);
      const el = last.el, r = el.getBoundingClientRect();
      const src = el.tagName === "IMG" ? (el.currentSrc || el.src) : (el.currentSrc || el.src || "");
      reply({ kind: last.kind, src, poster: el.poster || "", alt: el.alt || "", rect: { x: r.x, y: r.y, w: r.width, h: r.height },
              dpr: window.devicePixelRatio || 1, meta: igMeta(el) });
      return;
    }
    if (msg.cmd === "record") {
      if (!last || last.kind !== "video") return reply({ error: "pas de vidéo sous le curseur" });
      record(last.el, msg.maxSec || 60).then(reply, (e) => reply({ error: String(e) }));
      return true; // réponse asynchrone
    }
    if (msg.cmd === "frame") {
      if (!last || last.kind !== "video") return reply({ error: "pas de vidéo" });
      return reply(frameOf(last.el));
    }
  });
})();
