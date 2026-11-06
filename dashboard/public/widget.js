/**
 * selai web chat widget (vanilla JS, no dependencies).
 *
 * Usage — one line on any website, before </body>:
 *   <script src="https://www.selai.es/widget.js" data-selai="AGENCY_SLUG" async></script>
 *
 * Paints a floating chat button (bottom-right). Clicking it opens an iframe
 * to https://www.selai.es/chat/{slug} — the public chat page served by the
 * dashboard (src/app/chat/[slug]). The iframe is created lazily on first
 * open so the host page pays zero cost until the visitor wants to chat.
 *
 * (234) The panel paints the first-layer notice ("Chat atendido por IA ·
 * Política de privacidad") itself, before the iframe has loaded. The chat
 * page carries the same notice above its input, so the visitor sees it
 * either way; this one exists because the legal duty to inform lands the
 * moment the panel opens, and an iframe on someone else's slow site can
 * take a second or two — or never arrive at all. It is removed as soon as
 * the real chat is on screen, so nobody reads it twice.
 */
(function () {
  "use strict";

  var script =
    document.currentScript ||
    document.querySelector("script[data-selai]");
  if (!script) return;

  var slug = script.getAttribute("data-selai");
  if (!slug || window.__selaiWidgetLoaded) return;
  window.__selaiWidgetLoaded = true;

  var CHAT_ORIGIN = "https://www.selai.es";
  var chatUrl = CHAT_ORIGIN + "/chat/" + encodeURIComponent(slug);
  var Z = 2147483000;

  // ---- styles ------------------------------------------------------------
  var style = document.createElement("style");
  style.textContent =
    "#selai-widget-btn{position:fixed;bottom:20px;right:20px;z-index:" + Z + ";" +
    "width:56px;height:56px;border-radius:50%;border:none;cursor:pointer;" +
    "background:#5B61F0;color:#fff;box-shadow:0 8px 24px rgba(91,97,240,.45);" +
    "display:flex;align-items:center;justify-content:center;padding:0;" +
    "transition:transform .15s ease,box-shadow .15s ease;}" +
    "#selai-widget-btn:hover{transform:scale(1.06);box-shadow:0 10px 28px rgba(91,97,240,.55);}" +
    "#selai-widget-btn svg{width:26px;height:26px;display:block;}" +
    "#selai-widget-panel{position:fixed;bottom:88px;right:20px;z-index:" + Z + ";" +
    "width:380px;max-width:calc(100vw - 32px);height:600px;max-height:calc(100vh - 110px);" +
    "border-radius:16px;overflow:hidden;background:#fff;" +
    "box-shadow:0 24px 60px -12px rgba(11,13,23,.35);display:none;}" +
    "#selai-widget-panel.selai-open{display:block;}" +
    "#selai-widget-panel iframe{width:100%;height:100%;border:0;position:relative;z-index:1;}" +
    "#selai-widget-notice{position:absolute;left:0;right:0;bottom:0;z-index:0;" +
    "padding:10px 12px;background:#fff;color:#64748b;text-align:center;" +
    "font:400 12px/1.4 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;" +
    "border-top:1px solid #e2e8f0;}" +
    "#selai-widget-notice a{color:#5B61F0;}" +
    "@media (prefers-reduced-motion:reduce){#selai-widget-btn{transition:none;}}" +
    "@media (max-width:480px){#selai-widget-panel{right:8px;bottom:80px;width:calc(100vw - 16px);height:calc(100vh - 96px);}}" +
    // iOS/Android: 100vh incluye la barra del navegador; dvh sigue al viewport
    // visible para que el borde superior del panel nunca quede oculto.
    "@supports (height:100dvh){#selai-widget-panel{max-height:calc(100dvh - 110px);}" +
    "@media (max-width:480px){#selai-widget-panel{height:calc(100dvh - 96px);}}}";
  document.head.appendChild(style);

  // ---- button ------------------------------------------------------------
  var CHAT_ICON =
    '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
    '<path d="M4 6.5A3.5 3.5 0 0 1 7.5 3h9A3.5 3.5 0 0 1 20 6.5v6a3.5 3.5 0 0 1-3.5 3.5H9.6L5.7 19.2c-.7.5-1.7 0-1.7-.9V6.5Z" fill="currentColor"/>' +
    "</svg>";
  var CLOSE_ICON =
    '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
    '<path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/>' +
    "</svg>";

  var btn = document.createElement("button");
  btn.id = "selai-widget-btn";
  btn.type = "button";
  btn.setAttribute("aria-label", "Abrir el chat");
  btn.setAttribute("aria-expanded", "false");
  btn.setAttribute("aria-controls", "selai-widget-panel");
  btn.innerHTML = CHAT_ICON;

  var panel = document.createElement("div");
  panel.id = "selai-widget-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "true");
  panel.setAttribute("aria-hidden", "true");
  panel.setAttribute("aria-label", "Chat con la inmobiliaria");

  // (234) Deber de información antes del primer mensaje. Se pinta con el
  // panel, no cuando carga el iframe: si la web del cliente va lenta o el
  // iframe no llega a cargar, el aviso tiene que estar igualmente.
  var notice = document.createElement("p");
  notice.id = "selai-widget-notice";
  notice.innerHTML =
    'Chat atendido por IA · <a href="https://www.selai.es/privacidad" ' +
    'target="_blank" rel="noreferrer">Política de privacidad</a>';
  panel.appendChild(notice);

  var iframeCreated = false;
  var iframe = null;
  var open = false;

  function toggle() {
    open = !open;
    if (open && !iframeCreated) {
      iframeCreated = true;
      iframe = document.createElement("iframe");
      iframe.src = chatUrl;
      iframe.title = "Chat con la inmobiliaria";
      iframe.allow = "clipboard-write";
      iframe.loading = "lazy";
      iframe.setAttribute(
        "sandbox",
        "allow-forms allow-popups allow-same-origin allow-scripts"
      );
      // Una vez el chat real está en pantalla su propio aviso toma el
      // relevo; dejar los dos sería repetir lo mismo dos veces.
      iframe.addEventListener("load", function () {
        if (notice.parentNode) notice.parentNode.removeChild(notice);
      });
      panel.appendChild(iframe);
    }
    panel.className = open ? "selai-open" : "";
    panel.setAttribute("aria-hidden", open ? "false" : "true");
    btn.innerHTML = open ? CLOSE_ICON : CHAT_ICON;
    btn.setAttribute("aria-label", open ? "Cerrar el chat" : "Abrir el chat");
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      window.setTimeout(function () {
        if (iframe) iframe.focus();
      }, 0);
    } else {
      btn.focus();
    }
  }

  btn.addEventListener("click", toggle);
  document.addEventListener("keydown", function (event) {
    if (open && event.key === "Escape") toggle();
  });
  window.addEventListener("message", function (event) {
    if (
      open &&
      iframe &&
      event.source === iframe.contentWindow &&
      event.data &&
      event.data.type === "selai:close"
    ) {
      toggle();
    }
  });

  function mount() {
    document.body.appendChild(panel);
    document.body.appendChild(btn);
  }

  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount);
})();
