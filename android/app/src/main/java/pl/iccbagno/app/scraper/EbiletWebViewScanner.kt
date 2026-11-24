package pl.iccbagno.app.scraper

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import org.json.JSONObject
import pl.iccbagno.app.domain.FullTrainAvailabilityMap
import pl.iccbagno.app.domain.RouteHelpers
import pl.iccbagno.app.domain.SegmentAvailabilityMap
import pl.iccbagno.app.domain.StationStop
import java.io.ByteArrayInputStream
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger
import kotlin.coroutines.resume

open class GraphicReservationException(message: String) : Exception(message)

class EbiletWebViewScanner(
    private val context: Context,
    private val scheduleClient: ScheduleClient,
    private val onProgress: (Int, String) -> Unit,
    private val onAttachDebugWebView: ((WebView?) -> Unit)? = null
) {
    private val mainHandler = Handler(Looper.getMainLooper())
    private val concurrency = 2

    companion object {
        private val BLOCKED_HOST_SUBSTRINGS = listOf(
            "google-analytics.com",
            "googletagmanager.com",
            "doubleclick.net",
            "facebook.net",
            "facebook.com/tr",
            "hotjar.com",
            "clarity.ms",
            "adrum",
            "appdynamics"
        )

        private val BLOCKED_EXTENSIONS = listOf(
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
            ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp4", ".webm"
        )

        private val GRM_SVG_URL_REGEX = Regex(
            "^(https?://[^/]+/grm/wagon/svg/wbnet/[^/]+/[^/]+)/([^/]+)/([^/]+)(/.*)$"
        )

        private val TRAIN_DIGITS_REGEX = Regex("\\b(\\d{3,5})\\b")
        private const val TAG = "IccScanner"

        private const val FIND_CARDS_JS = """
            function __iccFindCards() {
                const desktop = Array.from(document.querySelectorAll(
                    "li[class*='tripPropositionDesktop__container' i][id], .connection-card, .connection-row, tr.connection"
                ));
                if (desktop.length > 0) return desktop;

                const candidates = Array.from(document.querySelectorAll(
                    "li[class*='TripProposition' i], div[class*='TripProposition' i], ul > li"
                )).filter(el => {
                    const txt = (el.innerText || '');
                    const low = txt.toLowerCase();
                    if (!/\b\d{1,2}:\d{2}\b/.test(txt)) return false;
                    return low.includes('klasa') || low.includes('kup bilet') || low.includes('wybierz') ||
                           low.includes('brak miejsc') || low.includes('niedostępn') || el.querySelector('button') !== null;
                });
                return candidates.filter(el => !candidates.some(other => other !== el && other.contains(el)));
            }
        """

        private fun emptyBlockedResponse(): WebResourceResponse =
            WebResourceResponse(
                "text/plain",
                "utf-8",
                404,
                "Blocked",
                emptyMap(),
                ByteArrayInputStream(ByteArray(0))
            )

        private const val GRM_HOOK_JS = """
            (function() {
                if (window.__iccGrmHookInstalled) return;
                window.__iccGrmHookInstalled = true;

                const notifySklad = (url, text) => {
                    try {
                        if (window.AndroidGrmBridge && text) {
                            window.AndroidGrmBridge.onSklad(String(url), String(text));
                        }
                    } catch (e) {}
                };
                const notifySvg = (url, text) => {
                    try {
                        if (window.AndroidGrmBridge && text) {
                            window.AndroidGrmBridge.onSvg(String(url), String(text));
                        }
                    } catch (e) {}
                };

                const notifyNet = (msg) => {
                    try {
                        if (window.AndroidGrmBridge && window.AndroidGrmBridge.onNetLog) {
                            window.AndroidGrmBridge.onNetLog(String(msg));
                        }
                    } catch (e) {}
                };

                const origFetch = window.fetch;
                window.fetch = async function(...args) {
                    const url = (typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || '');
                    try {
                        const resp = await origFetch.apply(this, args);
                        const finalUrl = resp.url || url || '';
                        const low = finalUrl.toLowerCase();
                        if (low.includes('intercity.pl') && !low.endsWith('.js') && !low.endsWith('.css')) {
                            notifyNet('FETCH ' + resp.status + ' ' + finalUrl.slice(0, 140));
                        }
                        if (resp && resp.status === 200 && (low.includes('/grm/sklad/wbnet/') || low.includes('/grm/wagon/svg/wbnet/'))) {
                            const cloned = resp.clone();
                            cloned.text().then(txt => {
                                if (low.includes('/grm/sklad/wbnet/')) notifySklad(finalUrl, txt);
                                else notifySvg(finalUrl, txt);
                            }).catch(() => {});
                        }
                        return resp;
                    } catch (err) {
                        notifyNet('FETCH_ERR ' + url.slice(0, 120) + ' -> ' + err);
                        throw err;
                    }
                };

                const origOpen = XMLHttpRequest.prototype.open;
                const origSend = XMLHttpRequest.prototype.send;
                XMLHttpRequest.prototype.open = function(method, url, ...rest) {
                    this.__iccUrl = String(url || '');
                    return origOpen.call(this, method, url, ...rest);
                };
                XMLHttpRequest.prototype.send = function(...args) {
                    this.addEventListener('load', function() {
                        try {
                            const u = String(this.responseURL || this.__iccUrl || '');
                            const low = u.toLowerCase();
                            if (low.includes('intercity.pl') && !low.endsWith('.js') && !low.endsWith('.css')) {
                                notifyNet('XHR ' + this.status + ' ' + u.slice(0, 140));
                            }
                            if (this.status === 200 && (low.includes('/grm/sklad/wbnet/') || low.includes('/grm/wagon/svg/wbnet/'))) {
                                const txt = this.responseText || '';
                                if (low.includes('/grm/sklad/wbnet/')) notifySklad(u, txt);
                                else notifySvg(u, txt);
                            }
                        } catch (e) {}
                    });
                    return origSend.apply(this, args);
                };
            })();
        """
    }

    private class SegmentCaptureState {
        @Volatile var skladJson: JSONObject? = null
        @Volatile var svgPrefix: String? = null
        @Volatile var svgSuffix: String? = null
        val svgsByWagon = ConcurrentHashMap<String, String>()
    }

    private class GrmJsBridge(private val stateProvider: () -> SegmentCaptureState) {
        @JavascriptInterface
        fun onNetLog(msg: String) {
            Log.i(TAG, "[NET] $msg")
        }

        @JavascriptInterface
        fun onSklad(url: String, jsonText: String) {
            try {
                val obj = JSONObject(jsonText)
                if (obj.has("klasa1") || obj.has("klasa2") || obj.has("wagonySchemat") || obj.has("wagony")) {
                    stateProvider().skladJson = obj
                }
            } catch (_: Exception) {
            }
        }

        @JavascriptInterface
        fun onSvg(url: String, svgText: String) {
            try {
                val st = stateProvider()
                val m = GRM_SVG_URL_REGEX.matchEntire(url)
                if (m != null) {
                    st.svgPrefix = m.groupValues[1]
                    val wNo = m.groupValues[2]
                    st.svgSuffix = m.groupValues[4]
                    if ("<svg" in svgText.lowercase()) {
                        st.svgsByWagon[wNo] = svgText
                    }
                }
            } catch (_: Exception) {
            }
        }
    }

    private class WorkerWebView(
        val webView: WebView,
        var captureState: SegmentCaptureState = SegmentCaptureState()
    )

    @SuppressLint("SetJavaScriptEnabled")
    private suspend fun createWorkerWebView(): WorkerWebView = withContext(Dispatchers.Main) {
        val stateHolder = arrayOf(SegmentCaptureState())
        val wv = WebView(context.applicationContext).apply {
            layoutParams = FrameLayout.LayoutParams(1280, 900)
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.setGeolocationEnabled(false)
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            settings.useWideViewPort = true
            settings.loadWithOverviewMode = true
            // Strip "; wv" marker while keeping genuine Android platform/Chrome UA consistency for Akamai
            settings.userAgentString = settings.userAgentString.replace("; wv)", ")").replace("; wv;", ";")
            settings.cacheMode = WebSettings.LOAD_DEFAULT
        }
        val bridge = GrmJsBridge { stateHolder[0] }
        wv.addJavascriptInterface(bridge, "AndroidGrmBridge")

        wv.webViewClient = object : WebViewClient() {
            override fun onPageStarted(view: WebView?, url: String?, favicon: Bitmap?) {
                super.onPageStarted(view, url, favicon)
                view?.evaluateJavascript(GRM_HOOK_JS, null)
            }

            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                view?.evaluateJavascript(GRM_HOOK_JS, null)
            }

            override fun shouldInterceptRequest(view: WebView?, request: WebResourceRequest?): WebResourceResponse? {
                val url = request?.url?.toString()?.lowercase() ?: return null
                if ("/grm/" in url) return null
                if (BLOCKED_HOST_SUBSTRINGS.any { it in url }) {
                    return emptyBlockedResponse()
                }
                val pathOnly = url.substringBefore('?')
                if (BLOCKED_EXTENSIONS.any { pathOnly.endsWith(it) }) {
                    return emptyBlockedResponse()
                }
                return super.shouldInterceptRequest(view, request)
            }
        }

        val cm = CookieManager.getInstance()
        cm.setAcceptCookie(true)
        cm.setAcceptThirdPartyCookies(wv, true)
        // Force initial 1280x900 layout so SPA renders desktop components
        wv.measure(
            android.view.View.MeasureSpec.makeMeasureSpec(1280, android.view.View.MeasureSpec.EXACTLY),
            android.view.View.MeasureSpec.makeMeasureSpec(900, android.view.View.MeasureSpec.EXACTLY)
        )
        wv.layout(0, 0, 1280, 900)

        WorkerWebView(wv, stateHolder[0])
    }

    private suspend fun destroyWorkerWebView(worker: WorkerWebView) = withContext(Dispatchers.Main) {
        try {
            (worker.webView.parent as? ViewGroup)?.removeView(worker.webView)
            worker.webView.stopLoading()
            worker.webView.clearCache(true)
            worker.webView.clearHistory()
            worker.webView.destroy()
        } catch (_: Exception) {
        }
    }

    private suspend fun evalJs(wv: WebView, script: String): String =
        withTimeoutOrNull(8_000L) {
            withContext(Dispatchers.Main) {
                suspendCancellableCoroutine { cont ->
                    wv.evaluateJavascript(script) { result ->
                        if (cont.isActive) cont.resume(result ?: "null")
                    }
                }
            }
        } ?: "null"

    private fun decodeJsString(raw: String): String {
        if (raw == "null" || raw.isBlank()) return ""
        return try {
            org.json.JSONTokener("{\"v\":$raw}").nextValue().let {
                if (it is JSONObject) it.optString("v", "") else raw.trim('"')
            }
        } catch (_: Exception) {
            raw.trim('"')
        }
    }

    private suspend fun closeCookieBanners(wv: WebView) {
        val js = """
            (function() {
                const sels = [
                    '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
                    '#CybotCookiebotDialogBodyButtonAccept',
                    "button[id*='Accept']",
                    "button[class*='accept']"
                ];
                for (const s of sels) {
                    const el = document.querySelector(s);
                    if (el && el.offsetParent !== null) { el.click(); return true; }
                }
                for (const b of document.querySelectorAll('button')) {
                    const t = (b.innerText || '').toLowerCase();
                    if ((t.includes('zaakceptuj') || t.includes('akceptuj') || t.includes('zgadzam')) && b.offsetParent !== null) {
                        b.click();
                        return true;
                    }
                }
                return false;
            })();
        """.trimIndent()
        evalJs(wv, js)
    }

    private suspend fun scanSingleRelation(
        worker: WorkerWebView,
        fromStation: String,
        toStation: String,
        travelDate: String,
        departureTime: String,
        trainNumber: String,
        ticketClass: Int,
        fromEva: String,
        toEva: String,
        stepProgress: (String) -> Unit = {}
    ): Map<String, List<String>> {
        val freshState = SegmentCaptureState()
        worker.captureState = freshState
        withContext(Dispatchers.Main) {
            worker.webView.removeJavascriptInterface("AndroidGrmBridge")
            worker.webView.addJavascriptInterface(GrmJsBridge { worker.captureState }, "AndroidGrmBridge")
        }

        val searchUrl = GrmParser.buildEicSearchUrl(
            fromEva = fromEva,
            toEva = toEva,
            dateStr = travelDate,
            departureTime = departureTime,
            directOnly = true
        )
        if ("swyj=&" in searchUrl || "sprzy=&" in searchUrl) {
            throw GraphicReservationException("SCRAPE_ERROR: Brak kodu EVA dla trasy $fromStation -> $toStation.")
        }

        val isSameMetro = RouteHelpers.isSameMetroHub(fromStation, toStation)
        val maxAttempts = 2
        var lastError: Throwable? = null

        for (attempt in 1..maxAttempts) {
            try {
                freshState.skladJson = null
                freshState.svgPrefix = null
                freshState.svgSuffix = null
                freshState.svgsByWagon.clear()

                Log.i(TAG, "[$fromStation -> $toStation] Attempt $attempt: loading $searchUrl")
                stepProgress("Otwieranie listy połączeń: $fromStation -> $toStation...")
                withContext(Dispatchers.Main) {
                    worker.webView.loadUrl(searchUrl)
                }

                // Wait up to 35s for connection cards (reload once after 10.0s stall)
                val startWait = System.currentTimeMillis()
                var reloaded = false
                var cardsReady = false
                var lastDiagAt = 0L
                val trainDigitsOnly = TRAIN_DIGITS_REGEX.find(trainNumber)?.groupValues?.get(1).orEmpty()
                while (System.currentTimeMillis() - startWait < 35_000L) {
                    coroutineScope { ensureActive() }
                    closeCookieBanners(worker.webView)
                    val probeRaw = evalJs(
                        worker.webView,
                        """
                        (function(tNum) {
                            $FIND_CARDS_JS
                            const cards = __iccFindCards();
                            const loaders = document.querySelectorAll("[class*='trainLoaderWithText' i]");
                            const body = (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').trim();
                            let sample = '';
                            if (cards.length === 0 && loaders.length === 0 && tNum && body.includes(tNum)) {
                                const els = Array.from(document.querySelectorAll('li, article, section, div'));
                                for (const el of els) {
                                    if (el.innerText && el.innerText.includes(tNum) && el.innerText.length < 400 && (el.innerText.includes('klasa') || el.querySelector('button'))) {
                                        const liParent = el.closest('li') || el.parentElement || el;
                                        sample = liParent.tagName + '#' + (liParent.id || '') + '.' + (liParent.className || '') + ' | btns=' + Array.from(liParent.querySelectorAll('button')).map(b => (b.innerText || b.getAttribute('aria-label') || '').trim()).join(' || ');
                                        break;
                                    }
                                }
                            }
                            return JSON.stringify({ cards: cards.length, loaders: loaders.length, w: window.innerWidth, sample: sample.slice(0, 400), body: body.slice(0, 600) });
                        })(${JSONObject.quote(trainDigitsOnly)});
                        """.trimIndent()
                    )
                    val probeJson = try {
                        JSONObject(decodeJsString(probeRaw))
                    } catch (_: Exception) {
                        JSONObject()
                    }
                    val cardCount = probeJson.optInt("cards", 0)
                    val loaderCount = probeJson.optInt("loaders", 0)
                    val bodyRaw = probeJson.optString("body", "")
                    val sampleRaw = probeJson.optString("sample", "")
                    val bodyLow = bodyRaw.lowercase()
                    val elapsedMs = System.currentTimeMillis() - startWait

                    if (elapsedMs - lastDiagAt >= 3_500L || sampleRaw.isNotEmpty()) {
                        lastDiagAt = elapsedMs
                        Log.i(TAG, "[$fromStation -> $toStation] t=${elapsedMs}ms cards=$cardCount loaders=$loaderCount w=${probeJson.optInt("w", -1)} sample='$sampleRaw' body='${bodyRaw.take(180)}'")
                    }

                    if (cardCount > 0 && loaderCount == 0) {
                        Log.i(TAG, "[$fromStation -> $toStation] Cards ready: count=$cardCount (${elapsedMs}ms)")
                        cardsReady = true
                        break
                    }

                    if (loaderCount == 0 && cardCount == 0 && elapsedMs > 4_000L) {
                        if ("kod stacji spoza zakresu" in bodyLow) {
                            throw GraphicReservationException("SCRAPE_ERROR: Kod stacji spoza zakresu (kod 303) dla $fromStation -> $toStation.")
                        }
                        if (("nie znaleziono połączeń" in bodyLow) ||
                            ("brak połączeń" in bodyLow && "po godzinie" !in bodyLow && "przed godziną" !in bodyLow && "przed godzina" !in bodyLow)
                        ) {
                            throw GraphicReservationException("SCRAPE_ERROR: System PKP Intercity nie znalazł żadnych połączeń dla $fromStation -> $toStation.")
                        }
                    }

                    if (elapsedMs > 10_000L && !reloaded && cardCount == 0) {
                        reloaded = true
                        Log.i(TAG, "[$fromStation -> $toStation] List stalled >10s (cards=$cardCount, loaders=$loaderCount), reloading...")
                        stepProgress("Przeładowanie listy połączeń ($fromStation -> $toStation)...")
                        withContext(Dispatchers.Main) { worker.webView.reload() }
                    }
                    delay(400)
                }

                if (!cardsReady) {
                    Log.w(TAG, "[$fromStation -> $toStation] Timeout waiting for cards after 35s")
                    throw GraphicReservationException(
                        "SCRAPE_ERROR: Nie znaleziono pociągu $trainNumber na liście połączeń dla relacji $fromStation -> $toStation po 35s oczekiwania."
                    )
                }

                // Match train card strictly by 3-5 digit train number + optional pagination
                val trainCleanNum = TRAIN_DIGITS_REGEX.find(trainNumber)?.groupValues?.get(1).orEmpty()
                Log.i(TAG, "[$fromStation -> $toStation] Matching train '$trainNumber' using cleanNum='$trainCleanNum'")
                stepProgress("Dopasowywanie pociągu $trainCleanNum ($fromStation -> $toStation)...")
                val matchJs = """
                    (function(num) {
                        $FIND_CARDS_JS
                        const cards = __iccFindCards();
                        const idRe = new RegExp('(?:_|\\\\|)' + num + '(?:_|\\\\||$)');
                        const wordRe = new RegExp('\\\\b' + num + '\\\\b');
                        for (let i = 0; i < cards.length; i++) {
                            const cid = cards[i].id || '';
                            if (cid && (cid.includes('_' + num + '_') || idRe.test(cid))) return i;
                        }
                        for (let i = 0; i < cards.length; i++) {
                            const txt = cards[i].innerText || '';
                            if (wordRe.test(txt)) return i;
                        }
                        return -1;
                    })(${JSONObject.quote(trainCleanNum)});
                """.trimIndent()

                var matchedIdx = evalJs(worker.webView, matchJs).trim().toIntOrNull() ?: -1
                if (matchedIdx < 0) {
                    outer@ for (dirLabel in listOf("Później", "Wcześniej")) {
                        for (step in 1..2) {
                            val clickedRaw = evalJs(
                                worker.webView,
                                """
                                (function(lbl) {
                                    for (const b of document.querySelectorAll('button')) {
                                        if ((b.innerText || '').includes(lbl) && b.offsetParent !== null && !b.disabled) {
                                            b.click();
                                            return true;
                                        }
                                    }
                                    return false;
                                })(${JSONObject.quote(dirLabel)});
                                """.trimIndent()
                            )
                            if (clickedRaw.trim() != "true") break
                            delay(1400)
                            matchedIdx = evalJs(worker.webView, matchJs).trim().toIntOrNull() ?: -1
                            if (matchedIdx >= 0) break@outer
                        }
                    }
                }

                if (matchedIdx < 0) {
                    Log.w(TAG, "[$fromStation -> $toStation] Train '$trainCleanNum' not matched after pagination")
                    throw GraphicReservationException(
                        "SCRAPE_ERROR: Nie znaleziono pociągu $trainNumber na liście połączeń dla relacji $fromStation -> $toStation."
                    )
                }
                Log.i(TAG, "[$fromStation -> $toStation] Matched card index=$matchedIdx")
                stepProgress("Wybór klasy $ticketClass i schematu miejsc...")

                // Wait for card price loader to finish
                val priceWaitStart = System.currentTimeMillis()
                while (System.currentTimeMillis() - priceWaitStart < 12_000L) {
                    val readyRaw = evalJs(
                        worker.webView,
                        """
                        (function(idx) {
                            $FIND_CARDS_JS
                            const cards = __iccFindCards();
                            const card = cards[idx];
                            if (!card) return true;
                            if (card.querySelector("button[aria-label*='Kup bilet'], button[data-testid='OutlineButton'], button[aria-label*='2 klas'], button[aria-label*='1 klas'], button[aria-label*='klasa 2' i], button[aria-label*='klasa 1' i]")) return true;
                            const txt = (card.innerText || '').toLowerCase();
                            return !(txt.includes('ładowanie ceny') || txt.includes('ladowanie ceny'));
                        })($matchedIdx);
                        """.trimIndent()
                    )
                    if (readyRaw.trim() == "true") break
                    delay(250)
                }

                // Expand card + select class
                val expandAndClassRaw = evalJs(
                    worker.webView,
                    """
                    (function(idx, tClass) {
                        $FIND_CARDS_JS
                        const cards = __iccFindCards();
                        const card = cards[idx];
                        if (!card) return 'NO_CARD';
                        const hasDirectClassBtn = Array.from(card.querySelectorAll("button, [role='button']")).some(b => {
                            const t = ((b.innerText || '') + ' ' + (b.getAttribute('aria-label') || '')).toLowerCase();
                            return (t.includes('klasa 1') || t.includes('klasa 2') || t.includes('1 klas') || t.includes('2 klas')) && b.offsetParent !== null;
                        });
                        if (!hasDirectClassBtn) {
                            const expSels = ["button[data-testid='OutlineButton']", "button[aria-label*='Kup bilet']"];
                            for (const s of expSels) {
                                const b = card.querySelector(s);
                                if (b && b.offsetParent !== null) { b.click(); break; }
                            }
                            if (!card.querySelector("button[aria-label*='1 klas'], button[aria-label*='2 klas']")) {
                                for (const b of card.querySelectorAll("button, [role='button']")) {
                                    const t = (b.innerText || '').toLowerCase();
                                    if ((t.includes('kup bilet') || t.includes('wybierz')) && b.offsetParent !== null) {
                                        b.click();
                                        break;
                                    }
                                }
                            }
                        }
                        return 'EXPANDED';
                    })($matchedIdx, $ticketClass);
                    """.trimIndent()
                )
                if (decodeJsString(expandAndClassRaw) == "NO_CARD") {
                    throw GraphicReservationException("SCRAPE_ERROR: Kafelek pociągu zniknął dla $fromStation -> $toStation.")
                }

                delay(300)

                val selectClassResult = decodeJsString(
                    evalJs(
                        worker.webView,
                        """
                        (function(idx, tClass) {
                            $FIND_CARDS_JS
                            const cards = __iccFindCards();
                            const root = cards[idx] || document;
                            const targets = tClass === 1
                                ? ["button[aria-label*='1 klas']", "button[aria-label*='klasa 1' i]", "[data-testid='SuperPromo-select-seat-type-0']", "1 klasa", "klasa 1", "1 kl"]
                                : ["button[aria-label*='2 klas']", "button[aria-label*='klasa 2' i]", "[data-testid='SuperPromo-select-seat-type-1']", "2 klasa", "klasa 2", "2 kl"];
                            for (const t of targets) {
                                const isCss = t.startsWith('button') || t.startsWith('[');
                                let btn = isCss ? root.querySelector(t) : null;
                                if (!btn && !isCss) {
                                    btn = Array.from(root.querySelectorAll("button, [role='button'], a")).find(b => {
                                        const low = ((b.innerText || '') + ' ' + (b.getAttribute('aria-label') || '')).toLowerCase();
                                        return low.includes(t) && b.offsetParent !== null;
                                    });
                                }
                                if (!btn && isCss) {
                                    btn = document.querySelector(t);
                                }
                                if (btn && btn.offsetParent !== null) {
                                    const txt = (btn.innerText || '').toLowerCase();
                                    if (txt.includes('brak miejsc') || txt.includes('niedostępne') || txt.includes('zablokowana') || txt.includes('wyprzedane')) {
                                        return 'SOLD_OUT';
                                    }
                                    btn.click();
                                    return 'CLICKED:' + (btn.innerText || '').replace(/\s+/g, ' ').trim();
                                }
                            }
                            return 'NOT_FOUND:' + (root.innerText || '').replace(/\s+/g, ' ').slice(0, 200);
                        })($matchedIdx, $ticketClass);
                        """.trimIndent()
                    )
                )
                Log.i(TAG, "[$fromStation -> $toStation] Class selection: $selectClassResult")
                if (selectClassResult == "SOLD_OUT") {
                    throw GraphicReservationException("BRAK_MIEJSC_KLASA_$ticketClass: Wyczerpano pulę miejsc w Klasie $ticketClass na tym odcinku.")
                }

                // Wait and click seatmap button on Your Journey page + re-install hook
                var schemaClicked = false
                val schemaWaitStart = System.currentTimeMillis()
                while (System.currentTimeMillis() - schemaWaitStart < 8_000L) {
                    evalJs(worker.webView, GRM_HOOK_JS)
                    val clickedRaw = evalJs(
                        worker.webView,
                        """
                        (function() {
                            // Dismiss intermediate modals
                            for (const b of document.querySelectorAll('button')) {
                                const t = (b.innerText || '').toLowerCase();
                                if ((t === 'dalej' || t === 'ok' || t === 'kontynuuj') && b.closest("[role='dialog']")) {
                                    b.click();
                                }
                            }
                            const sels = [
                                "button[class*='PreferedPlace_PreferedPlace_grmButton']",
                                "button[class*='PreferedPlace_PreferedPlace_groupButton']",
                                "button[class*='PreferedPlace']"
                            ];
                            for (const s of sels) {
                                const el = document.querySelector(s);
                                if (el && el.offsetParent !== null) { el.click(); return true; }
                            }
                            for (const b of document.querySelectorAll('button, a')) {
                                const t = (b.innerText || '').toLowerCase();
                                if ((t.includes('wybierz miejsce') || t.includes('wskazanie miejsca') || t.includes('wskaż miejsce') || t.includes('schematu')) && b.offsetParent !== null) {
                                    b.click();
                                    return true;
                                }
                            }
                            return false;
                        })();
                        """.trimIndent()
                    )
                    if (clickedRaw.trim() == "true") {
                        schemaClicked = true
                        break
                    }
                    delay(350)
                }

                // Wait for GRM API (/grm/sklad/wbnet/ + SVG)
                val grmWaitStart = System.currentTimeMillis()
                while (System.currentTimeMillis() - grmWaitStart < 10_000L) {
                    coroutineScope { ensureActive() }
                    val sklad = freshState.skladJson
                    if (sklad != null && freshState.svgPrefix != null) break
                    if (sklad != null) {
                        val (targetsChk, hasKeyChk) = GrmParser.selectGrmTargetWagons(sklad, ticketClass)
                        if (hasKeyChk && targetsChk.isEmpty()) break
                    }
                    delay(200)
                }

                val sklad = freshState.skladJson
                Log.i(TAG, "[$fromStation -> $toStation] GRM wait finished: sklad=${sklad != null}, svgPrefix=${freshState.svgPrefix != null}, cachedSvgs=${freshState.svgsByWagon.keys}")
                if (sklad != null) {
                    val (targets, hasKey) = GrmParser.selectGrmTargetWagons(sklad, ticketClass)
                    Log.i(TAG, "[$fromStation -> $toStation] Targets: $targets, hasKey=$hasKey")
                    if (hasKey && targets.isEmpty()) {
                        throw GraphicReservationException(
                            "BRAK_MIEJSC_KLASA_$ticketClass: Brak dostępnych wagonów Klasy $ticketClass w składzie."
                        )
                    }

                    val prefix = freshState.svgPrefix
                    val suffix = freshState.svgSuffix
                    if (targets.isNotEmpty() && !prefix.isNullOrEmpty() && !suffix.isNullOrEmpty()) {
                        val missing = targets.filter { (wNo, _) -> !freshState.svgsByWagon.containsKey(wNo) }
                        if (missing.isNotEmpty()) {
                            stepProgress("Pobieranie schematu ${targets.size} wagonów...")
                            val reqArr = JSONArray()
                            for ((wNo, schemaId) in missing) {
                                val url = "$prefix/$wNo/$schemaId$suffix"
                                reqArr.put(JSONObject().put("wNo", wNo).put("url", url))
                            }
                            val fetchJs = """
                                (async function(items) {
                                    window.__iccSvgBatch = null;
                                    const out = {};
                                    await Promise.all(items.map(async (it) => {
                                        try {
                                            const ctrl = new AbortController();
                                            const timer = setTimeout(() => ctrl.abort(), 6500);
                                            const r = await fetch(it.url, { method: 'GET', credentials: 'include', signal: ctrl.signal });
                                            clearTimeout(timer);
                                            if (r.ok) out[it.wNo] = await r.text();
                                        } catch (e) {}
                                    }));
                                    window.__iccSvgBatch = JSON.stringify(out);
                                    return true;
                                })(${reqArr});
                            """.trimIndent()
                            evalJs(worker.webView, fetchJs)

                            val batchWait = System.currentTimeMillis()
                            while (System.currentTimeMillis() - batchWait < 7_500L) {
                                val doneRaw = evalJs(worker.webView, "window.__iccSvgBatch")
                                if (doneRaw != "null" && doneRaw.isNotBlank()) {
                                    val mapJson = JSONObject(decodeJsString(doneRaw))
                                    val keys = mapJson.keys()
                                    while (keys.hasNext()) {
                                        val k = keys.next()
                                        val svgTxt = mapJson.optString(k, "")
                                        if (svgTxt.isNotBlank()) {
                                            freshState.svgsByWagon[k] = svgTxt
                                        }
                                    }
                                    break
                                }
                                delay(150)
                            }
                        }

                        val result = LinkedHashMap<String, List<String>>()
                        for ((wNo, _) in targets) {
                            val svgXml = freshState.svgsByWagon[wNo]
                            if (!svgXml.isNullOrBlank()) {
                                result[wNo] = GrmParser.parseGrmWagonSvg(svgXml, ticketClass)
                            }
                        }
                        if (result.size == targets.size) {
                            Log.i(TAG, "[$fromStation -> $toStation] Parsed seats OK: ${result.mapValues { it.value.size }}")
                            return result
                        }
                    }
                }

                // Secondary DOM fallback: extract visible SVG <g> elements directly if API template wasn't caught
                val domSvgRaw = decodeJsString(
                    evalJs(
                        worker.webView,
                        """
                        (function() {
                            const svg = document.querySelector("svg[viewBox], div[class*='SeatMap'] svg, div[class*='Wagon'] svg");
                            return svg ? svg.outerHTML : "";
                        })();
                        """.trimIndent()
                    )
                )
                if (domSvgRaw.isNotBlank() && "<svg" in domSvgRaw.lowercase()) {
                    val seats = GrmParser.parseGrmWagonSvg(domSvgRaw, ticketClass)
                    val activeWagon = decodeJsString(
                        evalJs(
                            worker.webView,
                            """
                            (function() {
                                const slides = document.querySelectorAll("div[class*='Carriage_carriageSlide']");
                                for (const s of slides) {
                                    const box = s.querySelector("div[class*='Carriage_carriageBox']");
                                    const st = ((box && box.getAttribute('style')) || '').toLowerCase();
                                    if (st.includes('current-')) {
                                        const aria = (s.querySelector("[aria-label*='wagon']") || {}).getAttribute ? (s.querySelector("[aria-label*='wagon']").getAttribute('aria-label') || '') : '';
                                        const m = aria.match(/(\d+)/) || (s.innerText || '').match(/\b(\d+)\b/);
                                        if (m) return m[1];
                                    }
                                }
                                return '1';
                            })();
                            """.trimIndent()
                        )
                    ).ifBlank { "1" }
                    if (seats.isNotEmpty()) {
                        return mapOf(activeWagon to seats)
                    }
                }

                val errMsg = if (!schemaClicked) {
                    "SCRAPE_ERROR: Brak przycisku otwarcia schematu dla $fromStation -> $toStation."
                } else {
                    "BRAK_MIEJSC_KLASA_$ticketClass: Brak wolnych miejsc na schemacie."
                }
                throw GraphicReservationException(errMsg)
            } catch (e: CancellationException) {
                throw e
            } catch (e: GraphicReservationException) {
                if (GrmParser.isSoldOutReason(e.message)) throw e
                val errLow = (e.message ?: "").lowercase()
                if (isSameMetro && "nie znaleziono pociągu" in errLow && "po 35s" !in errLow) {
                    throw e
                }
                lastError = e
                if (attempt >= maxAttempts) throw e
                delay(400)
            } catch (e: Exception) {
                lastError = e
                if (attempt >= maxAttempts) throw GraphicReservationException("SCRAPE_ERROR: ${e.message}")
                delay(400)
            }
        }
        throw (lastError ?: GraphicReservationException("SCRAPE_ERROR: Nieznany błąd skanowania."))
    }

    suspend fun scanFullTrain(
        fromStation: String,
        toStation: String,
        travelDate: String,
        trainNumber: String,
        departureTime: String? = null,
        ticketClass: Int = 2,
        fastPath: Boolean = true
    ): FullTrainAvailabilityMap = coroutineScope {
        onProgress(5, "Pobieranie trasy i stacji pośrednich pociągu $trainNumber...")
        val stops = scheduleClient.resolveRealRoute(fromStation, toStation, travelDate, trainNumber, departureTime)
        if (stops.size < 2) {
            throw GraphicReservationException("Nie udało się wyznaczyć trasy dla pociągu $trainNumber.")
        }

        val numSegments = stops.size - 1
        onProgress(10, "Wykryto $numSegments odcinków trasy.")

        val pool = ArrayList<WorkerWebView>(concurrency)
        try {
            repeat(concurrency) { idx ->
                val w = createWorkerWebView()
                pool.add(w)
                if (idx == 0) onAttachDebugWebView?.invoke(w.webView)
            }

            // Warmup e-IC home page so Akamai & Cookiebot initialize cookies naturally
            onProgress(12, "Inicjalizacja sesji e-IC i ciasteczek...")
            Log.i(TAG, "Warmup: loading https://ebilet.intercity.pl/")
            withContext(Dispatchers.Main) {
                pool[0].webView.loadUrl("https://ebilet.intercity.pl/")
            }
            delay(2000)
            closeCookieBanners(pool[0].webView)
            delay(400)

            // 1. Direct Fast-Path A -> B
            var fpWagonsUnavailable = false
            if (fastPath && numSegments > 1) {
                val sFirst = stops.first()
                val sLast = stops.last()
                val fullDep = sFirst.departureTime.ifBlank { sFirst.arrivalTime }.ifBlank { departureTime ?: "12:00" }
                val fullDate = RouteHelpers.computeSegmentDate(travelDate, sFirst.dayOffset)

                onProgress(14, "Szybkie sprawdzanie (Fast-Path): ${sFirst.name} -> ${sLast.name}...")
                try {
                    val fullCarriages = withTimeout(75_000L) {
                        scanSingleRelation(
                            worker = pool[0],
                            fromStation = sFirst.name,
                            toStation = sLast.name,
                            travelDate = fullDate,
                            departureTime = fullDep,
                            trainNumber = trainNumber,
                            ticketClass = ticketClass,
                            fromEva = sFirst.evaCode,
                            toEva = sLast.evaCode,
                            stepProgress = { msg -> onProgress(16, "Fast-Path: $msg") }
                        )
                    }
                    if (fullCarriages.values.any { it.isNotEmpty() }) {
                        onProgress(100, "Szybkie sprawdzanie: znaleziono wolne miejsca na 100% trasy bez przesiadek!")
                        return@coroutineScope FullTrainAvailabilityMap(
                            trainNumber = trainNumber,
                            travelDate = travelDate,
                            fastPathUsed = true,
                            totalRouteSegments = numSegments,
                            segments = listOf(
                                SegmentAvailabilityMap(
                                    segmentIndex = 0,
                                    fromStation = sFirst.name,
                                    toStation = sLast.name,
                                    departureTime = fullDep,
                                    durationMin = RouteHelpers.computeDurationMin(sFirst, sLast, 30 * numSegments),
                                    fromEva = sFirst.evaCode,
                                    toEva = sLast.evaCode,
                                    segmentDate = fullDate,
                                    carriages = fullCarriages,
                                    status = "OK"
                                )
                            )
                        )
                    }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: GraphicReservationException) {
                    fpWagonsUnavailable = GrmParser.isSeatmapWagonsUnavailableError(e.message)
                    onProgress(22, "Brak wolnego miejsca na całą trasę (${sFirst.name} -> ${sLast.name}) - przechodzę dalej...")
                } catch (_: Exception) {
                    onProgress(22, "Fast-Path niedostępny - sprawdzam dalej...")
                }

                // 2. Adaptive Split Fast-Path when e-IC blocks full-route graphical seatmap on long routes
                if (fpWagonsUnavailable && numSegments >= 8) {
                    onProgress(23, "Schemat zablokowany dla całej relacji - sprawdzam Fast-Path połówkami trasy...")
                    val probeBudget = AtomicInteger(6)

                    suspend fun scanSubrange(aIdx: Int, bIdx: Int): Pair<SegmentAvailabilityMap?, Boolean> {
                        if (probeBudget.getAndDecrement() <= 0) return null to false
                        ensureActive()
                        val sa = stops[aIdx]
                        val sb = stops[bIdx]
                        val subDep = sa.departureTime.ifBlank { sa.arrivalTime }.ifBlank { departureTime ?: "12:00" }
                        val subDate = RouteHelpers.computeSegmentDate(travelDate, sa.dayOffset)
                        return try {
                            val cars = withTimeout(60_000L) {
                                scanSingleRelation(
                                    worker = pool[0],
                                    fromStation = sa.name,
                                    toStation = sb.name,
                                    travelDate = subDate,
                                    departureTime = subDep,
                                    trainNumber = trainNumber,
                                    ticketClass = ticketClass,
                                    fromEva = sa.evaCode,
                                    toEva = sb.evaCode
                                )
                            }
                            if (cars.values.any { it.isNotEmpty() }) {
                                SegmentAvailabilityMap(
                                    segmentIndex = 0,
                                    fromStation = sa.name,
                                    toStation = sb.name,
                                    departureTime = subDep,
                                    durationMin = RouteHelpers.computeDurationMin(sa, sb, 30 * (bIdx - aIdx).coerceAtLeast(1)),
                                    fromEva = sa.evaCode,
                                    toEva = sb.evaCode,
                                    segmentDate = subDate,
                                    carriages = cars,
                                    status = "OK"
                                ) to false
                            } else null to false
                        } catch (e: CancellationException) {
                            throw e
                        } catch (e: GraphicReservationException) {
                            null to GrmParser.isSeatmapWagonsUnavailableError(e.message)
                        } catch (_: Exception) {
                            null to false
                        }
                    }

                    suspend fun trySplit(startIdx: Int, endIdx: Int, depth: Int): List<SegmentAvailabilityMap>? {
                        if ((endIdx - startIdx) < 2 || probeBudget.get() < 2) return null
                        val mid = (startIdx + endIdx) / 2

                        suspend fun resolveHalf(aIdx: Int, bIdx: Int): List<SegmentAvailabilityMap>? {
                            val (seg, unavail) = scanSubrange(aIdx, bIdx)
                            if (seg != null) return listOf(seg)
                            if (unavail && depth < 2 && (bIdx - aIdx) >= 4 && probeBudget.get() >= 2) {
                                return trySplit(aIdx, bIdx, depth + 1)
                            }
                            return null
                        }

                        val left = resolveHalf(startIdx, mid) ?: return null
                        val right = resolveHalf(mid, endIdx) ?: return null
                        return left + right
                    }

                    val macroSegments = trySplit(0, numSegments, 1)
                    if (!macroSegments.isNullOrEmpty()) {
                        val commonSeats = macroSegments
                            .map { seg ->
                                seg.carriages.flatMap { (w, seats) -> seats.map { s -> w to s } }.toSet()
                            }
                            .reduce { acc, set -> acc.intersect(set) }

                        if (commonSeats.isNotEmpty()) {
                            val indexed = macroSegments.mapIndexed { idx, s -> s.copy(segmentIndex = idx) }
                            onProgress(100, "Szybkie sprawdzanie (podział trasy na ${indexed.size} odcinki): znaleziono miejsce na 100% trasy!")
                            return@coroutineScope FullTrainAvailabilityMap(
                                trainNumber = trainNumber,
                                travelDate = travelDate,
                                fastPathUsed = true,
                                totalRouteSegments = numSegments,
                                segments = indexed
                            )
                        }
                    }
                    onProgress(24, "Podział trasy nie znalazł jednego miejsca na całość - skanuję wszystkie $numSegments odcinków...")
                }
            }

            // 3. Full segment-by-segment scan using pool of max 2 WebViews
            val sem = Semaphore(concurrency)
            val availablePool = ArrayDeque<WorkerWebView>(pool)
            val poolLock = Any()
            val progressBase = if (fastPath && numSegments > 1) 24 else 15
            val completed = AtomicInteger(0)

            val scannedList = (0 until numSegments).map { idx ->
                async {
                    sem.withPermit {
                        ensureActive()
                        val worker = synchronized(poolLock) { availablePool.removeFirst() }
                        try {
                            val sFrom = stops[idx]
                            val sTo = stops[idx + 1]
                            val segDep = sFrom.departureTime.ifBlank { sFrom.arrivalTime }.ifBlank { departureTime ?: "12:00" }
                            val segDate = RouteHelpers.computeSegmentDate(travelDate, sFrom.dayOffset)
                            val segDur = RouteHelpers.computeDurationMin(sFrom, sTo, 30)

                            var carriages: Map<String, List<String>> = emptyMap()
                            var status = "OK"
                            var errReason: String? = null

                            try {
                                carriages = withTimeout(75_000L) {
                                    scanSingleRelation(
                                        worker = worker,
                                        fromStation = sFrom.name,
                                        toStation = sTo.name,
                                        travelDate = segDate,
                                        departureTime = segDep,
                                        trainNumber = trainNumber,
                                        ticketClass = ticketClass,
                                        fromEva = sFrom.evaCode,
                                        toEva = sTo.evaCode,
                                        stepProgress = { msg -> onProgress(progressBase + (completed.get() * (96 - progressBase) / numSegments), "[${idx + 1}/$numSegments] $msg") }
                                    )
                                }
                                if (carriages.values.none { it.isNotEmpty() }) {
                                    status = "SOLD_OUT"
                                    errReason = "Brak wolnych miejsc w Klasie $ticketClass."
                                    carriages = emptyMap()
                                }
                            } catch (e: CancellationException) {
                                throw e
                            } catch (e: GraphicReservationException) {
                                status = if (GrmParser.isSoldOutReason(e.message)) "SOLD_OUT" else "SCRAPE_ERROR"
                                errReason = e.message
                                carriages = emptyMap()
                            } catch (e: Exception) {
                                status = "SCRAPE_ERROR"
                                errReason = e.message
                                carriages = emptyMap()
                            }

                            val doneNow = completed.incrementAndGet()
                            val pct = (progressBase + (doneNow * (96 - progressBase) / numSegments)).coerceIn(progressBase, 96)
                            onProgress(pct, "Ukończono odcinek $doneNow/$numSegments: ${sFrom.name} -> ${sTo.name}")

                            SegmentAvailabilityMap(
                                segmentIndex = idx,
                                fromStation = sFrom.name,
                                toStation = sTo.name,
                                departureTime = segDep,
                                durationMin = segDur,
                                fromEva = sFrom.evaCode,
                                toEva = sTo.evaCode,
                                segmentDate = segDate,
                                carriages = carriages,
                                status = status,
                                errorReason = errReason
                            )
                        } finally {
                            synchronized(poolLock) { availablePool.addLast(worker) }
                        }
                    }
                }
            }.awaitAll().toMutableList()

            // 4. Tail & Head metropolitan boundary recovery
            fun isUnsellableMetroBoundary(seg: SegmentAvailabilityMap): Boolean {
                if (seg.status != "SCRAPE_ERROR") return false
                val errLow = (seg.errorReason ?: "").lowercase()
                if ("nie znaleziono pociągu" !in errLow || "po 35s" in errLow) return false
                if (!RouteHelpers.isSameMetroHub(seg.fromStation, seg.toStation)) return false
                return seg.durationMin <= 15
            }

            suspend fun scanMergedBoundary(
                first: SegmentAvailabilityMap,
                second: SegmentAvailabilityMap,
                stopFrom: StationStop,
                stopTo: StationStop
            ): SegmentAvailabilityMap? {
                val mergedDur = RouteHelpers.computeDurationMin(stopFrom, stopTo, first.durationMin + second.durationMin)
                return try {
                    val cars = withTimeout(60_000L) {
                        scanSingleRelation(
                            worker = pool[0],
                            fromStation = first.fromStation,
                            toStation = second.toStation,
                            travelDate = first.segmentDate,
                            departureTime = first.departureTime,
                            trainNumber = trainNumber,
                            ticketClass = ticketClass,
                            fromEva = first.fromEva,
                            toEva = second.toEva
                        )
                    }
                    SegmentAvailabilityMap(
                        segmentIndex = first.segmentIndex,
                        fromStation = first.fromStation,
                        toStation = second.toStation,
                        departureTime = first.departureTime,
                        durationMin = mergedDur,
                        fromEva = first.fromEva,
                        toEva = second.toEva,
                        segmentDate = first.segmentDate,
                        carriages = cars,
                        status = if (cars.values.any { it.isNotEmpty() }) "OK" else "SOLD_OUT",
                        errorReason = if (cars.values.any { it.isNotEmpty() }) null else "Brak wolnych miejsc w Klasie $ticketClass."
                    )
                } catch (_: Exception) {
                    null
                }
            }

            if (scannedList.size >= 2 && isUnsellableMetroBoundary(scannedList.last())) {
                val prevSeg = scannedList[scannedList.size - 2]
                val tailSeg = scannedList.last()
                onProgress(97, "Łączę niesprzedawalny odcinek końcowy aglomeracji (${prevSeg.fromStation} -> ${tailSeg.toStation})...")
                val merged = scanMergedBoundary(prevSeg, tailSeg, stops[stops.size - 3], stops.last())
                if (merged != null) {
                    scannedList.removeAt(scannedList.lastIndex)
                    scannedList[scannedList.lastIndex] = merged
                }
            }

            if (scannedList.size >= 2 && isUnsellableMetroBoundary(scannedList.first())) {
                val headSeg = scannedList[0]
                val nextSeg = scannedList[1]
                onProgress(98, "Łączę niesprzedawalny odcinek początkowy aglomeracji (${headSeg.fromStation} -> ${nextSeg.toStation})...")
                val merged = scanMergedBoundary(headSeg, nextSeg, stops[0], stops[2])
                if (merged != null) {
                    scannedList[0] = merged
                    scannedList.removeAt(1)
                }
            }

            val finalSegments = scannedList.mapIndexed { idx, s -> s.copy(segmentIndex = idx) }
            onProgress(100, "Zakończono skanowanie wszystkich odcinków!")
            FullTrainAvailabilityMap(
                trainNumber = trainNumber,
                travelDate = travelDate,
                fastPathUsed = false,
                totalRouteSegments = finalSegments.size,
                segments = finalSegments
            )
        } finally {
            onAttachDebugWebView?.invoke(null)
            for (w in pool) {
                destroyWorkerWebView(w)
            }
            mainHandler.post {
                try {
                    CookieManager.getInstance().removeAllCookies(null)
                    CookieManager.getInstance().flush()
                } catch (_: Exception) {
                }
            }
        }
    }
}
