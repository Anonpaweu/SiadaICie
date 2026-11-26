package pl.iccbagno.app.scraper

import android.annotation.SuppressLint
import android.app.ActivityManager
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
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.CompletableDeferred
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
import pl.iccbagno.app.data.StationsRepository
import pl.iccbagno.app.domain.FullTrainAvailabilityMap
import pl.iccbagno.app.domain.RouteHelpers
import pl.iccbagno.app.domain.SeatFilterPrefs
import pl.iccbagno.app.domain.SegmentAvailabilityMap
import pl.iccbagno.app.domain.StationStop
import java.io.ByteArrayInputStream
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference
import kotlin.coroutines.resume

open class GraphicReservationException(message: String) : Exception(message)

class EbiletWebViewScanner(
    private val context: Context,
    private val scheduleClient: ScheduleClient,
    private val onProgress: (Int, String) -> Unit,
    private val onAttachDebugWebView: ((WebView?) -> Unit)? = null
) {
    private val mainHandler = Handler(Looper.getMainLooper())
    private val hardwareProfile: Pair<Int, Int> by lazy {
        val cores = Runtime.getRuntime().availableProcessors().coerceAtLeast(1)
        val totalRamMb = try {
            val am = context.getSystemService(Context.ACTIVITY_SERVICE) as? ActivityManager
            val mi = ActivityManager.MemoryInfo()
            if (am != null) {
                am.getMemoryInfo(mi)
                mi.totalMem / (1024L * 1024L)
            } else 4096L
        } catch (_: Exception) {
            4096L
        }
        val resolved = resolveHardwareConcurrency(totalRamMb, cores)
        Log.i(
            TAG,
            "Hardware profile: RAM=${totalRamMb}MB, cores=$cores -> webViewConcurrency=${resolved.first}, directGrmConcurrency=${resolved.second}"
        )
        resolved
    }
    private val webViewConcurrency: Int get() = hardwareProfile.first
    private val directGrmConcurrency: Int get() = hardwareProfile.second
    private val stationsRepo by lazy { StationsRepository(context.applicationContext) }
    private val capturedGrmHeaders = ConcurrentHashMap<String, String>()
    private val capturedGrmTemplate = AtomicReference<GrmParser.GrmUrlTemplate?>(null)
    private val directGrmPending = ConcurrentHashMap<String, CompletableDeferred<String>>()
    private val directGrmSeq = AtomicInteger(0)

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

        /**
         * Resolves (webViewConcurrency, directGrmConcurrency) from device RAM and CPU cores,
         * bounded to avoid Android WebView single-renderer contention and Akamai WAF rate-limiting.
         */
        internal fun resolveHardwareConcurrency(totalRamMb: Long, cpuCores: Int): Pair<Int, Int> {
            return when {
                totalRamMb >= 10_000L && cpuCores >= 8 -> 3 to 4
                totalRamMb >= 5_500L && cpuCores >= 6 -> 3 to 3
                else -> 2 to 2
            }
        }

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
                    if (!/\d{1,2}:\d{2}/.test(txt)) return false;
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
                window.__iccGrmHeaders = window.__iccGrmHeaders || {};
                window.__iccNetInFlight = window.__iccNetInFlight || 0;
                window.__iccLastNetAt = window.__iccLastNetAt || 0;

                const isSafeGrmHeader = (k) => {
                    const kl = String(k || '').toLowerCase();
                    return kl === 'authorization' || kl === 'accept' || kl === 'accept-language' || kl.startsWith('x-');
                };

                const isGrmUrl = (low) => low.includes('/grm/sklad/wbnet/') || low.includes('/grm/wagon/svg/wbnet/');
                const isIntercityApi = (low) => low.includes('intercity.pl') && !low.endsWith('.js') && !low.endsWith('.css');

                const notifyHeaders = () => {
                    try {
                        if (window.AndroidGrmBridge && window.AndroidGrmBridge.onGrmHeaders) {
                            window.AndroidGrmBridge.onGrmHeaders(JSON.stringify(window.__iccGrmHeaders));
                        }
                    } catch (e) {}
                };

                const recordHeaderObj = (obj) => {
                    if (!obj) return;
                    let changed = false;
                    try {
                        if (typeof Headers !== 'undefined' && obj instanceof Headers) {
                            obj.forEach((v, k) => {
                                if (isSafeGrmHeader(k) && v != null) {
                                    window.__iccGrmHeaders[k] = String(v);
                                    changed = true;
                                }
                            });
                        } else if (Array.isArray(obj)) {
                            for (const pair of obj) {
                                if (Array.isArray(pair) && pair.length >= 2 && isSafeGrmHeader(pair[0]) && pair[1] != null) {
                                    window.__iccGrmHeaders[String(pair[0])] = String(pair[1]);
                                    changed = true;
                                }
                            }
                        } else if (typeof obj === 'object') {
                            for (const k of Object.keys(obj)) {
                                if (isSafeGrmHeader(k) && obj[k] != null) {
                                    window.__iccGrmHeaders[k] = String(obj[k]);
                                    changed = true;
                                }
                            }
                        }
                    } catch (e) {}
                    if (changed) notifyHeaders();
                };

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
                    const req = args[0];
                    const init = args[1];
                    const url = (typeof req === 'string' ? req : (req && req.url) || '');
                    const urlLow = String(url).toLowerCase();
                    if (isGrmUrl(urlLow)) {
                        if (req && typeof req === 'object' && req.headers) recordHeaderObj(req.headers);
                        if (init && init.headers) recordHeaderObj(init.headers);
                    }
                    const trackApi = isIntercityApi(urlLow);
                    if (trackApi) {
                        window.__iccNetInFlight = (window.__iccNetInFlight || 0) + 1;
                        window.__iccLastNetAt = Date.now();
                    }
                    try {
                        const resp = await origFetch.apply(this, args);
                        const finalUrl = resp.url || url || '';
                        const low = finalUrl.toLowerCase();
                        if (isIntercityApi(low)) {
                            window.__iccLastNetAt = Date.now();
                            notifyNet('FETCH ' + resp.status + ' ' + finalUrl.slice(0, 140));
                        }
                        if (resp && resp.status === 200 && isGrmUrl(low)) {
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
                    } finally {
                        if (trackApi) {
                            window.__iccNetInFlight = Math.max(0, (window.__iccNetInFlight || 1) - 1);
                            window.__iccLastNetAt = Date.now();
                        }
                    }
                };

                const origOpen = XMLHttpRequest.prototype.open;
                const origSetHeader = XMLHttpRequest.prototype.setRequestHeader;
                const origSend = XMLHttpRequest.prototype.send;
                XMLHttpRequest.prototype.open = function(method, url, ...rest) {
                    this.__iccUrl = String(url || '');
                    this.__iccReqHeaders = {};
                    return origOpen.call(this, method, url, ...rest);
                };
                XMLHttpRequest.prototype.setRequestHeader = function(header, value) {
                    try {
                        if (!this.__iccReqHeaders) this.__iccReqHeaders = {};
                        if (header) this.__iccReqHeaders[String(header)] = String(value);
                    } catch (e) {}
                    return origSetHeader.call(this, header, value);
                };
                XMLHttpRequest.prototype.send = function(...args) {
                    const urlLow = String(this.__iccUrl || '').toLowerCase();
                    try {
                        if (isGrmUrl(urlLow) && this.__iccReqHeaders) {
                            recordHeaderObj(this.__iccReqHeaders);
                        }
                    } catch (e) {}
                    const trackApi = isIntercityApi(urlLow);
                    if (trackApi) {
                        window.__iccNetInFlight = (window.__iccNetInFlight || 0) + 1;
                        window.__iccLastNetAt = Date.now();
                        this.addEventListener('loadend', function() {
                            window.__iccNetInFlight = Math.max(0, (window.__iccNetInFlight || 1) - 1);
                            window.__iccLastNetAt = Date.now();
                        });
                    }
                    this.addEventListener('load', function() {
                        try {
                            const u = String(this.responseURL || this.__iccUrl || '');
                            const low = u.toLowerCase();
                            if (isIntercityApi(low)) {
                                window.__iccLastNetAt = Date.now();
                                notifyNet('XHR ' + this.status + ' ' + u.slice(0, 140));
                            }
                            if (this.status === 200 && isGrmUrl(low)) {
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
        @Volatile var skladUrl: String? = null
        @Volatile var svgPrefix: String? = null
        @Volatile var svgSuffix: String? = null
        val svgsByWagon = ConcurrentHashMap<String, String>()
    }

    private data class WagonScanResult(
        val carriages: Map<String, List<String>>,
        val seatTags: Map<String, List<String>>
    )

    private sealed class DirectGrmResult {
        data class Ok(val scan: WagonScanResult) : DirectGrmResult()
        data class SoldOut(val reason: String) : DirectGrmResult()
        data class Fallback(val reason: String, val isAuthOrBlocked: Boolean = false) : DirectGrmResult()
    }

    private fun parseSvgPayloads(
        targets: List<Pair<String, String>>,
        svgLookup: (String) -> String?,
        ticketClass: Int
    ): WagonScanResult {
        val carriages = LinkedHashMap<String, List<String>>()
        val seatTags = LinkedHashMap<String, List<String>>()
        for ((wNo, _) in targets) {
            val svgXml = svgLookup(wNo)
            if (!svgXml.isNullOrBlank() && "<svg" in svgXml.lowercase()) {
                val (seats, perSeatTags) = GrmParser.parseGrmWagonSvgWithMeta(
                    svgXml = svgXml,
                    ticketClass = ticketClass,
                    includeWheelchair = true
                )
                carriages[wNo] = seats
                for ((sNo, tags) in perSeatTags) {
                    seatTags["${wNo}_$sNo"] = tags
                }
            }
        }
        return WagonScanResult(carriages = carriages, seatTags = seatTags)
    }

    private fun hasAnyAllowedSeat(scan: WagonScanResult, seatFilters: SeatFilterPrefs): Boolean {
        return scan.carriages.any { (wNo, seats) ->
            seats.any { sNo -> GrmParser.isSeatAllowed(scan.seatTags["${wNo}_$sNo"].orEmpty(), seatFilters) }
        }
    }

    private class GrmJsBridge(
        private val stateProvider: () -> SegmentCaptureState,
        private val onTemplateCaptured: (GrmParser.GrmUrlTemplate) -> Unit,
        private val onHeadersCaptured: (Map<String, String>) -> Unit,
        private val onDirectResult: (String, String) -> Unit
    ) {
        @JavascriptInterface
        fun onNetLog(msg: String) {
            Log.i(TAG, "[NET] $msg")
        }

        @JavascriptInterface
        fun onGrmHeaders(headersJson: String) {
            try {
                val obj = JSONObject(headersJson)
                val map = HashMap<String, String>()
                val keys = obj.keys()
                while (keys.hasNext()) {
                    val k = keys.next()
                    val v = obj.optString(k, "")
                    if (k.isNotBlank() && v.isNotBlank()) map[k] = v
                }
                if (map.isNotEmpty()) onHeadersCaptured(map)
            } catch (_: Exception) {
            }
        }

        @JavascriptInterface
        fun onDirectGrmResult(reqId: String, payloadJson: String) {
            onDirectResult(reqId, payloadJson)
        }

        @JavascriptInterface
        fun onSklad(url: String, jsonText: String) {
            try {
                val obj = JSONObject(jsonText)
                if (obj.has("klasa1") || obj.has("klasa2") || obj.has("wagonySchemat") || obj.has("wagony")) {
                    val st = stateProvider()
                    st.skladJson = obj
                    st.skladUrl = url
                    GrmParser.parseGrmUrlTemplate(url)?.let(onTemplateCaptured)
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
                    GrmParser.parseGrmUrlTemplate(null, url)?.let(onTemplateCaptured)
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
        @Volatile var captureState: SegmentCaptureState = SegmentCaptureState()
    )

    @SuppressLint("SetJavaScriptEnabled")
    private suspend fun createWorkerWebView(): WorkerWebView = withContext(Dispatchers.Main) {
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
        val worker = WorkerWebView(wv)
        val bridge = GrmJsBridge(
            stateProvider = { worker.captureState },
            onTemplateCaptured = { tmpl ->
                capturedGrmTemplate.compareAndSet(null, tmpl)
            },
            onHeadersCaptured = { hdrs ->
                capturedGrmHeaders.putAll(hdrs)
            },
            onDirectResult = { reqId, payload ->
                directGrmPending.remove(reqId)?.complete(payload)
            }
        )
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
        try {
            wv.onResume()
            wv.resumeTimers()
        } catch (_: Exception) {
        }

        worker
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

    private suspend fun closeCookieBanners(wv: WebView): Boolean {
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
        return evalJs(wv, js).trim() == "true"
    }

    private fun resolveStationEpa(stop: StationStop): String? {
        return try {
            val candidates = stationsRepo.searchStations(stop.name, limit = 10)
            val evaTrim = stop.evaCode.trim()
            val normName = RouteHelpers.normalizePolishName(stop.name)
            val byEva = if (evaTrim.isNotEmpty()) {
                candidates.firstOrNull { it.evaCode.trim() == evaTrim && !it.icId.isNullOrBlank() }
            } else null
            val chosen = byEva
                ?: candidates.firstOrNull { it.normalizedName == normName && !it.icId.isNullOrBlank() }
                ?: candidates.firstOrNull { !it.icId.isNullOrBlank() }
            chosen?.icId?.trim()?.takeIf { it.length >= 6 && it.all(Char::isDigit) }
        } catch (_: Exception) {
            null
        }
    }

    private suspend fun awaitDirectJs(
        wv: WebView,
        timeoutMs: Long,
        scriptBuilder: (reqId: String, headersJson: String) -> String
    ): String? {
        val reqId = "grm_${directGrmSeq.incrementAndGet()}"
        val deferred = CompletableDeferred<String>()
        directGrmPending[reqId] = deferred
        return try {
            val hdrObj = JSONObject()
            for ((k, v) in capturedGrmHeaders) {
                hdrObj.put(k, v)
            }
            val script = scriptBuilder(reqId, hdrObj.toString())
            withContext(Dispatchers.Main) {
                wv.evaluateJavascript(script, null)
            }
            withTimeoutOrNull(timeoutMs) { deferred.await() }
        } finally {
            directGrmPending.remove(reqId)
        }
    }

    private suspend fun webViewFetchGet(wv: WebView, url: String, timeoutMs: Long = 10_000L): String? {
        val raw = awaitDirectJs(wv, timeoutMs) { reqId, _ ->
            """
            (async function(reqId, targetUrl) {
                const reply = (obj) => {
                    try {
                        if (window.AndroidGrmBridge && window.AndroidGrmBridge.onDirectGrmResult) {
                            window.AndroidGrmBridge.onDirectGrmResult(reqId, JSON.stringify(obj));
                        }
                    } catch (e) {}
                };
                try {
                    const ctrl = new AbortController();
                    const timer = setTimeout(() => ctrl.abort(), 8000);
                    const r = await fetch(targetUrl, {
                        method: 'GET',
                        credentials: 'include',
                        signal: ctrl.signal
                    });
                    clearTimeout(timer);
                    const txt = await r.text();
                    reply({ ok: r.ok, status: r.status, text: txt });
                } catch (e) {
                    reply({ ok: false, status: 0, text: '', err: String(e) });
                }
            })(${JSONObject.quote(reqId)}, ${JSONObject.quote(url)});
            """.trimIndent()
        } ?: return null

        return try {
            val obj = JSONObject(raw)
            if (obj.optBoolean("ok", false)) {
                obj.optString("text", "")
            } else {
                Log.w(TAG, "webViewFetchGet HTTP ${obj.optInt("status", 0)} for $url: ${obj.optString("err", "")}")
                null
            }
        } catch (_: Exception) {
            null
        }
    }

    private suspend fun webViewFetchPostJson(
        wv: WebView,
        url: String,
        jsonBody: String,
        timeoutMs: Long = 20_000L
    ): String? {
        val raw = awaitDirectJs(wv, timeoutMs) { reqId, _ ->
            """
            (async function(reqId, targetUrl, bodyStr) {
                const reply = (obj) => {
                    try {
                        if (window.AndroidGrmBridge && window.AndroidGrmBridge.onDirectGrmResult) {
                            window.AndroidGrmBridge.onDirectGrmResult(reqId, JSON.stringify(obj));
                        }
                    } catch (e) {}
                };
                try {
                    const ctrl = new AbortController();
                    const timer = setTimeout(() => ctrl.abort(), 18000);
                    const r = await fetch(targetUrl, {
                        method: 'POST',
                        credentials: 'include',
                        headers: {
                            'Content-Type': 'application/json',
                            'Accept': 'application/json, text/plain, */*',
                            'app-version': '1.6.20'
                        },
                        body: bodyStr,
                        signal: ctrl.signal
                    });
                    clearTimeout(timer);
                    const txt = await r.text();
                    reply({ ok: r.ok, status: r.status, text: txt });
                } catch (e) {
                    reply({ ok: false, status: 0, text: '', err: String(e) });
                }
            })(${JSONObject.quote(reqId)}, ${JSONObject.quote(url)}, ${JSONObject.quote(jsonBody)});
            """.trimIndent()
        } ?: return null

        return try {
            val obj = JSONObject(raw)
            if (obj.optBoolean("ok", false)) {
                obj.optString("text", "")
            } else {
                Log.w(TAG, "webViewFetchPostJson HTTP ${obj.optInt("status", 0)} for $url: ${obj.optString("err", "")}")
                null
            }
        } catch (_: Exception) {
            null
        }
    }

    private suspend fun scanRelationViaDirectGrm(
        worker: WorkerWebView,
        template: GrmParser.GrmUrlTemplate,
        fromStop: StationStop,
        toStop: StationStop,
        fromEpa: String?,
        toEpa: String?,
        travelDate: String,
        fallbackDepTime: String,
        ticketClass: Int
    ): DirectGrmResult {
        if (RouteHelpers.isSameMetroHub(fromStop.name, toStop.name)) {
            return DirectGrmResult.Fallback("same metro hub")
        }
        if (fromEpa.isNullOrBlank() || toEpa.isNullOrBlank()) {
            return DirectGrmResult.Fallback("missing EPA code")
        }

        val depTime = fromStop.departureTime.ifBlank { fromStop.arrivalTime }.ifBlank { fallbackDepTime }
        val arrTime = toStop.arrivalTime.ifBlank { toStop.departureTime }.ifBlank { depTime }
        val depDate = RouteHelpers.computeSegmentDate(travelDate, fromStop.dayOffset)
        val arrDayOffset = GrmParser.computeArrivalDayOffset(toStop.arrivalTime, toStop.departureTime, toStop.dayOffset)
        val arrDate = RouteHelpers.computeSegmentDate(travelDate, arrDayOffset)

        val depDt = GrmParser.formatGrmDateTime(depDate, depTime)
            ?: return DirectGrmResult.Fallback("invalid dep datetime")
        val arrDt = GrmParser.formatGrmDateTime(arrDate, arrTime)
            ?: return DirectGrmResult.Fallback("invalid arr datetime")

        val skladUrl = GrmParser.buildGrmSkladUrl(template, fromEpa, toEpa, depDt, arrDt)
        val skladRaw = awaitDirectJs(worker.webView, timeoutMs = 8_000L) { reqId, hdrsJson ->
            """
            (async function(reqId, targetUrl, extraHdrs) {
                const reply = (obj) => {
                    try {
                        if (window.AndroidGrmBridge && window.AndroidGrmBridge.onDirectGrmResult) {
                            window.AndroidGrmBridge.onDirectGrmResult(reqId, JSON.stringify(obj));
                        }
                    } catch (e) {}
                };
                try {
                    const hdrs = Object.assign({}, extraHdrs || {}, window.__iccGrmHeaders || {});
                    const ctrl = new AbortController();
                    const timer = setTimeout(() => ctrl.abort(), 6500);
                    const r = await fetch(targetUrl, {
                        method: 'GET',
                        credentials: 'include',
                        headers: hdrs,
                        signal: ctrl.signal
                    });
                    clearTimeout(timer);
                    const txt = await r.text();
                    reply({ ok: r.ok, status: r.status, text: txt });
                } catch (e) {
                    reply({ ok: false, status: 0, text: '', err: String(e) });
                }
            })(${JSONObject.quote(reqId)}, ${JSONObject.quote(skladUrl)}, $hdrsJson);
            """.trimIndent()
        } ?: return DirectGrmResult.Fallback("sklad timeout")

        val skladResp = try {
            JSONObject(skladRaw)
        } catch (_: Exception) {
            return DirectGrmResult.Fallback("invalid sklad bridge payload")
        }
        val status = skladResp.optInt("status", 0)
        if (status == 401 || status == 403) {
            return DirectGrmResult.Fallback("HTTP $status", isAuthOrBlocked = true)
        }
        if (!skladResp.optBoolean("ok", false)) {
            return DirectGrmResult.Fallback("HTTP $status")
        }
        val skladText = skladResp.optString("text", "")
        if (skladText.isBlank()) {
            return DirectGrmResult.Fallback("empty sklad body")
        }
        val skladJson = try {
            JSONObject(skladText)
        } catch (_: Exception) {
            return DirectGrmResult.Fallback("non-JSON sklad")
        }
        if (!skladJson.has("klasa1") && !skladJson.has("klasa2") && !skladJson.has("wagonySchemat") && !skladJson.has("wagony")) {
            return DirectGrmResult.Fallback("unexpected sklad schema")
        }

        val (targets, hasKey) = GrmParser.selectGrmTargetWagons(skladJson, ticketClass)
        if (targets.isEmpty()) {
            return if (hasKey) {
                DirectGrmResult.SoldOut(
                    "BRAK_MIEJSC_KLASA_$ticketClass: Brak dostępnych wagonów Klasy $ticketClass w składzie."
                )
            } else {
                DirectGrmResult.Fallback("no targets and no class key")
            }
        }

        val reqArr = JSONArray()
        for ((wNo, schemaId) in targets) {
            val svgUrl = GrmParser.buildGrmSvgUrl(template, wNo, schemaId, fromEpa, toEpa, depDt, arrDt)
            reqArr.put(JSONObject().put("wNo", wNo).put("url", svgUrl))
        }

        val svgBatchRaw = awaitDirectJs(worker.webView, timeoutMs = 18_000L) { reqId, hdrsJson ->
            """
            (async function(reqId, items, extraHdrs) {
                const reply = (obj) => {
                    try {
                        if (window.AndroidGrmBridge && window.AndroidGrmBridge.onDirectGrmResult) {
                            window.AndroidGrmBridge.onDirectGrmResult(reqId, JSON.stringify(obj));
                        }
                    } catch (e) {}
                };
                try {
                    const hdrs = Object.assign({}, extraHdrs || {}, window.__iccGrmHeaders || {});
                    const svgs = {};
                    const fail = [];
                    let idx = 0;

                    const fetchOne = async (it) => {
                        for (let tryNum = 0; tryNum < 2; tryNum++) {
                            try {
                                const ctrl = new AbortController();
                                const timer = setTimeout(() => ctrl.abort(), 6500);
                                const r = await fetch(it.url, {
                                    method: 'GET',
                                    credentials: 'include',
                                    headers: hdrs,
                                    signal: ctrl.signal
                                });
                                clearTimeout(timer);
                                if (r.ok) {
                                    const txt = await r.text();
                                    if (txt && txt.toLowerCase().includes('<svg')) {
                                        svgs[it.wNo] = txt;
                                        return;
                                    }
                                } else if (tryNum === 1) {
                                    fail.push(it.wNo + ':' + r.status);
                                    return;
                                }
                            } catch (e) {
                                if (tryNum === 1) {
                                    fail.push(it.wNo + ':err');
                                    return;
                                }
                            }
                            await new Promise(res => setTimeout(res, 150));
                        }
                    };

                    const workerCount = Math.min(3, items.length);
                    await Promise.all(Array.from({ length: workerCount }, async () => {
                        while (idx < items.length) {
                            const current = items[idx++];
                            await fetchOne(current);
                        }
                    }));

                    reply({ svgs: svgs, fail: fail });
                } catch (e) {
                    reply({ svgs: {}, fail: ['batch_err:' + String(e)] });
                }
            })(${JSONObject.quote(reqId)}, $reqArr, $hdrsJson);
            """.trimIndent()
        } ?: return DirectGrmResult.Fallback("svg batch timeout")

        val svgsObj = try {
            JSONObject(svgBatchRaw).optJSONObject("svgs")
        } catch (_: Exception) {
            null
        } ?: return DirectGrmResult.Fallback("invalid svg batch payload")

        val scan = parseSvgPayloads(targets, { wNo -> svgsObj.optString(wNo, "") }, ticketClass)
        val result = scan.carriages

        return when {
            result.size == targets.size -> {
                if (result.values.none { it.isNotEmpty() }) {
                    DirectGrmResult.SoldOut(
                        "BRAK_MIEJSC_KLASA_$ticketClass: Wszystkie dostępne wagony Klasy $ticketClass (${result.keys.joinToString(", ")}) mają 0 wolnych miejsc."
                    )
                } else {
                    DirectGrmResult.Ok(scan)
                }
            }
            result.values.any { it.isNotEmpty() } -> {
                Log.w(TAG, "[DIRECT GRM] Partial wagons ${result.size}/${targets.size} for ${fromStop.name} -> ${toStop.name}, returning available seats")
                DirectGrmResult.Ok(scan)
            }
            else -> {
                DirectGrmResult.Fallback("incomplete SVGs ${result.size}/${targets.size}")
            }
        }
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
    ): WagonScanResult {
        val freshState = SegmentCaptureState()
        worker.captureState = freshState

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
                evalJs(worker.webView, "window.__iccStalePage = true;")
                withContext(Dispatchers.Main) {
                    if (attempt > 1) {
                        worker.webView.loadUrl("about:blank")
                    }
                    worker.webView.loadUrl(searchUrl)
                }

                // Wait up to 35s for connection cards (smart reload after 14s idle or 20s hard stall)
                val startWait = System.currentTimeMillis()
                var reloaded = false
                var cardsReady = false
                var cookieDismissed = false
                var lastCookieCheckAt = 0L
                var lastDiagAt = 0L
                val trainDigitsOnly = TRAIN_DIGITS_REGEX.find(trainNumber)?.groupValues?.get(1).orEmpty()
                while (System.currentTimeMillis() - startWait < 35_000L) {
                    coroutineScope { ensureActive() }
                    val nowLoopMs = System.currentTimeMillis()
                    if (!cookieDismissed && (nowLoopMs - lastCookieCheckAt >= 600L)) {
                        lastCookieCheckAt = nowLoopMs
                        if (closeCookieBanners(worker.webView)) {
                            cookieDismissed = true
                        }
                    }
                    val probeRaw = evalJs(
                        worker.webView,
                        """
                        (function(tNum) {
                            if (window.__iccStalePage || location.href === 'about:blank') {
                                return JSON.stringify({ cards: 0, loaders: 1, w: window.innerWidth, inFlight: 0, netIdleMs: 999999, sample: '', body: '' });
                            }
                            $FIND_CARDS_JS
                            const cards = __iccFindCards();
                            const loaders = document.querySelectorAll("[class*='trainLoaderWithText' i]");
                            const body = (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').trim();
                            const inFlight = window.__iccNetInFlight || 0;
                            const netIdleMs = window.__iccLastNetAt ? (Date.now() - window.__iccLastNetAt) : 999999;
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
                            return JSON.stringify({ cards: cards.length, loaders: loaders.length, w: window.innerWidth, inFlight: inFlight, netIdleMs: netIdleMs, sample: sample.slice(0, 400), body: body.slice(0, 600) });
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
                    val inFlight = probeJson.optInt("inFlight", 0)
                    val netIdleMs = probeJson.optLong("netIdleMs", 999_999L)
                    val bodyRaw = probeJson.optString("body", "")
                    val sampleRaw = probeJson.optString("sample", "")
                    val bodyLow = bodyRaw.lowercase()
                    val elapsedMs = System.currentTimeMillis() - startWait

                    if (elapsedMs - lastDiagAt >= 3_500L || sampleRaw.isNotEmpty()) {
                        lastDiagAt = elapsedMs
                        Log.i(TAG, "[$fromStation -> $toStation] t=${elapsedMs}ms cards=$cardCount loaders=$loaderCount inFlight=$inFlight idle=${netIdleMs}ms w=${probeJson.optInt("w", -1)} sample='$sampleRaw' body='${bodyRaw.take(180)}'")
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

                    val shouldSmartReload = !reloaded && cardCount == 0 && (
                        (elapsedMs > 14_000L && inFlight == 0 && netIdleMs >= 3_000L) ||
                            elapsedMs > 20_000L
                    )
                    if (shouldSmartReload) {
                        reloaded = true
                        Log.i(TAG, "[$fromStation -> $toStation] List stalled at ${elapsedMs}ms (cards=$cardCount, loaders=$loaderCount, inFlight=$inFlight, netIdleMs=$netIdleMs), reloading...")
                        stepProgress("Przeładowanie listy połączeń ($fromStation -> $toStation)...")
                        withContext(Dispatchers.Main) { worker.webView.reload() }
                    }
                    delay(150)
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
                        const idRe = new RegExp('(?:_|\\|)' + num + '(?:_|\\||$)');
                        const wordRe = new RegExp('\\b' + num + '\\b');
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
                                        if ((b.innerText || '').toLowerCase().includes(lbl.toLowerCase()) && b.offsetParent !== null && !b.disabled) {
                                            b.click();
                                            return true;
                                        }
                                    }
                                    return false;
                                })(${JSONObject.quote(dirLabel)});
                                """.trimIndent()
                            )
                            if (clickedRaw.trim() != "true") break
                            val pagStart = System.currentTimeMillis()
                            while (System.currentTimeMillis() - pagStart < 2_500L) {
                                delay(150)
                                matchedIdx = evalJs(worker.webView, matchJs).trim().toIntOrNull() ?: -1
                                if (matchedIdx >= 0) break@outer
                                val loadersNow = evalJs(
                                    worker.webView,
                                    "document.querySelectorAll(\"[class*='trainLoaderWithText' i]\").length"
                                ).trim().toIntOrNull() ?: 0
                                if (loadersNow == 0 && System.currentTimeMillis() - pagStart >= 600L) break
                            }
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

                // Wait for card price loader and actionable content to be ready
                val priceWaitStart = System.currentTimeMillis()
                while (System.currentTimeMillis() - priceWaitStart < 12_000L) {
                    coroutineScope { ensureActive() }
                    val readyRaw = evalJs(
                        worker.webView,
                        """
                        (function(idx) {
                            $FIND_CARDS_JS
                            const cards = __iccFindCards();
                            const card = cards[idx];
                            if (!card) return true;
                            const txt = (card.innerText || '').toLowerCase();
                            if (txt.includes('ładowanie ceny') || txt.includes('ladowanie ceny')) return false;
                            const visibleLoaders = Array.from(card.querySelectorAll("[class*='skeleton' i], [class*='loader' i], [class*='spinner' i]"))
                                .filter(el => el.offsetParent !== null);
                            if (visibleLoaders.length > 0) return false;
                            const isSummaryBtn = (low) =>
                                (low.includes('klasa 1') || low.includes('1 klas')) &&
                                (low.includes('klasa 2') || low.includes('2 klas'));
                            const hasDirectClassBtn = Array.from(card.querySelectorAll("button, [role='button']")).some(b => {
                                if (b.offsetParent === null) return false;
                                const t = ((b.innerText || '') + ' ' + (b.getAttribute('aria-label') || '')).toLowerCase();
                                return !isSummaryBtn(t) && (t.includes('klasa 1') || t.includes('klasa 2') || t.includes('1 klas') || t.includes('2 klas'));
                            });
                            const hasDesktopBtn = !!card.querySelector("button[aria-label*='Kup bilet'], button[data-testid='OutlineButton']");
                            const hasActionableText = txt.includes('zł') || txt.includes('pln') ||
                                txt.includes('brak miejsc') || txt.includes('niedostępn') ||
                                txt.includes('wyprzedan') || txt.includes('kup bilet') || txt.includes('wybierz');
                            if (!hasDirectClassBtn && !hasDesktopBtn && !hasActionableText) return false;
                            const primaryBtn = Array.from(card.querySelectorAll("button, [role='button']"))
                                .find(b => b.offsetParent !== null);
                            if (primaryBtn && (primaryBtn.disabled || primaryBtn.getAttribute('aria-disabled') === 'true')) {
                                return false;
                            }
                            return true;
                        })($matchedIdx);
                        """.trimIndent()
                    )
                    if (readyRaw.trim() == "true") break
                    delay(150)
                }

                // Expand card + select class (with retry if card collapses or ignores first tap)
                val expandCardJs = """
                    (function(idx) {
                        $FIND_CARDS_JS
                        const cards = __iccFindCards();
                        const card = cards[idx];
                        if (!card) return 'NO_CARD';
                        const isSummaryBtn = (low) =>
                            (low.includes('klasa 1') || low.includes('1 klas')) &&
                            (low.includes('klasa 2') || low.includes('2 klas'));
                        const isEnabledVisible = (b) =>
                            b && b.offsetParent !== null && !b.disabled && b.getAttribute('aria-disabled') !== 'true';
                        const hasDirectClassBtn = Array.from(card.querySelectorAll("button, [role='button']")).some(b => {
                            if (!isEnabledVisible(b)) return false;
                            const t = ((b.innerText || '') + ' ' + (b.getAttribute('aria-label') || '')).toLowerCase();
                            if (isSummaryBtn(t)) return false;
                            return t.includes('klasa 1') || t.includes('klasa 2') || t.includes('1 klas') || t.includes('2 klas');
                        });
                        if (hasDirectClassBtn) return 'DIRECT_READY';

                        let chosen = null;
                        const expSels = ["button[data-testid='OutlineButton']", "button[aria-label*='Kup bilet']"];
                        for (const s of expSels) {
                            const b = card.querySelector(s);
                            if (isEnabledVisible(b)) { chosen = b; break; }
                        }
                        if (!chosen) {
                            for (const b of card.querySelectorAll("button, [role='button']")) {
                                if (!isEnabledVisible(b)) continue;
                                const t = ((b.innerText || '') + ' ' + (b.getAttribute('aria-label') || '')).toLowerCase();
                                if (isSummaryBtn(t) || t.includes('kup bilet') || t.includes('wybierz')) {
                                    chosen = b;
                                    break;
                                }
                            }
                        }
                        if (!chosen) {
                            for (const b of card.querySelectorAll("button, [role='button']")) {
                                if (isEnabledVisible(b)) { chosen = b; break; }
                            }
                        }
                        if (!chosen) return 'EXPANDED_NOOP';
                        try { chosen.scrollIntoView({ block: 'center' }); } catch (e) {}
                        chosen.click();
                        const lbl = (chosen.tagName + ':' + (chosen.innerText || chosen.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim()).slice(0, 90);
                        return 'EXPANDED_CLICKED:' + lbl;
                    })($matchedIdx);
                """.trimIndent()

                val expandStatus = decodeJsString(evalJs(worker.webView, expandCardJs))
                Log.i(TAG, "[$fromStation -> $toStation] Expand status: $expandStatus")
                if (expandStatus == "NO_CARD") {
                    throw GraphicReservationException("SCRAPE_ERROR: Kafelek pociągu zniknął dla $fromStation -> $toStation.")
                }

                // Wait for class buttons to render after expanding (re-try expand up to 3 times if card stays closed)
                var selectClassResult = ""
                var lastExpandAt = System.currentTimeMillis()
                var reExpandAttempts = 0
                val classWaitStart = System.currentTimeMillis()
                var firstClassCheck = true
                while (System.currentTimeMillis() - classWaitStart < 6_500L) {
                    coroutineScope { ensureActive() }
                    if (firstClassCheck) {
                        firstClassCheck = false
                        if (expandStatus != "DIRECT_READY") delay(100)
                    } else {
                        delay(150)
                    }
                    selectClassResult = decodeJsString(
                        evalJs(
                            worker.webView,
                            """
                            (function(idx, tClass) {
                                $FIND_CARDS_JS
                                const cards = __iccFindCards();
                                const card = cards[idx] || null;
                                const isSummaryBtn = (low) =>
                                    (low.includes('klasa 1') || low.includes('1 klas')) &&
                                    (low.includes('klasa 2') || low.includes('2 klas'));
                                const targets = tClass === 1
                                    ? ["button[aria-label*='1 klas']", "button[aria-label*='klasa 1' i]", "[data-testid='SuperPromo-select-seat-type-0']", "1 klasa", "klasa 1", "1 kl"]
                                    : ["button[aria-label*='2 klas']", "button[aria-label*='klasa 2' i]", "[data-testid='SuperPromo-select-seat-type-1']", "2 klasa", "klasa 2", "2 kl"];
                                const roots = card ? [card, document] : [document];
                                for (const root of roots) {
                                    for (const t of targets) {
                                        const isCss = t.startsWith('button') || t.startsWith('[');
                                        let btn = null;
                                        if (isCss) {
                                            btn = Array.from(root.querySelectorAll(t)).find(b => {
                                                if (b.offsetParent === null) return false;
                                                const low = ((b.innerText || '') + ' ' + (b.getAttribute('aria-label') || '')).toLowerCase();
                                                return !isSummaryBtn(low);
                                            }) || null;
                                        } else {
                                            btn = Array.from(root.querySelectorAll("button, [role='button'], a")).find(b => {
                                                if (b.offsetParent === null) return false;
                                                const low = ((b.innerText || '') + ' ' + (b.getAttribute('aria-label') || '')).toLowerCase();
                                                return low.includes(t) && !isSummaryBtn(low);
                                            }) || null;
                                        }
                                        if (btn) {
                                            const txt = (btn.innerText || '').toLowerCase();
                                            if (txt.includes('brak miejsc') || txt.includes('niedostępne') || txt.includes('zablokowana') || txt.includes('wyprzedane')) {
                                                return 'SOLD_OUT';
                                            }
                                            btn.click();
                                            return 'CLICKED:' + (btn.innerText || btn.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
                                        }
                                    }
                                }
                                const btns = Array.from(document.querySelectorAll("button, [role='button']"))
                                    .filter(b => b.offsetParent !== null)
                                    .map(b => ((b.innerText || b.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim()))
                                    .filter(Boolean)
                                    .slice(0, 12)
                                    .join(' | ');
                                return 'NOT_FOUND:url=' + location.href + ' btns=' + btns.slice(0, 300);
                            })($matchedIdx, $ticketClass);
                            """.trimIndent()
                        )
                    )
                    if (selectClassResult.startsWith("CLICKED:") || selectClassResult == "SOLD_OUT") {
                        break
                    }
                    val nowMs = System.currentTimeMillis()
                    if (expandStatus != "DIRECT_READY" && reExpandAttempts < 3 && (nowMs - lastExpandAt >= 900L)) {
                        reExpandAttempts++
                        lastExpandAt = nowMs
                        val retryExp = decodeJsString(evalJs(worker.webView, expandCardJs))
                        Log.i(TAG, "[$fromStation -> $toStation] Retry expand #$reExpandAttempts: $retryExp")
                    }
                }
                Log.i(TAG, "[$fromStation -> $toStation] Class selection: $selectClassResult")
                if (selectClassResult == "SOLD_OUT") {
                    throw GraphicReservationException("BRAK_MIEJSC_KLASA_$ticketClass: Wyczerpano pulę miejsc w Klasie $ticketClass na tym odcinku.")
                }
                if (!selectClassResult.startsWith("CLICKED:")) {
                    throw GraphicReservationException("SCRAPE_ERROR: Nie udało się wybrać Klasy $ticketClass dla $fromStation -> $toStation ($selectClassResult)")
                }

                // Wait and click seatmap button on Your Journey page + ensure hook is installed
                evalJs(worker.webView, GRM_HOOK_JS)
                var schemaClicked = false
                val schemaWaitStart = System.currentTimeMillis()
                while (System.currentTimeMillis() - schemaWaitStart < 8_000L) {
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
                    delay(150)
                }
                if (!schemaClicked) {
                    val schemaDiag = decodeJsString(
                        evalJs(
                            worker.webView,
                            """
                            (function() {
                                const btns = Array.from(document.querySelectorAll('button, a'))
                                    .filter(b => b.offsetParent !== null)
                                    .map(b => ((b.innerText || b.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim()))
                                    .filter(Boolean)
                                    .slice(0, 14)
                                    .join(' | ');
                                return 'url=' + location.href + ' btns=' + btns.slice(0, 350);
                            })();
                            """.trimIndent()
                        )
                    )
                    Log.w(TAG, "[$fromStation -> $toStation] Seatmap button NOT clicked after 8s: $schemaDiag")
                } else {
                    Log.i(TAG, "[$fromStation -> $toStation] Seatmap button clicked OK")
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
                    delay(120)
                }

                val sklad = freshState.skladJson
                var incompleteGrmStatus: String? = null
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
                                    const hdrs = window.__iccGrmHeaders || {};
                                    const okList = [];
                                    const failList = [];
                                    let idx = 0;

                                    const fetchOne = async (it) => {
                                        for (let tryNum = 0; tryNum < 2; tryNum++) {
                                            try {
                                                const ctrl = new AbortController();
                                                const timer = setTimeout(() => ctrl.abort(), 9000);
                                                const r = await fetch(it.url, {
                                                    method: 'GET',
                                                    credentials: 'include',
                                                    headers: hdrs,
                                                    signal: ctrl.signal
                                                });
                                                clearTimeout(timer);
                                                if (r.ok) {
                                                    const txt = await r.text();
                                                    if (txt && txt.toLowerCase().includes('<svg')) {
                                                        if (window.AndroidGrmBridge) {
                                                            window.AndroidGrmBridge.onSvg(it.url, txt);
                                                        }
                                                        okList.push(it.wNo);
                                                        return;
                                                    }
                                                } else if (tryNum === 1) {
                                                    failList.push(it.wNo + ':' + r.status);
                                                    return;
                                                }
                                            } catch (e) {
                                                if (tryNum === 1) {
                                                    failList.push(it.wNo + ':err');
                                                    return;
                                                }
                                            }
                                            await new Promise(res => setTimeout(res, 200));
                                        }
                                    };

                                    const workerCount = Math.min(3, items.length);
                                    await Promise.all(Array.from({ length: workerCount }, async () => {
                                        while (idx < items.length) {
                                            const current = items[idx++];
                                            await fetchOne(current);
                                        }
                                    }));

                                    window.__iccSvgBatch = JSON.stringify({ done: true, ok: okList, fail: failList });
                                    return true;
                                })(${reqArr});
                            """.trimIndent()
                            evalJs(worker.webView, fetchJs)

                            val batchWait = System.currentTimeMillis()
                            var batchStatusRaw = ""
                            while (System.currentTimeMillis() - batchWait < 20_000L) {
                                coroutineScope { ensureActive() }
                                val doneRaw = evalJs(worker.webView, "window.__iccSvgBatch")
                                if (doneRaw != "null" && doneRaw.isNotBlank()) {
                                    batchStatusRaw = decodeJsString(doneRaw)
                                    break
                                }
                                delay(100)
                            }
                            Log.i(TAG, "[$fromStation -> $toStation] SVG batch status=$batchStatusRaw, svgsByWagon=${freshState.svgsByWagon.keys}")
                        }

                        val scan = parseSvgPayloads(targets, { wNo -> freshState.svgsByWagon[wNo] }, ticketClass)
                        val result = scan.carriages
                        if (result.size == targets.size) {
                            Log.i(TAG, "[$fromStation -> $toStation] Parsed seats OK: ${result.mapValues { it.value.size }}")
                            if (result.values.none { it.isNotEmpty() }) {
                                throw GraphicReservationException(
                                    "BRAK_MIEJSC_KLASA_$ticketClass: Wszystkie dostępne wagony Klasy $ticketClass (${result.keys.joinToString(", ")}) mają 0 wolnych miejsc."
                                )
                            }
                            return scan
                        } else if (result.values.any { it.isNotEmpty() }) {
                            Log.w(TAG, "[$fromStation -> $toStation] Parsed partial GRM wagons ${result.size}/${targets.size}, but found free seats - returning result: ${result.mapValues { it.value.size }}")
                            return scan
                        } else {
                            Log.w(TAG, "[$fromStation -> $toStation] Incomplete GRM fetch ${result.size}/${targets.size} with 0 seats; falling back to DOM")
                            incompleteGrmStatus = "${result.size}/${targets.size}"
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
                    val (seats, perSeatTags) = GrmParser.parseGrmWagonSvgWithMeta(domSvgRaw, ticketClass, includeWheelchair = true)
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
                        val tagsMap = perSeatTags.mapKeys { (sNo, _) -> "${activeWagon}_$sNo" }
                        return WagonScanResult(carriages = mapOf(activeWagon to seats), seatTags = tagsMap)
                    }
                }

                val errMsg = when {
                    !schemaClicked -> "SCRAPE_ERROR: Brak przycisku otwarcia schematu dla $fromStation -> $toStation."
                    incompleteGrmStatus != null -> "SCRAPE_ERROR: Niepełny odczyt schematu GRM ($incompleteGrmStatus wagonów) dla $fromStation -> $toStation."
                    else -> "BRAK_MIEJSC_KLASA_$ticketClass: Brak wolnych miejsc na schemacie."
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
        fastPath: Boolean = true,
        seatFilters: SeatFilterPrefs = SeatFilterPrefs()
    ): FullTrainAvailabilityMap = coroutineScope {
        onProgress(5, "Pobieranie trasy i stacji pośrednich pociągu $trainNumber...")
        val stops = scheduleClient.resolveRealRoute(fromStation, toStation, travelDate, trainNumber, departureTime)
        if (stops.size < 2) {
            throw GraphicReservationException("Nie udało się wyznaczyć trasy dla pociągu $trainNumber.")
        }

        val numSegments = stops.size - 1
        onProgress(10, "Wykryto $numSegments odcinków trasy.")

        capturedGrmHeaders.clear()
        capturedGrmTemplate.set(null)
        directGrmPending.clear()
        val stopEpaByIdx: List<String?> = withContext(Dispatchers.IO) {
            stops.map { resolveStationEpa(it) }
        }

        val pool = ArrayList<WorkerWebView>(webViewConcurrency)
        try {
            val firstWorker = createWorkerWebView()
            pool.add(firstWorker)
            onAttachDebugWebView?.invoke(firstWorker.webView)

            // Dynamic warmup on e-IC home page (capped at 1200ms) so Akamai & Cookiebot initialize naturally
            onProgress(12, "Inicjalizacja sesji e-IC i ciasteczek...")
            Log.i(TAG, "Warmup: loading https://ebilet.intercity.pl/")
            withContext(Dispatchers.Main) {
                pool[0].webView.loadUrl("https://ebilet.intercity.pl/")
            }
            val warmupStart = System.currentTimeMillis()
            var warmupBannerClicked = false
            while (System.currentTimeMillis() - warmupStart < 1_200L) {
                ensureActive()
                delay(120)
                val readyState = decodeJsString(evalJs(pool[0].webView, "document.readyState"))
                if (readyState == "interactive" || readyState == "complete") {
                    if (closeCookieBanners(pool[0].webView)) {
                        warmupBannerClicked = true
                        break
                    }
                    if (readyState == "complete" && System.currentTimeMillis() - warmupStart >= 450L) {
                        break
                    }
                }
            }
            if (!warmupBannerClicked) {
                warmupBannerClicked = closeCookieBanners(pool[0].webView)
            }
            if (warmupBannerClicked) {
                delay(100)
            }
            Log.i(TAG, "Warmup finished in ${System.currentTimeMillis() - warmupStart}ms (bannerClicked=$warmupBannerClicked)")

            // 0. Check if train has graphical seatmap via WebView Chromium network stack
            val webViewTransport = object : NonGrmScanner.EicHttpTransport {
                override suspend fun get(url: String): String? = webViewFetchGet(pool[0].webView, url)
                override suspend fun postJson(url: String, jsonBody: String): String? =
                    webViewFetchPostJson(pool[0].webView, url, jsonBody)
            }
            val grmAvailable = NonGrmScanner.checkGrmAvailable(trainNumber, travelDate, webViewTransport)
            Log.i(TAG, "GRM available check for $trainNumber ($travelDate): $grmAvailable")
            if (!grmAvailable) {
                onProgress(
                    13,
                    "Wykryto pociąg bez graficznego schematu miejsc — uruchamiam tryb odcinkowy (bez blokowania miejsc)..."
                )
                return@coroutineScope NonGrmScanner.scanNonGrmTrainFromStops(
                    stops = stops,
                    travelDate = travelDate,
                    trainNumber = trainNumber,
                    depTime = departureTime,
                    ticketClass = ticketClass,
                    transport = webViewTransport,
                    onProgress = onProgress
                )
            }

            // 1. Direct Fast-Path A -> B
            var fpWagonsUnavailable = false
            if (fastPath && numSegments > 1) {
                val sFirst = stops.first()
                val sLast = stops.last()
                val fullDep = sFirst.departureTime.ifBlank { sFirst.arrivalTime }.ifBlank { departureTime ?: "12:00" }
                val fullDate = RouteHelpers.computeSegmentDate(travelDate, sFirst.dayOffset)

                onProgress(14, "Szybkie sprawdzanie (Fast-Path): ${sFirst.name} -> ${sLast.name}...")
                try {
                    val fullScan = withTimeout(75_000L) {
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
                    if (hasAnyAllowedSeat(fullScan, seatFilters)) {
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
                                    carriages = fullScan.carriages,
                                    seatTags = fullScan.seatTags,
                                    status = "OK"
                                )
                            )
                        )
                    }
                } catch (_: TimeoutCancellationException) {
                    Log.w(TAG, "Fast-Path timeout (75s) for ${sFirst.name} -> ${sLast.name}, falling back to segments")
                    onProgress(22, "Fast-Path przekroczył limit czasu - przechodzę do skanowania odcinków...")
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
                        val subDur = RouteHelpers.computeDurationMin(sa, sb, 30 * (bIdx - aIdx).coerceAtLeast(1))

                        val tmpl = capturedGrmTemplate.get()
                        if (tmpl != null) {
                            when (
                                val directRes = scanRelationViaDirectGrm(
                                    worker = pool[0],
                                    template = tmpl,
                                    fromStop = sa,
                                    toStop = sb,
                                    fromEpa = stopEpaByIdx.getOrNull(aIdx),
                                    toEpa = stopEpaByIdx.getOrNull(bIdx),
                                    travelDate = travelDate,
                                    fallbackDepTime = subDep,
                                    ticketClass = ticketClass
                                )
                            ) {
                                is DirectGrmResult.Ok -> {
                                    Log.i(TAG, "[DIRECT GRM] Split Fast-Path OK: ${sa.name} -> ${sb.name}")
                                    return SegmentAvailabilityMap(
                                        segmentIndex = 0,
                                        fromStation = sa.name,
                                        toStation = sb.name,
                                        departureTime = subDep,
                                        durationMin = subDur,
                                        fromEva = sa.evaCode,
                                        toEva = sb.evaCode,
                                        segmentDate = subDate,
                                        carriages = directRes.scan.carriages,
                                        seatTags = directRes.scan.seatTags,
                                        status = "OK"
                                    ) to false
                                }
                                is DirectGrmResult.SoldOut -> {
                                    Log.i(TAG, "[DIRECT GRM] Split Fast-Path SOLD_OUT: ${sa.name} -> ${sb.name} (${directRes.reason})")
                                    return null to GrmParser.isSeatmapWagonsUnavailableError(directRes.reason)
                                }
                                is DirectGrmResult.Fallback -> {
                                    Log.i(TAG, "[DIRECT GRM] Split Fast-Path fallback (${directRes.reason}): ${sa.name} -> ${sb.name}")
                                }
                            }
                        }

                        return try {
                            val subScan = withTimeout(60_000L) {
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
                            if (subScan.carriages.values.any { it.isNotEmpty() }) {
                                SegmentAvailabilityMap(
                                    segmentIndex = 0,
                                    fromStation = sa.name,
                                    toStation = sb.name,
                                    departureTime = subDep,
                                    durationMin = subDur,
                                    fromEva = sa.evaCode,
                                    toEva = sb.evaCode,
                                    segmentDate = subDate,
                                    carriages = subScan.carriages,
                                    seatTags = subScan.seatTags,
                                    status = "OK"
                                ) to false
                            } else null to false
                        } catch (_: TimeoutCancellationException) {
                            Log.w(TAG, "Split Fast-Path timeout (60s) for ${sa.name} -> ${sb.name}")
                            null to false
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
                        val commonAllowedSeats = macroSegments
                            .map { seg ->
                                seg.carriages.flatMap { (w, seats) ->
                                    seats.filter { s ->
                                        GrmParser.isSeatAllowed(seg.seatTags["${w}_$s"].orEmpty(), seatFilters)
                                    }.map { s -> w to s }
                                }.toSet()
                            }
                            .reduce { acc, set -> acc.intersect(set) }

                        if (commonAllowedSeats.isNotEmpty()) {
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

            // 3. Full segment-by-segment scan: Direct GRM API fast-scan + automatic WebView UI fallback
            val progressBase = if (fastPath && numSegments > 1) 24 else 15
            val directMaxPct = 72
            val completed = AtomicInteger(0)
            val resultsByIdx = arrayOfNulls<SegmentAvailabilityMap>(numSegments)

            suspend fun scanSegmentViaWebView(
                worker: WorkerWebView,
                idx: Int,
                progressRangeStart: Int = progressBase,
                progressRangeEnd: Int = 95,
                rangeDoneCounter: AtomicInteger? = null,
                rangeTotalCount: Int = numSegments
            ): SegmentAvailabilityMap {
                val sFrom = stops[idx]
                val sTo = stops[idx + 1]
                val segDep = sFrom.departureTime.ifBlank { sFrom.arrivalTime }.ifBlank { departureTime ?: "12:00" }
                val segDate = RouteHelpers.computeSegmentDate(travelDate, sFrom.dayOffset)
                val segDur = RouteHelpers.computeDurationMin(sFrom, sTo, 30)

                var carriages: Map<String, List<String>> = emptyMap()
                var seatTags: Map<String, List<String>> = emptyMap()
                var status = "OK"
                var errReason: String? = null
                val stepTicks = AtomicInteger(0)
                val safeTotal = rangeTotalCount.coerceAtLeast(1)

                try {
                    val segScan = withTimeout(75_000L) {
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
                            stepProgress = { msg ->
                                val tick = stepTicks.incrementAndGet().coerceAtMost(6)
                                val doneBase = rangeDoneCounter?.get() ?: completed.get()
                                val span = (progressRangeEnd - progressRangeStart).coerceAtLeast(1)
                                val frac = (doneBase.toDouble() + (tick / 7.0)) / safeTotal.toDouble()
                                val pct = (progressRangeStart + (frac * span).toInt()).coerceIn(progressRangeStart, (progressRangeEnd - 1).coerceAtLeast(progressRangeStart))
                                onProgress(pct, "[${idx + 1}/$numSegments] $msg")
                            }
                        )
                    }
                    carriages = segScan.carriages
                    seatTags = segScan.seatTags
                    if (carriages.values.none { it.isNotEmpty() }) {
                        status = "SOLD_OUT"
                        errReason = "Brak wolnych miejsc w Klasie $ticketClass."
                        carriages = emptyMap()
                        seatTags = emptyMap()
                    }
                } catch (_: TimeoutCancellationException) {
                    Log.w(TAG, "WebView segment timeout (75s) for ${sFrom.name} -> ${sTo.name}")
                    status = "SCRAPE_ERROR"
                    errReason = "SCRAPE_ERROR: Przekroczono limit czasu skanowania odcinka (75s)."
                    carriages = emptyMap()
                    seatTags = emptyMap()
                } catch (e: CancellationException) {
                    throw e
                } catch (e: GraphicReservationException) {
                    status = if (GrmParser.isSoldOutReason(e.message)) "SOLD_OUT" else "SCRAPE_ERROR"
                    errReason = e.message
                    carriages = emptyMap()
                    seatTags = emptyMap()
                } catch (e: Exception) {
                    status = "SCRAPE_ERROR"
                    errReason = e.message
                    carriages = emptyMap()
                    seatTags = emptyMap()
                }

                val doneNow = completed.incrementAndGet()
                val rangeDoneNow = rangeDoneCounter?.incrementAndGet() ?: doneNow
                val span = (progressRangeEnd - progressRangeStart).coerceAtLeast(1)
                val pct = (progressRangeStart + (rangeDoneNow * span / safeTotal)).coerceIn(progressRangeStart, progressRangeEnd)
                onProgress(pct, "Ukończono odcinek $doneNow/$numSegments: ${sFrom.name} -> ${sTo.name}")

                return SegmentAvailabilityMap(
                    segmentIndex = idx,
                    fromStation = sFrom.name,
                    toStation = sTo.name,
                    departureTime = segDep,
                    durationMin = segDur,
                    fromEva = sFrom.evaCode,
                    toEva = sTo.evaCode,
                    segmentDate = segDate,
                    carriages = carriages,
                    seatTags = seatTags,
                    status = status,
                    errorReason = errReason
                )
            }

            // If we don't have a GRM template yet (e.g. fastPath=false), scan segment 0 via WebView first
            // so its intercepted /grm/sklad/wbnet/ template + headers unlock Direct GRM for all remaining segments.
            if (capturedGrmTemplate.get() == null && numSegments > 1) {
                ensureActive()
                resultsByIdx[0] = scanSegmentViaWebView(pool[0], 0, progressBase, progressBase + 10, null, 1)
            }

            // Phase 3A: Direct GRM API fast-scan on pool[0] (stays parked on ebilet.intercity.pl)
            val directTemplate = capturedGrmTemplate.get()
            if (directTemplate != null) {
                val pendingDirect = (0 until numSegments).filter { resultsByIdx[it] == null }
                if (pendingDirect.isNotEmpty()) {
                    Log.i(TAG, "[DIRECT GRM] Starting fast API scan for ${pendingDirect.size} segments using template=$directTemplate (concurrency=$directGrmConcurrency)")
                    val directSem = Semaphore(directGrmConcurrency)
                    val consecutiveAuthFailures = AtomicInteger(0)
                    val abortDirect = AtomicBoolean(false)

                    pendingDirect.map { idx ->
                        async {
                            directSem.withPermit {
                                ensureActive()
                                if (abortDirect.get()) return@withPermit
                                val sFrom = stops[idx]
                                val sTo = stops[idx + 1]
                                val segDep = sFrom.departureTime.ifBlank { sFrom.arrivalTime }.ifBlank { departureTime ?: "12:00" }
                                val segDate = RouteHelpers.computeSegmentDate(travelDate, sFrom.dayOffset)
                                val segDur = RouteHelpers.computeDurationMin(sFrom, sTo, 30)

                                when (
                                    val directRes = scanRelationViaDirectGrm(
                                        worker = pool[0],
                                        template = directTemplate,
                                        fromStop = sFrom,
                                        toStop = sTo,
                                        fromEpa = stopEpaByIdx.getOrNull(idx),
                                        toEpa = stopEpaByIdx.getOrNull(idx + 1),
                                        travelDate = travelDate,
                                        fallbackDepTime = segDep,
                                        ticketClass = ticketClass
                                    )
                                ) {
                                    is DirectGrmResult.Ok -> {
                                        consecutiveAuthFailures.set(0)
                                        val doneNow = completed.incrementAndGet()
                                        val pct = (progressBase + (doneNow * (directMaxPct - progressBase) / numSegments)).coerceIn(progressBase, directMaxPct)
                                        onProgress(pct, "Szybki odczyt GRM $doneNow/$numSegments: ${sFrom.name} -> ${sTo.name}")
                                        Log.i(TAG, "[DIRECT GRM] Segment $idx OK (${sFrom.name} -> ${sTo.name}): ${directRes.scan.carriages.mapValues { it.value.size }}")
                                        resultsByIdx[idx] = SegmentAvailabilityMap(
                                            segmentIndex = idx,
                                            fromStation = sFrom.name,
                                            toStation = sTo.name,
                                            departureTime = segDep,
                                            durationMin = segDur,
                                            fromEva = sFrom.evaCode,
                                            toEva = sTo.evaCode,
                                            segmentDate = segDate,
                                            carriages = directRes.scan.carriages,
                                            seatTags = directRes.scan.seatTags,
                                            status = "OK"
                                        )
                                    }
                                    is DirectGrmResult.SoldOut -> {
                                        consecutiveAuthFailures.set(0)
                                        val doneNow = completed.incrementAndGet()
                                        val pct = (progressBase + (doneNow * (directMaxPct - progressBase) / numSegments)).coerceIn(progressBase, directMaxPct)
                                        onProgress(pct, "Szybki odczyt GRM $doneNow/$numSegments: ${sFrom.name} -> ${sTo.name} (brak miejsc)")
                                        Log.i(TAG, "[DIRECT GRM] Segment $idx SOLD_OUT (${sFrom.name} -> ${sTo.name}): ${directRes.reason}")
                                        resultsByIdx[idx] = SegmentAvailabilityMap(
                                            segmentIndex = idx,
                                            fromStation = sFrom.name,
                                            toStation = sTo.name,
                                            departureTime = segDep,
                                            durationMin = segDur,
                                            fromEva = sFrom.evaCode,
                                            toEva = sTo.evaCode,
                                            segmentDate = segDate,
                                            carriages = emptyMap(),
                                            status = "SOLD_OUT",
                                            errorReason = directRes.reason
                                        )
                                    }
                                    is DirectGrmResult.Fallback -> {
                                        Log.i(TAG, "[DIRECT GRM] Segment $idx fallback to WebView (${sFrom.name} -> ${sTo.name}): ${directRes.reason}")
                                        if (directRes.isAuthOrBlocked && consecutiveAuthFailures.incrementAndGet() >= 2) {
                                            abortDirect.set(true)
                                            Log.w(TAG, "[DIRECT GRM] Aborting remaining Direct GRM attempts after 2 auth/block failures; falling back to WebView pool")
                                        }
                                    }
                                }
                            }
                        }
                    }.awaitAll()
                }
            }

            // Phase 3B: WebView UI pool fallback for any remaining segments
            val remainingIndices = (0 until numSegments).filter { resultsByIdx[it] == null }
            if (remainingIndices.isEmpty()) {
                onProgress(95, "Odczytano wszystkie $numSegments odcinków przez GRM API...")
            } else {
                val phase3bStart = (progressBase + (completed.get() * (directMaxPct - progressBase) / numSegments)).coerceIn(progressBase, directMaxPct)
                val fallbackDone = AtomicInteger(0)
                val targetWorkers = minOf(webViewConcurrency, remainingIndices.size).coerceAtLeast(1)
                while (pool.size < targetWorkers) {
                    ensureActive()
                    pool.add(createWorkerWebView())
                }
                val sem = Semaphore(pool.size)
                val availablePool = ArrayDeque<WorkerWebView>(pool)
                val poolLock = Any()

                remainingIndices.map { idx ->
                    async {
                        sem.withPermit {
                            ensureActive()
                            val worker = synchronized(poolLock) { availablePool.removeFirst() }
                            try {
                                resultsByIdx[idx] = scanSegmentViaWebView(
                                    worker = worker,
                                    idx = idx,
                                    progressRangeStart = phase3bStart,
                                    progressRangeEnd = 95,
                                    rangeDoneCounter = fallbackDone,
                                    rangeTotalCount = remainingIndices.size
                                )
                            } finally {
                                synchronized(poolLock) { availablePool.addLast(worker) }
                            }
                        }
                    }
                }.awaitAll()
            }

            // Phase 3C: Sequential retry on pool[0] for transient non-metro-hub WebView UI errors (e.g. price-loader stall)
            val retryCandidates = resultsByIdx.mapIndexedNotNull { idx, seg ->
                if (seg == null || seg.status != "SCRAPE_ERROR") {
                    null
                } else {
                    val isMetroHop = RouteHelpers.isSameMetroHub(seg.fromStation, seg.toStation) && seg.durationMin <= 15
                    val errLow = (seg.errorReason ?: "").lowercase()
                    val isTransient = listOf(
                        "nie udało się wybrać klasy",
                        "not_found",
                        "timeout",
                        "kafelek pociągu zniknął"
                    ).any { it in errLow }
                    if (!isMetroHop && isTransient) idx else null
                }
            }.take(3)

            for (idx in retryCandidates) {
                ensureActive()
                val failedSeg = resultsByIdx[idx] ?: continue
                onProgress(95, "Ponawiam odczyt odcinka ${failedSeg.fromStation} -> ${failedSeg.toStation}...")
                delay(800L)
                val retried = scanSegmentViaWebView(
                    worker = pool[0],
                    idx = idx,
                    progressRangeStart = 95,
                    progressRangeEnd = 96,
                    rangeDoneCounter = null,
                    rangeTotalCount = 1
                )
                if (retried.status != "SCRAPE_ERROR") {
                    resultsByIdx[idx] = retried
                }
            }

            val scannedList = resultsByIdx.map { it!! }.toMutableList()

            // 4. Tail & Head metropolitan boundary recovery
            fun isUnsellableMetroBoundary(seg: SegmentAvailabilityMap): Boolean {
                if (seg.status != "SCRAPE_ERROR") return false
                val errLow = (seg.errorReason ?: "").lowercase()
                val matchesKnown = listOf(
                    "nie znaleziono pociągu",
                    "nie znalazł żadnych połączeń",
                    "timeout"
                ).any { it in errLow }
                if (!matchesKnown) return false
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
                    val mergedScan = withTimeout(60_000L) {
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
                    val cars = mergedScan.carriages
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
                        seatTags = mergedScan.seatTags,
                        status = if (cars.values.any { it.isNotEmpty() }) "OK" else "SOLD_OUT",
                        errorReason = if (cars.values.any { it.isNotEmpty() }) null else "Brak wolnych miejsc w Klasie $ticketClass."
                    )
                } catch (e: GraphicReservationException) {
                    if (GrmParser.isSoldOutReason(e.message)) {
                        SegmentAvailabilityMap(
                            segmentIndex = first.segmentIndex,
                            fromStation = first.fromStation,
                            toStation = second.toStation,
                            departureTime = first.departureTime,
                            durationMin = mergedDur,
                            fromEva = first.fromEva,
                            toEva = second.toEva,
                            segmentDate = first.segmentDate,
                            carriages = emptyMap(),
                            seatTags = emptyMap(),
                            status = "SOLD_OUT",
                            errorReason = e.message ?: "Brak wolnych miejsc w Klasie $ticketClass."
                        )
                    } else {
                        null
                    }
                } catch (_: TimeoutCancellationException) {
                    Log.w(TAG, "Merged boundary timeout (60s) for ${first.fromStation} -> ${second.toStation}")
                    null
                } catch (e: CancellationException) {
                    throw e
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
            directGrmPending.values.forEach { it.cancel() }
            directGrmPending.clear()
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
