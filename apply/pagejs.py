"""ONE definition of the page facts every field scanner shares.

Two scanners decide what a form control is:

  * ``apply/engine.py`` -- ``EXTRACT_JS`` (deterministic rules engine)
  * ``apply/agent.py`` -- ``SNAP_JS`` (LLM navigator)

They used to carry their own copies of the same four rules, and the copies drifted. Last night
(two Oracle HCM jobs) ``SNAP_JS`` and ``EXTRACT_JS`` disagreed about a visible
``name="honey-pot"`` robot trap and about the 0x0 consent checkbox: the deterministic scanner had
no trap wording for ``honey-pot`` (only ``honeypot``) and dropped every 0-size control, so the
checkbox could never be ticked; the navigator's wording list missed the hyphenated name entirely.

This module is the single source for:

  1. TRAP WORDING -- a robot trap is excluded by wording alone (any control type).
  2. VISIBILITY TEST -- what "visible" means (rect, display, visibility, opacity).
  3. LABEL RESOLUTION -- own label first (for=/aria/wrapping label/placeholder/title), then one
                            unambiguous nearby label; never guess between several.
  4. HIDDEN-BUT-REAL -- a styled checkbox/radio/file is legitimately hidden (Oracle JET
                            ``input-row__hidden-control``); a hidden text box is noise. Geometry
                            therefore only drops text-like controls, and a hidden checkbox/radio is
                            kept only when it has a label a human could click.

``HELPERS_JS`` is a JS fragment meant to be embedded at the top of a `` => { ... }`` page
function. ``engine.EXTRACT_JS`` is built from it. The exact patch that makes ``agent.SNAP_JS``
read the same fragment is in the task report (agent.py is owned by another agent); until that lands
the navigator keeps its private copy, and ``tests/test_pagejs.py`` pins the shared rules.

Pure data + strings: no browser, no network, no files.
"""
import re

# --------------------------------------------------------------------------- robot-trap wording
# Union of the two scanners' old lists, with the broad bare "honey" tightened to honey-pot
# (Oracle HCM names the trap `honey-pot`; "honey" alone would also match a company label).
TRAP_RX_SRC = (
    r"robots? only"
    r"|for robots"
    r"|do not enter if you"
    r"|honey[-\s_]?pots?"
    r"|bot[-\s]?traps?"
    r"|captcha"
    r"|leave (?:this|it)(?: field)? blank"
    r"|do not fill"
    r"|^url2$"
)

# Python mirror (tests and Python-side checks); the JS below compiles the same source.
TRAP_RX = re.compile(TRAP_RX_SRC, re.I)


def is_trap_text(text: str) -> bool:
    """True when an element's attribute/label text carries robot-trap wording (single definition)."""
    return bool(TRAP_RX.search(str(text or "")))


# --------------------------------------------------------------------------- what "visible" means
VIS_JS = r"""
  const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.visibility !== 'collapse'
      && s.display !== 'none' && s.opacity !== '0'; };
"""

# --------------------------------------------------------------------------- label resolution
# A box's OWN label first (for=, aria, wrapping label, placeholder); only then a nearby label that
# does not belong to another input. Several candidate labels nearby -> do not guess.
LAB_JS = r"""
  const clean = t => (t || '').replace(/\s+/g, ' ').trim().slice(0, 220);
  // Web-component forms (SmartRecruiters: <spl-input label="First name"> with the real <input> inside its shadow root, and
  // <spl-button>Next</spl-button> with an empty <button> inside): the name lives on the HOST element, which no label lookup reached. Every field
  // was listed as "spl-form-element_12", the model typed the city into Country/Region, and all text-less buttons collapsed into one, so "Next"
  // was never offered. 22 attempts on 10 companies, 0 sent.
  const hostOf = e => { const r = e.getRootNode ? e.getRootNode() : null; return r && r.host ? r.host : null; };
  const hostLab = e => { let h = hostOf(e);
    for (let i = 0; i < 3 && h; i++) {
      if (h.shadowRoot && h.shadowRoot.querySelectorAll('input,select,textarea').length > 1) return '';   // a host of several boxes names none of them
      const t = h.getAttribute('label') || h.getAttribute('aria-label') || '';
      if (t.trim()) return t;
      h = hostOf(h); }
    return ''; };
  const hostText = e => { const h = hostOf(e);
    if (!h || h.shadowRoot.querySelectorAll('button,a,[role=button],[role=option]').length !== 1) return '';
    return (h.getAttribute('aria-label') || (h.innerText != null ? h.innerText : h.textContent) || '').replace(/\s+/g, ' ').trim().slice(0, 80); };
  const lab = e => { let t = '';
    if (e.id) { const l = document.querySelector('label[for="' + CSS.escape(e.id) + '"]'); if (l) t = l.innerText; }
    if (!t && e.id && hostOf(e)) { const l = e.getRootNode().querySelector('label[for="' + CSS.escape(e.id) + '"]'); if (l) t = l.innerText; }
    if (!t && e.getAttribute('aria-label')) t = e.getAttribute('aria-label');
    if (!t && e.getAttribute('aria-labelledby')) t = e.getAttribute('aria-labelledby').split(' ').map(i => (document.getElementById(i) || {}).innerText || '').join(' ');
    if (!t) { const l = e.closest('label'); if (l && l.querySelectorAll('input,select,textarea').length <= 1) t = l.innerText; }
    if (!t) t = hostLab(e);
    if (!t && e.placeholder && e.type !== 'radio' && e.type !== 'checkbox') t = e.placeholder;
    if (!t && e.title) t = e.title;
    if (!t) { let p = e.parentElement; for (let i = 0; i < 3 && p && !t; i++) {
        // KB-65: `[class*=label]` can match an SVG (no innerText -> `.trim` of undefined killed the whole snapshot); read text safely
        const txtl = l => (l.innerText != null ? l.innerText : l.textContent) || '';
        const cands = [...p.querySelectorAll('label,legend,[class*=label]')].filter(l => !l.contains(e) && !l.querySelector('input,select,textarea') && !l.htmlFor && txtl(l).trim());
        if (cands.length === 1) t = cands[0].innerText;   // ambiguous (several labels nearby) -> don't guess
        p = p.parentElement; } }
    return clean(t || e.name || e.id); };
"""

# --------------------------------------------------------------------------- trap + hidden-but-real
TRAP_AND_HIDDEN_JS = r"""
  const TRAP_RX = /""" + TRAP_RX_SRC + r"""/i;
  const realLab = e => !!((e.id && document.querySelector('label[for="' + CSS.escape(e.id) + '"]')) || e.getAttribute('aria-label') ||
                           e.getAttribute('aria-labelledby') || e.closest('label'));
  const trapBlob = e => [e.name, e.id, (typeof e.className === 'string' ? e.className : ''),
    e.getAttribute('aria-label'), e.getAttribute('title'), e.placeholder,
    (e.labels && e.labels[0] && e.labels[0].innerText) || '', lab(e)].join(' ');
  // Robot traps are excluded by WORDING only, never by shape: Oracle's trap is a VISIBLE 184x38 box.
  const isTrap = e => TRAP_RX.test(trapBlob(e));
  // A hidden control is kept only when it is a styled upload/checkbox/radio a human could operate.
  const keepHidden = (e, type) => type === 'file' || ((type === 'checkbox' || type === 'radio') && realLab(e));
"""

# Everything a scanner needs, in declaration order (lab must exist before trapBlob uses it).
HELPERS_JS = VIS_JS + LAB_JS + TRAP_AND_HIDDEN_JS
