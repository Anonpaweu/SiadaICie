package pl.iccbagno.app.scraper

import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import org.json.JSONArray
import org.json.JSONObject
import pl.iccbagno.app.domain.FullTrainAvailabilityMap
import pl.iccbagno.app.domain.RouteHelpers
import pl.iccbagno.app.domain.SegmentAvailabilityMap
import pl.iccbagno.app.domain.StationStop
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.round

/**
 * Non-GRM Scanner ("Tryb bez schematu wagonu") for PKP Intercity trains without graphical seatmaps.
 *
 * Uses e-IC public read-only endpoints routed through WebView Chromium `fetch()` (so Akamai TLS/cookies match):
 * 1. GET https://api-gateway.intercity.pl/grm/grm/available/{train_num}/{YYYYMMDD}
 * 2. POST https://api-gateway.intercity.pl/server/public/endpoint/Pociagi (wyszukajPolaczenia)
 * 3. POST https://api-gateway.intercity.pl/server/public/endpoint/Sprzedaz (sprawdzCenyLite)
 */
object NonGrmScanner {

    private const val POCIAGI_URL = "https://api-gateway.intercity.pl/server/public/endpoint/Pociagi"
    private const val SPRZEDAZ_URL = "https://api-gateway.intercity.pl/server/public/endpoint/Sprzedaz"

    private val CAT_REGEX = Regex("^(EIP|EIC|IC|TLK)\\b", RegexOption.IGNORE_CASE)
    private val DIGITS_REGEX = Regex("\\b(\\d{1,5})\\b")
    private val STANDING_PRIORITY = mapOf("OVERBOOKED" to 3, "SPECIAL_ONLY" to 2, "AVAILABLE" to 1)

    // Cache mapping 7-digit HAFAS EVA code -> e-IC internal numeric station ID (e.g. "5100069" -> 248)
    private val eicShortIdCache = ConcurrentHashMap<String, Int>()

    interface EicHttpTransport {
        suspend fun get(url: String): String?
        suspend fun postJson(url: String, jsonBody: String): String?
    }

    data class SprawdzCenyResult(
        val hasStandardSeats: Boolean,
        val standingStatus: String?,
        val pricePln: Double?,
        val message: String
    )

    data class PairQuery(
        val pairId: Int,
        val depDateTime: String,
        val fromShortId: Int,
        val toShortId: Int
    )

    data class NonGrmLeg(
        val startIdx: Int,
        val endIdx: Int,
        val isStanding: Boolean,
        val durationMin: Int,
        val pricePln: Double?,
        val standingStatus: String?
    )

    data class NonGrmPlan(
        val legs: List<NonGrmLeg>,
        val fastPathUsed: Boolean,
        val fullRouteStandingStatus: String?,
        val fullRoutePricePln: Double?
    )

    fun extractTrainDigitsAndCategory(trainNumber: String): Pair<String, String> {
        val raw = trainNumber.trim()
        val cat = CAT_REGEX.find(raw)?.groupValues?.get(1)?.uppercase() ?: "IC"
        val digits = DIGITS_REGEX.find(raw)?.groupValues?.get(1).orEmpty()
        return digits to cat
    }

    /**
     * Checks GET /grm/grm/available/{train_num}/{YYYYMMDD} via `transport`.
     * Returns false ONLY when JSON explicitly contains {"available": false}.
     * On errors/timeouts returns true so the standard WebView GRM scanner pipeline runs.
     */
    suspend fun checkGrmAvailable(
        trainNumber: String,
        travelDate: String,
        transport: EicHttpTransport
    ): Boolean {
        val (num, _) = extractTrainDigitsAndCategory(trainNumber)
        if (num.isEmpty() || travelDate.isBlank()) return true
        val cleanIso = GrmParser.normalizeIsoDate(travelDate)
        val yyyymmdd = cleanIso.replace("-", "")
        if (yyyymmdd.length != 8 || !yyyymmdd.all { it.isDigit() }) return true

        val url = "https://api-gateway.intercity.pl/grm/grm/available/$num/$yyyymmdd"
        try {
            val body = transport.get(url) ?: return true
            val obj = JSONObject(body)
            if (obj.has("available") && !obj.optBoolean("available", true)) {
                return false
            }
        } catch (_: Exception) {
        }
        return true
    }

