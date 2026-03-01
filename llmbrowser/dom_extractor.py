DOM_EXTRACTION_JS = """
() => {
    const MAX_TEXT_LENGTH = 80;
    const SKIP_TAGS = new Set([
        'script', 'style', 'noscript', 'svg', 'path',
        'meta', 'link', 'head',
    ]);
    const INTERACTIVE_TAGS = new Set([
        'a', 'button', 'input', 'textarea', 'select', 'option', 'label',
    ]);
    const STRUCTURAL_TAGS = new Set([
        'main', 'nav', 'header', 'footer', 'section',
        'article', 'aside', 'form', 'table', 'ul', 'ol',
    ]);

    function truncate(text) {
        if (!text) return '';
        text = text.trim().replace(/\\s+/g, ' ');
        return text.length > MAX_TEXT_LENGTH ? text.slice(0, MAX_TEXT_LENGTH) + '...' : text;
    }

    function getElementInfo(el) {
        const tag  = el.tagName.toLowerCase();
        const info = { tag };

        const llmId = el.getAttribute('data-llm-id');
        if (llmId) info.id = parseInt(llmId);

        if (el.getAttribute('href'))        info.href        = truncate(el.getAttribute('href'));
        if (el.getAttribute('type'))        info.type        = el.getAttribute('type');
        if (el.getAttribute('placeholder')) info.placeholder = el.getAttribute('placeholder');
        if (el.getAttribute('aria-label'))  info.aria_label  = el.getAttribute('aria-label');
        if (el.getAttribute('name'))        info.name        = el.getAttribute('name');
        if (el.getAttribute('role'))        info.role        = el.getAttribute('role');
        if (el.getAttribute('data-llm-frame-id')) info.frame_id = el.getAttribute('data-llm-frame-id');

        if (tag === 'input' || tag === 'textarea') {
            info.value    = truncate(el.value);
            info.disabled = el.disabled;
        }
        if (tag === 'select') {
            const opt = el.options[el.selectedIndex];
            info.selected = opt ? opt.text : '';
        }

        const directText = Array.from(el.childNodes)
            .filter(n => n.nodeType === Node.TEXT_NODE)
            .map(n => n.textContent.trim())
            .join(' ');
        if (directText) info.text = truncate(directText);

        return info;
    }

    function buildTree(el, depth = 0) {
        if (depth > 12) return null;

        const tag = el.tagName ? el.tagName.toLowerCase() : '';
        if (SKIP_TAGS.has(tag)) return null;

        // Prune invisible elements (keep annotated ones — they may be in shadow DOM or iframes)
        if (el.offsetWidth === 0 && el.offsetHeight === 0 && !el.getAttribute('data-llm-id')) return null;

        const isInteractive = INTERACTIVE_TAGS.has(tag)
            || el.getAttribute('role') === 'button'
            || el.getAttribute('data-llm-id');
        const isStructural  = STRUCTURAL_TAGS.has(tag) || tag === 'iframe';

        if (!isInteractive && !isStructural && depth > 3) return null;

        const node     = getElementInfo(el);
        const children = [];

        // Light DOM children
        for (const child of el.children) {
            const childNode = buildTree(child, depth + 1);
            if (childNode) children.push(childNode);
        }

        // Shadow DOM children
        if (el.shadowRoot) {
            for (const child of el.shadowRoot.children) {
                const childNode = buildTree(child, depth + 1);
                if (childNode) children.push(childNode);
            }
        }

        // Same-origin iframe content — recurse into the iframe document body
        if (tag === 'iframe' && depth < 8) {
            try {
                const doc = el.contentDocument;
                if (doc && doc.body) {
                    const iframeBodyNode = buildTree(doc.body, depth + 1);
                    if (iframeBodyNode) {
                        iframeBodyNode.tag = 'iframe-content';
                        children.push(iframeBodyNode);
                    }
                }
            } catch (e) {
                // Cross-origin — note it but don't recurse
                children.push({ tag: 'iframe-content', text: '(cross-origin, not accessible)' });
            }
        }

        if (children.length > 0) node.children = children;
        return node;
    }

    function treeToText(node, indent = 0) {
        if (!node) return '';
        const pad  = '  '.repeat(indent);
        let   line = pad + `<${node.tag}`;

        if (node.id !== undefined)  line += ` [id=${node.id}]`;
        if (node.frame_id)          line += ` [frame=${node.frame_id}]`;
        if (node.type)              line += ` type="${node.type}"`;
        if (node.name)              line += ` name="${node.name}"`;
        if (node.role)              line += ` role="${node.role}"`;
        if (node.aria_label)        line += ` aria-label="${node.aria_label}"`;
        if (node.placeholder)       line += ` placeholder="${node.placeholder}"`;
        if (node.href)              line += ` href="${node.href}"`;
        if (node.value)             line += ` value="${node.value}"`;
        if (node.selected)          line += ` selected="${node.selected}"`;
        if (node.disabled)          line += ` disabled`;
        line += node.text ? `> ${node.text}` : '>';

        let result = line + '\\n';
        if (node.children) {
            for (const child of node.children) {
                result += treeToText(child, indent + 1);
            }
        }
        return result;
    }

    const tree = buildTree(document.body);
    return treeToText(tree);
}
"""

INTERACTIVE_ELEMENTS_JS = """
() => {
    const result = [];

    // offsetTop/offsetLeft: the iframe's position in main-doc coordinates
    function collectFrom(root, frameId, offsetTop, offsetLeft) {
        root.querySelectorAll('[data-llm-id]').forEach(el => {
            const rect    = el.getBoundingClientRect();
            const absTop  = offsetTop  + rect.top;
            const absLeft = offsetLeft + rect.left;

            result.push({
                id:          parseInt(el.getAttribute('data-llm-id')),
                tag:         el.tagName.toLowerCase(),
                type:        el.getAttribute('type') || null,
                text:        (el.innerText || el.value || el.placeholder || '').trim().slice(0, 60),
                aria_label:  el.getAttribute('aria-label') || null,
                name:        el.getAttribute('name') || null,
                href:        el.getAttribute('href') || null,
                role:        el.getAttribute('role') || null,
                placeholder: el.getAttribute('placeholder') || null,
                frame_id:    frameId || null,
                rect: {
                    x:      Math.round(absLeft),
                    y:      Math.round(absTop),
                    width:  Math.round(rect.width),
                    height: Math.round(rect.height),
                },
                // True if any part of the element is within the main viewport
                in_viewport: (
                    absTop  + rect.height > 0 && absTop  < window.innerHeight &&
                    absLeft + rect.width  > 0 && absLeft < window.innerWidth
                ),
            });
        });

        // Recurse into shadow roots (inheriting the same coordinate offset)
        root.querySelectorAll('*').forEach(el => {
            if (el.shadowRoot) collectFrom(el.shadowRoot, frameId, offsetTop, offsetLeft);
        });
    }

    // Main document
    collectFrom(document, null, 0, 0);

    // Same-origin iframes
    document.querySelectorAll('[data-llm-frame-id]').forEach(iframeEl => {
        try {
            const doc = iframeEl.contentDocument;
            if (!doc) return;
            const frameId    = iframeEl.getAttribute('data-llm-frame-id');
            const iframeRect = iframeEl.getBoundingClientRect();
            collectFrom(doc, frameId, iframeRect.top, iframeRect.left);
        } catch (e) {
            /* cross-origin — skip */
        }
    });

    result.sort((a, b) => a.id - b.id);
    return result;
}
"""
