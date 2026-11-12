// One "load more" step, run by the browser collector every 2-5 seconds.
// It clicks at most a few buttons whose whole text matches the selectors file ("View more
// comments", "View 3 replies"), then scrolls. It never touches forms, text boxes or anything
// that could like, comment, share, post, follow or report.
// Python fills in __SETTINGS__. The result, as JSON text (Qt for Python doesn't pass objects
// back), tells Python what happened.
(function (settings) {
  "use strict";
  const DENY = /^(like|liked|unlike|react|reply|comment|share|send|post|follow|following|unfollow|save|saved|report|block|delete|edit|hide|message|add friend|join|subscribe)$/i;
  const body = document.body;
  const text = body ? body.innerText || "" : "";
  for (const wall of settings.wallTexts) {
    if (text.indexOf(wall) !== -1) return JSON.stringify({ clicked: 0, wall: wall });
  }
  const patterns = settings.buttons.map(function (p) { return new RegExp(p, "i"); });
  const clicked = [];
  const candidates = document.querySelectorAll(
    'button, [role="button"], a[role="link"], div[role="link"], span[dir="auto"]'
  );
  for (let i = 0; i < candidates.length && clicked.length < settings.maxClicks; i++) {
    const element = candidates[i];
    const label = (element.innerText || "").trim();
    if (!label || label.length > 60 || DENY.test(label)) continue;
    if (!patterns.some(function (p) { return p.test(label); })) continue;
    if (element.closest('form, textarea, input, [contenteditable="true"]')) continue;
    if (clicked.some(function (c) { return c.contains(element) || element.contains(c); })) continue;
    const box = element.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) continue;
    element.click();
    clicked.push(element);
  }
  if (settings.scroll) {
    window.scrollBy(0, Math.round(window.innerHeight * 0.8));
    const boxes = document.querySelectorAll("div, section, ul");
    for (let i = 0; i < boxes.length; i++) {
      const box = boxes[i];
      if (box.scrollHeight > box.clientHeight + 50 && box.clientHeight > 150) {
        const style = getComputedStyle(box);
        if (style.overflowY === "auto" || style.overflowY === "scroll") {
          box.scrollTop += Math.round(box.clientHeight * 0.8);
        }
      }
    }
  }
  return JSON.stringify({ clicked: clicked.length, wall: null });
})(__SETTINGS__);