    fun parseSprawdzCenyEntry(cenyList: JSONArray?, ticketClass: Int): SprawdzCenyResult {
        if (cenyList == null || cenyList.length() == 0) {
            return SprawdzCenyResult(false, "OVERBOOKED", null, "Brak oferty dla wybranej klasy.")
        }

        val candidates = ArrayList<JSONObject>()
        for (i in 0 until cenyList.length()) {
            val obj = cenyList.optJSONObject(i) ?: continue
            if (obj.optInt("klasa", -1) == ticketClass) {
                candidates.add(obj)
            }
        }
        if (candidates.isEmpty()) {
            return SprawdzCenyResult(false, "OVERBOOKED", null, "Brak oferty dla wybranej klasy.")
        }

        val entry = candidates.firstOrNull { it.optInt("rodzajMiejscaKod", -1) == 1 } ?: candidates[0]
        val cenaGrosze = entry.optInt("cena", 0)
        val pricePln = if (cenaGrosze > 0) round(cenaGrosze.toDouble()) / 100.0 else null

        val komunikaty = entry.optJSONArray("komunikaty")
        val komunikatTekst = entry.optString("komunikatTekst", "").trim()
        val codes = HashSet<Int>()
        if (komunikaty != null) {
            for (k in 0 until komunikaty.length()) {
                val ko = komunikaty.optJSONObject(k) ?: continue
                if (ko.has("komunikatKod")) {
                    codes.add(ko.optInt("komunikatKod", -1))
                }
            }
        }
        val hasKomunikaty = komunikaty != null && komunikaty.length() > 0

        if (cenaGrosze > 0 && !hasKomunikaty && komunikatTekst.isEmpty()) {
            return SprawdzCenyResult(
                hasStandardSeats = true,
                standingStatus = null,
                pricePln = pricePln,
                message = ""
            )
        }

        val low = komunikatTekst.lowercase()
        val status = when {
            48 in codes || "bez gwarancji" in low -> "AVAILABLE"
            47 in codes || "miejsca specjalne" in low -> "SPECIAL_ONLY"
            else -> "OVERBOOKED"
        }
        return SprawdzCenyResult(
            hasStandardSeats = false,
            standingStatus = status,
            pricePln = pricePln,
            message = komunikatTekst
        )
    }

    private suspend fun queryWyszukajPolaczenia(
        transport: EicHttpTransport,
        fromEva: String,
        toEva: String,
        travelDate: String,
        endDate: String,
        trainNumStr: String
    ): JSONObject? {
        val fromInt = fromEva.toIntOrNull() ?: return null
        val toInt = toEva.toIntOrNull() ?: return null

        val payload = JSONObject().apply {
            put("metoda", "wyszukajPolaczenia")
            put("wersja", "1.6.20_desktop")
            put("url", "https://ebilet.intercity.pl/wyszukiwanie")
            put("dataWyjazdu", "$travelDate 00:00:00")
            put("dataPrzyjazdu", "$endDate 23:59:59")
            put("stacjaWyjazdu", fromInt)
            put("stacjaPrzyjazdu", toInt)
            put("czasNaPrzesiadkeMin", 0)
            put("stacjePrzez", JSONArray())
            put("polaczeniaBezposrednie", 1)
            put("polaczeniaNajszybsze", 0)
            put("liczbaPolaczen", 0)
            put("kategoriePociagow", JSONArray())
            put("kodyPrzewoznikow", JSONArray())
            put("rodzajeMiejsc", JSONArray())
            put("typyMiejsc", JSONArray())
            put("czasNaPrzesiadkeMax", 1440)
            put("braille", 0)
            put("liczbaPrzesiadekMax", 0)
            put("atrybutyHandlowe", JSONArray())
            put("urzadzenieNr", 956)
        }

        return try {
            val raw = transport.postJson(POCIAGI_URL, payload.toString()) ?: return null
            val data = JSONObject(raw)
            val polaczenia = data.optJSONArray("polaczenia") ?: return null
            for (i in 0 until polaczenia.length()) {
                val conn = polaczenia.optJSONObject(i) ?: continue
                val pociagi = conn.optJSONArray("pociagi") ?: continue
                if (pociagi.length() != 1) continue
                val tr = pociagi.optJSONObject(0) ?: continue
                if (tr.optString("nrPociagu", "").trim() == trainNumStr) {
                    return tr
                }
            }
            null
        } catch (_: Exception) {
            null
        }
    }

