ANNOTATION_JS = """
() => {
    const SELECTOR = [
        'button',
        'a[href]',
        'input:not([type="hidden"])',
        'textarea',
        'select',
        '[role="button"]',
        '[role="link"]',
        '[role="checkbox"]',
        '[role="menuitem"]',
        '[role="option"]',
        '[role="tab"]',
        '[contenteditable="true"]',
    ].join(', ');

    let idCounter = 1;

    // ── deep clean ── removes all leftover annotations from light DOM,
    //                  shadow roots, and accessible iframe documents
    function deepClean(root) {
        root.querySelectorAll('.llm-annotation-box').forEach(el => el.remove());
        root.querySelectorAll('[data-llm-id]').forEach(el => {
            el.removeAttribute('data-llm-id');
            el.removeAttribute('data-llm-frame');
        });
        root.querySelectorAll('[data-llm-frame-id]').forEach(el => {
            el.removeAttribute('data-llm-frame-id');
        });
        root.querySelectorAll('*').forEach(el => {
            if (el.shadowRoot) deepClean(el.shadowRoot);
        });
    }

    // ── annotate ── tags every interactive element in `root` and draws a
    //               fixed-position overlay box in the main document.
    //
    //   frameId    — null for main doc; 'frame-N' for iframe content
    //   offsetTop  — iframe's top in main-doc coordinates (0 for main doc)
    //   offsetLeft — iframe's left in main-doc coordinates (0 for main doc)
    function annotateRoot(root, frameId, offsetTop, offsetLeft) {
        root.querySelectorAll(SELECTOR).forEach(el => {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return;

            // Convert to main-document viewport coordinates
            const absTop    = offsetTop  + rect.top;
            const absLeft   = offsetLeft + rect.left;
            const absBottom = absTop + rect.height;

            // Skip elements entirely outside the main viewport
            if (absBottom <= 0 || absTop >= window.innerHeight) return;

            el.setAttribute('data-llm-id', idCounter);
            if (frameId) el.setAttribute('data-llm-frame', frameId);

            // Clip the overlay box to the visible portion of the viewport
            const visibleTop    = Math.max(0, absTop);
            const visibleBottom = Math.min(window.innerHeight, absBottom);

            // Boxes always live in the main document body so they overlay correctly
            const box = document.createElement('div');
            box.className = 'llm-annotation-box';
            box.style.cssText = [
                'position:fixed',
                `left:${absLeft}px`,
                `top:${visibleTop}px`,
                `width:${rect.width}px`,
                `height:${visibleBottom - visibleTop}px`,
                'border:2px solid #FF3B30',
                'z-index:2147483647',
                'pointer-events:none',
                'box-sizing:border-box',
            ].join(';');

            const label = document.createElement('span');
            label.textContent = idCounter;
            label.style.cssText = [
                'position:absolute',
                'background:#FF3B30',
                'color:white',
                'font-size:11px',
                'font-weight:bold',
                'font-family:monospace',
                'padding:1px 3px',
                'top:-18px',
                'left:-2px',
                'border-radius:2px',
                'white-space:nowrap',
                'line-height:1.4',
            ].join(';');

            box.appendChild(label);
            document.body.appendChild(box);
            idCounter++;
        });

        // Recurse into shadow roots, inheriting the same coordinate offset
        root.querySelectorAll('*').forEach(el => {
            if (el.shadowRoot) annotateRoot(el.shadowRoot, frameId, offsetTop, offsetLeft);
        });
    }

    // ── 1. Clean everything ──────────────────────────────────────────────
    deepClean(document);
    document.querySelectorAll('iframe').forEach(iframeEl => {
        try { deepClean(iframeEl.contentDocument); } catch (e) { /* cross-origin */ }
    });

    // ── 2. Annotate main document ────────────────────────────────────────
    annotateRoot(document, null, 0, 0);

    // ── 3. Annotate same-origin iframes ─────────────────────────────────
    let frameCounter = 1;
    document.querySelectorAll('iframe').forEach(iframeEl => {
        try {
            const doc = iframeEl.contentDocument;
            if (!doc || !doc.body) return;

            const iframeRect = iframeEl.getBoundingClientRect();
            // Skip iframes that are entirely outside the main viewport
            if (iframeRect.bottom <= 0 || iframeRect.top >= window.innerHeight) return;

            const frameId = `frame-${frameCounter++}`;
            iframeEl.setAttribute('data-llm-frame-id', frameId);
            annotateRoot(doc, frameId, iframeRect.top, iframeRect.left);
        } catch (e) {
            /* cross-origin iframe — skip silently */
        }
    });

    return idCounter - 1;
}
"""

CLEANUP_JS = """
() => {
    // Overlay boxes are always in the main document body
    document.querySelectorAll('.llm-annotation-box').forEach(el => el.remove());

    function cleanRoot(root) {
        root.querySelectorAll('[data-llm-id]').forEach(el => {
            el.removeAttribute('data-llm-id');
            el.removeAttribute('data-llm-frame');
        });
        root.querySelectorAll('[data-llm-frame-id]').forEach(el => {
            el.removeAttribute('data-llm-frame-id');
        });
        root.querySelectorAll('*').forEach(el => {
            if (el.shadowRoot) cleanRoot(el.shadowRoot);
        });
    }

    cleanRoot(document);

    // Also clean accessible iframes
    document.querySelectorAll('iframe').forEach(iframeEl => {
        try { cleanRoot(iframeEl.contentDocument); } catch (e) { /* cross-origin */ }
    });

    return true;
}
"""
