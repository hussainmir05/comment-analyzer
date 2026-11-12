// Injected at document creation into collection pages only, never into login pages.
// Hands the app the API responses the page itself receives, when their address matches the
// site's selectors file. It reads nothing else: no cookies, no form fields, no storage.
// Python fills in __PATTERNS__ (a list of regular expressions) and __DOCUMENT_JSON__.
(function () {
  "use strict";
  if (window.__commentAnalyzerCapture) return;
  window.__commentAnalyzerCapture = true;

  const PATTERNS = __PATTERNS__.map(function (p) { return new RegExp(p); });
  const DOCUMENT_JSON = __DOCUMENT_JSON__;
  const MAX_BODY = 8 * 1024 * 1024;
  let bridge = null;
  const waiting = [];

  function wanted(url) {
    return PATTERNS.some(function (p) { return p.test(url); });
  }

  function send(url, status, body) {
    if (typeof body !== "string" || body.length > MAX_BODY || !wanted(url)) return;
    const message = JSON.stringify({ kind: "response", url: url, status: status, body: body });
    if (bridge) bridge.capture(message);
    else if (waiting.length < 50) waiting.push(message);
  }

  new QWebChannel(qt.webChannelTransport, function (channel) {
    bridge = channel.objects.capture;
    while (waiting.length) bridge.capture(waiting.shift());
  });

  const realFetch = window.fetch;
  window.fetch = function () {
    const args = arguments;
    return realFetch.apply(this, args).then(function (response) {
      try {
        const url = response.url || String((args[0] && args[0].url) || args[0]);
        if (wanted(url)) {
          response.clone().text().then(function (body) { send(url, response.status, body); },
                                       function () {});
        }
      } catch (e) { /* never break the page */ }
      return response;
    });
  };

  const realOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__commentAnalyzerUrl = String(url);
    return realOpen.apply(this, arguments);
  };
  const realSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function () {
    const xhr = this;
    xhr.addEventListener("load", function () {
      try {
        if (xhr.responseType === "" || xhr.responseType === "text") {
          send(xhr.responseURL || xhr.__commentAnalyzerUrl, xhr.status, xhr.responseText);
        }
      } catch (e) { /* never break the page */ }
    });
    return realSend.apply(this, arguments);
  };

  // Some sites (Facebook) put the first comments in the page itself, as JSON script blocks.
  if (DOCUMENT_JSON) {
    document.addEventListener("DOMContentLoaded", function () {
      const blocks = document.querySelectorAll('script[type="application/json"]');
      for (let i = 0; i < blocks.length; i++) {
        send("document:" + location.href, 200, blocks[i].textContent);
      }
    });
  }
})();