    suspend fun resolveEicShortCodes(
        stops: List<StationStop>,
        trainNumber: String,
        travelDate: String,
        transport: EicHttpTransport
    ): Triple<Map<String, Int>, String, Map<String, String>> {
        val (trainNumStr, defaultCat) = extractTrainDigitsAndCategory(trainNumber)
        var category = defaultCat
        val shortCodes = ConcurrentHashMap<String, Int>()
        val exactDeps = ConcurrentHashMap<String, String>()

        for (st in stops) {
            val cached = eicShortIdCache[st.evaCode]
            if (cached != null) {
                shortCodes[st.evaCode] = cached
            }
        }

        val maxOffset = maxOf(1, (stops.lastOrNull()?.dayOffset ?: 0) + 1)
        val endDate = RouteHelpers.computeSegmentDate(travelDate, maxOffset)
        val firstEva = stops.first().evaCode
        val lastEva = stops.last().evaCode

        val rootTr = queryWyszukajPolaczenia(transport, firstEva, lastEva, travelDate, endDate, trainNumStr)
        if (rootTr != null) {
            val cat = rootTr.optString("kategoriaPociagu", "").trim()
            if (cat.isNotEmpty()) category = cat.uppercase()
            val depSid = rootTr.optInt("stacjaWyjazdu", 0)
            if (depSid > 0) {
                shortCodes[firstEva] = depSid
                eicShortIdCache[firstEva] = depSid
            }
            val arrSid = rootTr.optInt("stacjaPrzyjazdu", 0)
            if (arrSid > 0) {
                shortCodes[lastEva] = arrSid
                eicShortIdCache[lastEva] = arrSid
            }
            val depDt = rootTr.optString("dataWyjazdu", "").trim()
            if (depDt.isNotEmpty()) {
                exactDeps[firstEva] = depDt
            }
        }

        val missingMid = stops.drop(1).dropLast(1).filter { it.evaCode.isNotEmpty() && !shortCodes.containsKey(it.evaCode) }
        if (missingMid.isNotEmpty() && firstEva.isNotEmpty()) {
            val sem = Semaphore(6)
            coroutineScope {
                missingMid.map { st ->
                    async {
                        sem.withPermit {
                            currentCoroutineContext().ensureActive()
                            val tr = queryWyszukajPolaczenia(transport, firstEva, st.evaCode, travelDate, endDate, trainNumStr)
                            val sid = tr?.optInt("stacjaPrzyjazdu", 0) ?: 0
                            if (sid > 0) {
                                shortCodes[st.evaCode] = sid
                                eicShortIdCache[st.evaCode] = sid
                            }
                        }
                    }
                }.awaitAll()
            }
        }

        val stillMissing = stops.dropLast(1).filter { it.evaCode.isNotEmpty() && !shortCodes.containsKey(it.evaCode) }
        if (stillMissing.isNotEmpty() && lastEva.isNotEmpty()) {
            val sem = Semaphore(6)
            coroutineScope {
                stillMissing.map { st ->
                    async {
                        sem.withPermit {
                            currentCoroutineContext().ensureActive()
                            val segDate = RouteHelpers.computeSegmentDate(travelDate, st.dayOffset)
                            val tr = queryWyszukajPolaczenia(transport, st.evaCode, lastEva, segDate, endDate, trainNumStr)
                            val sid = tr?.optInt("stacjaWyjazdu", 0) ?: 0
                            if (sid > 0) {
                                shortCodes[st.evaCode] = sid
                                eicShortIdCache[st.evaCode] = sid
                                val depDt = tr?.optString("dataWyjazdu", "")?.trim().orEmpty()
                                if (depDt.isNotEmpty()) exactDeps[st.evaCode] = depDt
                            }
                        }
                    }
                }.awaitAll()
            }
        }

        return Triple(shortCodes.toMap(), category, exactDeps.toMap())
    }

    private fun formatDepDateTime(stop: StationStop, travelDate: String, exactDeps: Map<String, String>): String {
        val exact = exactDeps[stop.evaCode]
        if (!exact.isNullOrBlank()) return exact
        val segDate = RouteHelpers.computeSegmentDate(travelDate, stop.dayOffset)
        val hhmm = stop.departureTime.ifBlank { stop.arrivalTime }.ifBlank { "12:00" }.trim()
        return if (hhmm.length == 5) "$segDate $hhmm:00" else "$segDate 12:00:00"
    }

