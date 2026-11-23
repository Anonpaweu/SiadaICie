"""
In-page JavaScript extractors for e-IC 2.0 DOM fallback seatmap scraping.

Extracted from ebilet_seat_scraper.py for modular maintainability.
"""

SVG_SEATS_EXTRACTOR_JS = r"""
(targetClass = 2) => {
    const seats = new Set();
    const isClass1Target = Number(targetClass) === 1;

    const shouldSkipClass = (aria, dClass) => {
        if (isClass1Target) {
            return dClass.includes('second class') || (aria.includes('klasa 2') && !aria.includes('klasa 1'));
        }
        return dClass.includes('first class') || (aria.includes('klasa 1') && !aria.includes('klasa 2'));
    };

    const isPrmWheelchair = (aria) => {
        return (
            aria.includes('na wózku') ||
            aria.includes('na wozku') ||
            aria.includes('wózku inwalidzkim') ||
            aria.includes('wozku inwalidzkim') ||
            aria.includes('opiekuna osoby na wózku') ||
            aria.includes('opiekuna osoby na wozku') ||
            aria.includes('wózek inwalidzki') ||
            aria.includes('wozek inwalidzki')
        );
    };

    // 1. Primary: SVG <g> groups with aria-label containing 'wolne'
    const gElements = document.querySelectorAll("g[aria-label*='wolne' i], g[aria-label*='Wolne' i]");
    for (const g of gElements) {
        const aria = (g.getAttribute('aria-label') || '').toLowerCase();
        const dClass = (g.getAttribute('data-class') || '').toLowerCase();
        if (shouldSkipClass(aria, dClass) || isPrmWheelchair(aria)) {
            continue;
        }

        let num = '';
        const textEl = g.querySelector('text');
        if (textEl && textEl.textContent) {
            num = textEl.textContent.trim();
        }
        if (!num || !/^\d+$/.test(num)) {
            const m = aria.match(/miejsce\s+(\d+)/i);
            if (m) {
                num = m[1];
            }
        }
        if (num && /^\d+$/.test(num)) {
            seats.add(num);
        }
    }

    // 2. Secondary: button.seat-overlay-button overlays
    if (seats.size === 0) {
        const btns = document.querySelectorAll('button.seat-overlay-button');
        for (const b of btns) {
            const aria = (b.getAttribute('aria-label') || '').toLowerCase();
            if (!aria.includes('wolne')) continue;
            if (shouldSkipClass(aria, '') || isPrmWheelchair(aria)) continue;

            const placeVal = b.getAttribute('data-place') || '';
            if (placeVal && /^\d+$/.test(placeVal)) {
                seats.add(placeVal);
            } else {
                const m = aria.match(/miejsce\s+(\d+)/i);
                if (m && /^\d+$/.test(m[1])) {
                    seats.add(m[1]);
                }
            }
        }
    }

    // 3. Tertiary: image.place with status='1'
    if (seats.size === 0) {
        const imgs = document.querySelectorAll("image.place[status='1']");
        for (const img of imgs) {
            let parentG = img.closest('g');
            if (!parentG && img.id) {
                try {
                    parentG = document.querySelector(`g:has(#${CSS.escape(img.id)})`);
                } catch(e) {}
            }
            if (parentG) {
                const aria = (parentG.getAttribute('aria-label') || '').toLowerCase();
                const dClass = (parentG.getAttribute('data-class') || '').toLowerCase();
                if (shouldSkipClass(aria, dClass) || isPrmWheelchair(aria)) {
                    continue;
                }
                const textEl = parentG.querySelector('text');
                if (textEl && textEl.textContent) {
                    const num = textEl.textContent.trim();
                    if (num && /^\d+$/.test(num)) {
                        seats.add(num);
                    }
                }
            }
        }
    }

    // 4. Fallback selectors
    if (seats.size === 0) {
        const fallbackEls = document.querySelectorAll(
            '.seat.available, .seat.free, [data-status="available"], [data-status="free"], button.seat:not(.occupied):not(.taken):not(.disabled)'
        );
        for (const el of fallbackEls) {
            const txt = (el.innerText || el.textContent || '').trim();
            const m = txt.match(/\b\d+\b/);
            if (m) {
                seats.add(m[0]);
            }
        }
    }

    return Array.from(seats);
}
"""

CAROUSEL_METADATA_JS = r"""
(targetClass = 2) => {
    const slides = document.querySelectorAll("div[class*='Carriage_carriageSlide']");
    const isClass1Target = Number(targetClass) === 1;
    const result = [];
    slides.forEach((s, idx) => {
        const klasaEl = s.querySelector("p[class*='Carriage_carriageKlasa']");
        const klasaTxt = (klasaEl ? klasaEl.textContent : '').trim().toLowerCase();
        const slideTxt = (s.innerText || '').toLowerCase();
        const isClass1Only = (klasaTxt.includes('klasa 1') || klasaTxt.includes('kl. 1') || slideTxt.includes('klasa 1')) && (!klasaTxt.includes('klasa 2') && !slideTxt.includes('klasa 2'));
        const isClass2Only = (klasaTxt.includes('klasa 2') || klasaTxt.includes('kl. 2') || slideTxt.includes('klasa 2')) && (!klasaTxt.includes('klasa 1') && !slideTxt.includes('klasa 1'));
        const isWrongClass = isClass1Target ? isClass2Only : isClass1Only;
        const isService = ['wars', 'restaurac', 'sypialn', 'kuszet'].some(k => klasaTxt.includes(k) || slideTxt.includes(k));
        
        let cNo = '';
        const boxEl = s.querySelector("div[aria-label*='wagon numer'], div[aria-label*='Wagon'], [aria-label*='wagon']");
        if (boxEl) {
            const aria = boxEl.getAttribute('aria-label') || '';
            let m = aria.match(/wagon\s+(?:numer\s+)?(\d+)/i) || aria.match(/numer\s+(\d+)/i);
            if (m) cNo = m[1];
        }
        if (!cNo) {
            const numEl = s.querySelector("p[class*='Carriage_carriageNumberDesktop'], p[class*='Carriage_carriageNumberMb'], p[class*='Carriage_carriageNumber']");
            if (numEl) {
                let m = (numEl.textContent || '').match(/\d+/);
                if (m) cNo = m[0];
            }
        }
        if (!cNo && slideTxt) {
            let m = slideTxt.match(/wagon\s*(\d+)/i) || slideTxt.match(/\b(\d+)\b/);
            if (m) cNo = m[1];
        }

        // Do NOT use swiper-slide-active (marks leftmost visible viewport slide, not the selected carriage).
        // e-IC 2.0 marks the currently selected carriage with 'current-' in Carriage_carriageBox background-image.
        const styleBox = s.querySelector("div[class*='Carriage_carriageBox']") || boxEl;
        const boxStyle = (styleBox ? (styleBox.getAttribute('style') || '') : '').toLowerCase();
        const boxBg = (styleBox && styleBox.style && styleBox.style.backgroundImage ? styleBox.style.backgroundImage : '').toLowerCase();
        const isActive = boxStyle.includes('current-') || boxBg.includes('current-');

        result.push({
            index: idx,
            carriageNumber: cNo,
            skip: isWrongClass || isService || !cNo,
            isActive: isActive
        });
    });
    return result;
}
"""