    private suspend fun checkSprawdzCenyPair(
        transport: EicHttpTransport,
        query: PairQuery,
        trainNumInt: Int,
        category: String,
        ticketClass: Int
    ): SprawdzCenyResult? {
        val polArr = JSONArray().apply {
            put(
                JSONObject().apply {
                    put("idPolaczenia", query.pairId)
                    put(
                        "odcinki",
                        JSONArray().apply {
                            put(
                                JSONObject().apply {
                                    put("wyjazdData", query.depDateTime)
                                    put("stacjaOdKod", query.fromShortId)
                                    put("stacjaDoKod", query.toShortId)
                                    put("pociagNr", trainNumInt)
                                    put("kategoriaPociagu", category)
                                }
                            )
                        }
                    )
                }
            )
        }
        val payload = JSONObject().apply {
            put("metoda", "sprawdzCenyLite")
            put("wersja", "1.6.20_desktop")
            put("url", "https://ebilet.intercity.pl/wyszukiwanie")
            put("jezyk", "PL")
            put("biletTyp", 1)
            put(
                "ofertyZaznaczone",
                JSONArray().apply {
                    put(JSONObject().apply { put("ofertaKod", 4); put("uwzglednic", 0) })
                    put(JSONObject().apply { put("ofertaKod", 14); put("uwzglednic", 0) })
                    put(JSONObject().apply { put("ofertaKod", 15); put("uwzglednic", 0) })
                }
            )
            put("polaczenia", polArr)
            put(
                "podrozni",
                JSONArray().apply {
                    put(JSONObject().apply { put("kodZakupowyZnizki", 1010) })
                }
            )
            put("urzadzenieNr", 956)
        }

        val bodyStr = payload.toString()
        for (attempt in 0..1) {
            currentCoroutineContext().ensureActive()
            try {
                val raw = transport.postJson(SPRZEDAZ_URL, bodyStr)
                if (raw != null) {
                    val data = JSONObject(raw)
                    val cenyPolaczen = data.optJSONArray("cenyPolaczen") ?: return null
                    for (i in 0 until cenyPolaczen.length()) {
                        val item = cenyPolaczen.optJSONObject(i) ?: continue
                        if (item.optInt("idPolaczenia", -1) == query.pairId) {
                            return parseSprawdzCenyEntry(item.optJSONArray("ceny"), ticketClass)
                        }
                    }
                    return null
                }
            } catch (_: Exception) {
                if (attempt == 1) return null
            }
            if (attempt == 0) delay(400L)
        }
        return null
    }

    private fun mergeStandingLegs(legs: List<NonGrmLeg>): List<NonGrmLeg> {
        val merged = ArrayList<NonGrmLeg>(legs.size)
        for (leg in legs) {
            val prev = merged.lastOrNull()
            if (leg.isStanding && prev != null && prev.isStanding && prev.endIdx == leg.startIdx) {
                val prevSt = prev.standingStatus ?: "AVAILABLE"
                val curSt = leg.standingStatus ?: "AVAILABLE"
                val chosen = if ((STANDING_PRIORITY[prevSt] ?: 0) >= (STANDING_PRIORITY[curSt] ?: 0)) prevSt else curSt
                merged[merged.lastIndex] = prev.copy(
                    endIdx = leg.endIdx,
                    durationMin = prev.durationMin + leg.durationMin,
                    standingStatus = chosen
                )
            } else {
                merged.add(leg)
            }
        }
        return merged
    }

    suspend fun planNonGrmLegs(
        stops: List<StationStop>,
        shortCodes: Map<String, Int>,
        exactDeps: Map<String, String>,
        travelDate: String,
        checkPairFn: suspend (PairQuery) -> SprawdzCenyResult?,
        onProgress: ((Int, String) -> Unit)? = null
    ): NonGrmPlan {
        val n = stops.size - 1
        val firstSid = shortCodes[stops.first().evaCode]
        val lastSid = shortCodes[stops.last().evaCode]

        var fullRouteStatus: String? = null
        var fullRoutePrice: Double? = null
        if (firstSid != null && lastSid != null) {
            val fullDep = formatDepDateTime(stops.first(), travelDate, exactDeps)
            val entry0 = checkPairFn(PairQuery(0, fullDep, firstSid, lastSid))
            if (entry0 != null) {
                fullRouteStatus = entry0.standingStatus
                fullRoutePrice = entry0.pricePln
                if (entry0.hasStandardSeats) {
                    val dur = RouteHelpers.computeDurationMin(stops.first(), stops.last(), 60)
                    return NonGrmPlan(
                        legs = listOf(
                            NonGrmLeg(
                                startIdx = 0,
                                endIdx = n,
                                isStanding = false,
                                durationMin = dur,
                                pricePln = entry0.pricePln,
                                standingStatus = null
                            )
                        ),
                        fastPathUsed = true,
                        fullRouteStandingStatus = null,
                        fullRoutePricePln = fullRoutePrice
                    )
                }
            }
        }

        val rawLegs = ArrayList<NonGrmLeg>()
        var curr = 0
        while (curr < n) {
            currentCoroutineContext().ensureActive()
            val fromStop = stops[curr]
            val fromSid = shortCodes[fromStop.evaCode]
            if (fromSid == null) {
                val dur = RouteHelpers.computeDurationMin(fromStop, stops[curr + 1], 20)
                rawLegs.add(NonGrmLeg(curr, curr + 1, true, dur, null, fullRouteStatus ?: "OVERBOOKED"))
                curr++
                continue
            }

            if (onProgress != null) {
                val pct = (35 + (curr * 55) / maxOf(1, n)).coerceAtMost(92)
                onProgress(pct, "Tryb bez schematu: sprawdzanie od stacji ${fromStop.name}...")
            }

            val depDt = formatDepDateTime(fromStop, travelDate, exactDeps)
            val validJs = ((curr + 1)..n).filter { shortCodes[stops[it].evaCode] != null }
            if (validJs.isEmpty()) {
                val dur = RouteHelpers.computeDurationMin(fromStop, stops[curr + 1], 20)
                rawLegs.add(NonGrmLeg(curr, curr + 1, true, dur, null, fullRouteStatus ?: "OVERBOOKED"))
                curr++
                continue
            }

            val cacheJ = HashMap<Int, SprawdzCenyResult?>()
            suspend fun probe(jIdx: Int): SprawdzCenyResult? {
                if (cacheJ.containsKey(jIdx)) return cacheJ[jIdx]
                val toSid = shortCodes[stops[jIdx].evaCode] ?: return null
                val res = checkPairFn(PairQuery(jIdx, depDt, fromSid, toSid))
                cacheJ[jIdx] = res
                return res
            }

            val firstHop = probe(validJs[0])
            var bestJ: Int? = null
            if (firstHop != null && firstHop.hasStandardSeats) {
                bestJ = validJs[0]
                val upperPos = validJs.size - 1
                if (curr > 0 && upperPos > 0) {
                    val farInfo = probe(validJs[upperPos])
                    if (farInfo != null && farInfo.hasStandardSeats) {
                        bestJ = validJs[upperPos]
                    }
                }
                if (bestJ != validJs.last()) {
                    var lo = 1
                    var hi = validJs.size - (if (curr == 0 && validJs.last() == n && fullRouteStatus != null) 2 else 1)
                    while (lo <= hi) {
                        val mid = (lo + hi) / 2
                        val candJ = validJs[mid]
                        val midInfo = probe(candJ)
                        if (midInfo != null && midInfo.hasStandardSeats) {
                            bestJ = candJ
                            lo = mid + 1
                        } else {
                            hi = mid - 1
                        }
                    }
                }
            } else if (validJs.size > 1 && (firstHop == null || firstHop.pricePln == null)) {
                val hop2 = probe(validJs[1])
                if (hop2 != null && hop2.hasStandardSeats) {
                    bestJ = validJs[1]
                    var lo = 2
                    var hi = validJs.size - 1
                    while (lo <= hi) {
                        val mid = (lo + hi) / 2
                        val candJ = validJs[mid]
                        val midInfo = probe(candJ)
                        if (midInfo != null && midInfo.hasStandardSeats) {
                            bestJ = candJ
                            lo = mid + 1
                        } else {
                            hi = mid - 1
                        }
                    }
                }
            }

            if (bestJ != null) {
                val info = cacheJ[bestJ]
                val dur = RouteHelpers.computeDurationMin(fromStop, stops[bestJ], 20)
                rawLegs.add(
                    NonGrmLeg(
                        startIdx = curr,
                        endIdx = bestJ,
                        isStanding = false,
                        durationMin = dur,
                        pricePln = info?.pricePln,
                        standingStatus = null
                    )
                )
                curr = bestJ
            } else {
                val gapStatus = firstHop?.standingStatus ?: fullRouteStatus ?: "AVAILABLE"
                val dur = RouteHelpers.computeDurationMin(fromStop, stops[curr + 1], 20)
                rawLegs.add(
                    NonGrmLeg(
                        startIdx = curr,
                        endIdx = curr + 1,
                        isStanding = true,
                        durationMin = dur,
                        pricePln = null,
                        standingStatus = gapStatus
                    )
                )
                curr++
            }
        }

        return NonGrmPlan(
            legs = mergeStandingLegs(rawLegs),
            fastPathUsed = false,
            fullRouteStandingStatus = fullRouteStatus,
            fullRoutePricePln = fullRoutePrice
        )
    }

    suspend fun scanNonGrmTrainFromStops(
        stops: List<StationStop>,
        travelDate: String,
        trainNumber: String,
        depTime: String?,
        ticketClass: Int,
        transport: EicHttpTransport,
        onProgress: (Int, String) -> Unit
    ): FullTrainAvailabilityMap {
        if (stops.size < 2) {
            throw IllegalStateException("Nie udało się ustalić listy przystanków dla $trainNumber.")
        }

        val (trainNumStr, _) = extractTrainDigitsAndCategory(trainNumber)
        val trainNumInt = trainNumStr.toIntOrNull() ?: 0

        onProgress(28, "Pobieranie identyfikatorów stacji e-IC (bez blokowania miejsc)...")
        val (shortCodes, category, exactDeps) = resolveEicShortCodes(stops, trainNumber, travelDate, transport)

        val plan = planNonGrmLegs(
            stops = stops,
            shortCodes = shortCodes,
            exactDeps = exactDeps,
            travelDate = travelDate,
            checkPairFn = { query ->
                checkSprawdzCenyPair(transport, query, trainNumInt, category, ticketClass)
            },
            onProgress = onProgress
        )

        onProgress(100, "Zakończono analizę połączenia bez schematu miejsc!")

        val numSegs = stops.size - 1
        val segToken = arrayOfNulls<String>(numSegs)
        val segStanding = arrayOfNulls<String>(numSegs)
        var seatedCounter = 1

        for (leg in plan.legs) {
            if (!leg.isStanding) {
                val token = "AUTO_$seatedCounter"
                seatedCounter++
                for (k in leg.startIdx until leg.endIdx) {
                    segToken[k] = token
                }
            } else {
                val stStatus = leg.standingStatus ?: "AVAILABLE"
                for (k in leg.startIdx until leg.endIdx) {
                    segStanding[k] = stStatus
                }
            }
        }

        val firstDep = stops.first().departureTime.ifBlank { stops.first().arrivalTime }.ifBlank { depTime ?: "08:00" }
        val segmentsOut = (0 until numSegs).map { i ->
            val sf = stops[i]
            val st = stops[i + 1]
            val dep = sf.departureTime.ifBlank { sf.arrivalTime }.ifBlank { firstDep }
            val dur = RouteHelpers.computeDurationMin(sf, st, 20)
            val tok = segToken[i]
            val isOk = tok != null
            SegmentAvailabilityMap(
                segmentIndex = i,
                fromStation = sf.name,
                toStation = st.name,
                departureTime = dep,
                durationMin = dur,
                fromEva = sf.evaCode,
                toEva = st.evaCode,
                segmentDate = RouteHelpers.computeSegmentDate(travelDate, sf.dayOffset),
                carriages = if (tok != null) mapOf("0" to listOf(tok)) else emptyMap(),
                seatTags = emptyMap(),
                status = if (isOk) "OK" else "SOLD_OUT",
                errorReason = if (isOk) null else "Brak miejsc ogólnodostępnych w Klasie $ticketClass.",
                standingStatus = if (isOk) null else segStanding[i]
            )
        }

        return FullTrainAvailabilityMap(
            trainNumber = trainNumber,
            travelDate = travelDate,
            fastPathUsed = plan.fastPathUsed,
            totalRouteSegments = numSegs,
            noSeatmapMode = true,
            fullRouteStandingStatus = plan.fullRouteStandingStatus,
            segments = segmentsOut
        )
    }
}
