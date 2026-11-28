package pl.siadaicie.app.scraper

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import org.jsoup.Jsoup
import pl.siadaicie.app.data.StationsRepository
import pl.siadaicie.app.domain.RouteHelpers
import pl.siadaicie.app.domain.StationStop
import pl.siadaicie.app.domain.TrainConnection
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit

class ScheduleClient(private val stationsRepo: StationsRepository) {

    private val client = OkHttpClient.Builder()
        .connectTimeout(12, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .followRedirects(true)
        .build()

    private val routeCache = ConcurrentHashMap<String, List<StationStop>>()

    companion object {
        val FULL_DAY_WINDOWS = listOf(
            "00:00", "02:30", "05:00", "07:00", "09:00", "11:00",
            "13:00", "15:00", "17:00", "19:00", "21:00", "22:30"
        )
        private const val UA =
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

        private val TP_ROW_SPLIT = Regex("<tr[^>]+class=\"[^\"]*tpOverview[^\"]*\"[^>]*>(.*?)(?=<tr[^>]+class=\"[^\"]*tpOverview|$)", RegexOption.DOT_MATCHES_ALL)
        private val ALT_ATTR_REGEX = Regex("alt=\"([^\"]*)\"")
        private val DEP_TIME_REGEX = Regex("odj.*?<span>(\\d{1,2}:\\d{2})</span>", setOf(RegexOption.DOT_MATCHES_ALL, RegexOption.IGNORE_CASE))
        private val ARR_TIME_REGEX = Regex("przyj.*?<span>(\\d{1,2}:\\d{2})</span>", setOf(RegexOption.DOT_MATCHES_ALL, RegexOption.IGNORE_CASE))
        private val DUR_REGEX = Regex("czas przejazdu:(?:&nbsp;|\\s*)(\\d+:\\d{2})")
        private val CHANGES_REGEX = Regex("przesiadki:\\s*(\\d+)")
        private val NAME_REGEX = Regex("Nazwa:(?:&nbsp;|\\s*)([A-ZĄĆĘŁŃÓŚŹŻ\\s\\-]+)")
        private val TRAIN_CODE_PREFIX_REGEX = Regex("^[A-Z]+\\s*\\d+")
        private val CLEAN_NUM_REGEX = Regex("\\b(\\d{3,5})\\b")
        private val BRAND_PREFIX_REGEX = Regex("^\\s*(EIP|EIC|IC|TLK)\\b", RegexOption.IGNORE_CASE)
        private val TI_HREF_REGEX = Regex("href=\"(/pl/ti\\?[^\"]*)\"")
        private val EVA_HREF_REGEX = Regex("(?:input|station_evaId)=(\\d{7})")
        private val TIME_REGEX = Regex("\\b(\\d{1,2}:\\d{2})\\b")

        val KNOWN_REAL_CORRIDORS: Map<String, List<Triple<String, String, Pair<Int, Int>>>> = mapOf(
            "WARSZAWA_GDANSK" to listOf(
                Triple("Warszawa Centralna", "5100065", 0 to 0),
                Triple("Warszawa Wschodnia", "5100066", 7 to 9),
                Triple("Modlin", "5102206", 35 to 36),
                Triple("Nasielsk", "5100178", 48 to 49),
                Triple("Ciechanów", "5100095", 72 to 73),
                Triple("Mława", "5100219", 95 to 96),
                Triple("Działdowo", "5100100", 110 to 111),
                Triple("Iława Główna", "5100075", 145 to 147),
                Triple("Malbork", "5100040", 180 to 182),
                Triple("Tczew", "5100061", 200 to 202),
                Triple("Gdańsk Główny", "5100009", 225 to 225)
            ),
            "WARSZAWA_KRAKOW" to listOf(
                Triple("Warszawa Wschodnia", "5100066", 0 to 0),
                Triple("Warszawa Centralna", "5100065", 6 to 11),
                Triple("Warszawa Zachodnia", "5100067", 15 to 16),
                Triple("Opoczno Południe", "5104744", 65 to 66),
                Triple("Włoszczowa Północ", "5104660", 95 to 96),
                Triple("Miechów", "5100218", 135 to 136),
                Triple("Kraków Główny", "5100028", 155 to 155)
            ),
            "WARSZAWA_POZNAN" to listOf(
                Triple("Warszawa Wschodnia", "5100066", 0 to 0),
                Triple("Warszawa Centralna", "5100065", 6 to 11),
                Triple("Warszawa Zachodnia", "5100067", 14 to 16),
                Triple("Kutno", "5100032", 65 to 67),
                Triple("Koło", "5100258", 92 to 93),
                Triple("Konin", "5100026", 115 to 117),
                Triple("Września", "5100157", 145 to 146),
                Triple("Poznań Główny", "5100081", 175 to 175)
            ),
            "POZNAN_WROCLAW" to listOf(
                Triple("Poznań Główny", "5100081", 0 to 0),
                Triple("Kościan", "5100124", 25 to 26),
                Triple("Leszno", "5100036", 45 to 47),
                Triple("Rawicz", "5100202", 68 to 69),
                Triple("Żmigród", "5104359", 85 to 86),
                Triple("Oborniki Śląskie", "5100182", 100 to 101),
                Triple("Wrocław Główny", "5100069", 120 to 120)
            )
        )

        fun formatHafasDate(travelDate: String): String {
            val trimmed = travelDate.trim()
            if ("-" in trimmed) {
                val p = trimmed.split("-")
                if (p.size == 3 && p[0].length == 4) {
                    return "${p[2]}.${p[1]}.${p[0]}"
                }
            }
            return trimmed
        }

        fun extractTiUrlFromSqHtml(htmlText: String, trainNo: String): String? {
            val numMatch = CLEAN_NUM_REGEX.find(trainNo) ?: return null
            val cleanNum = numMatch.groupValues[1]
            val brandMatch = BRAND_PREFIX_REGEX.find(trainNo)
            val exactBrandRe = brandMatch?.let {
                Regex("\\b${Regex.escape(it.groupValues[1])}\\s*${Regex.escape(cleanNum)}\\b", RegexOption.IGNORE_CASE)
            }
            val anyIcRe = Regex("\\b(EIP|EIC|IC|TLK)\\s*${Regex.escape(cleanNum)}\\b", RegexOption.IGNORE_CASE)

            val doc = Jsoup.parse(htmlText)
            var fallback: String? = null
            for (tr in doc.select("tr")) {
                val rowHtml = tr.outerHtml()
                val tiM = TI_HREF_REGEX.find(rowHtml) ?: continue
                val url = "https://rozklad-pkp.pl" + tiM.groupValues[1].replace("&amp;", "&")
                val rowText = tr.text()
                if (exactBrandRe != null && exactBrandRe.containsMatchIn(rowText)) {
                    return url
                }
                if (fallback == null && anyIcRe.containsMatchIn(rowText)) {
                    fallback = url
                }
            }
            return fallback
        }

        fun parseStopsFromTiHtml(
            htmlText: String,
            fromStation: String,
            toStation: String,
            fromEva: String = "",
            toEva: String = ""
        ): List<StationStop> {
            data class RawStop(
                val name: String,
                val eva: String,
                val arrStr: String,
                val depStr: String,
                val mArr: Int,
                val mDep: Int
            )

            val doc = Jsoup.parse(htmlText)
            val rawStops = ArrayList<RawStop>()
            for (tr in doc.select("tr")) {
                val tds = tr.select("td")
                if (tds.size < 4) continue
                val stnEl = tds[1]
                val stnName = (stnEl.selectFirst("a")?.text() ?: stnEl.text()).trim()
                val arrM = TIME_REGEX.find(tds[2].text())?.groupValues?.get(1)
                val depM = TIME_REGEX.find(tds[3].text())?.groupValues?.get(1)
                val eva = EVA_HREF_REGEX.find(stnEl.html())?.groupValues?.get(1).orEmpty()

                if (stnName.isNotEmpty() && (arrM != null || depM != null)) {
                    val finalDep = depM ?: arrM!!
                    val finalArr = arrM ?: depM!!
                    rawStops.add(
                        RawStop(
                            name = stnName,
                            eva = eva,
                            arrStr = finalArr,
                            depStr = finalDep,
                            mArr = RouteHelpers.timeToMinutes(finalArr),
                            mDep = RouteHelpers.timeToMinutes(finalDep)
                        )
                    )
                }
            }

            if (rawStops.isEmpty()) return emptyList()

            data class CumStop(
                val name: String,
                val evaCode: String,
                val arrivalTime: String,
                val departureTime: String,
                val arrMin: Int,
                val depMin: Int,
                val depDay: Int
            )

            val cumStops = ArrayList<CumStop>(rawStops.size)
            var dayOffsetDays = 0
            var prevTime: Int? = null
            for (s in rawStops) {
                val curArr = s.mArr
                val curDep = s.mDep
                if (prevTime != null && curArr < prevTime - 300) {
                    dayOffsetDays++
                }
                val arrDay = dayOffsetDays
                val arrMin = curArr + arrDay * 1440
                prevTime = curArr

                if (curDep < prevTime - 300) {
                    dayOffsetDays++
                }
                val depDay = dayOffsetDays
                val depMin = curDep + depDay * 1440
                prevTime = curDep

                cumStops.add(
                    CumStop(
                        name = s.name,
                        evaCode = s.eva,
                        arrivalTime = s.arrStr,
                        departureTime = s.depStr,
                        arrMin = arrMin,
                        depMin = depMin,
                        depDay = depDay
                    )
                )
            }

            var fromIdx: Int? = null
            var toIdx: Int? = null
            val fromLower = fromStation.lowercase()
            val toLower = toStation.lowercase()
            val fromNorm = RouteHelpers.normalizePolishName(fromStation)
            val toNorm = RouteHelpers.normalizePolishName(toStation)

            for (i in cumStops.indices) {
                val s = cumStops[i]
                val sName = s.name.lowercase()
                val sNorm = RouteHelpers.normalizePolishName(s.name)
                if (fromIdx == null) {
                    if ((fromEva.isNotEmpty() && fromEva == s.evaCode) ||
                        fromLower in sName ||
                        (fromNorm.isNotEmpty() && fromNorm in sNorm)
                    ) {
                        fromIdx = i
                        continue
                    }
                }
                if (fromIdx != null && toIdx == null) {
                    if ((toEva.isNotEmpty() && toEva == s.evaCode) ||
                        toLower in sName ||
                        (toNorm.isNotEmpty() && toNorm in sNorm)
                    ) {
                        toIdx = i
                        break
                    }
                }
            }

            val sliced = if (fromIdx != null && toIdx != null && toIdx > fromIdx) {
                cumStops.subList(fromIdx, toIdx + 1)
            } else {
                cumStops
            }

            val baseDay = sliced[0].depDay
            return sliced.mapIndexed { seq, s ->
                val hub = RouteHelpers.METROPOLITAN_HUBS[s.name]
                StationStop(
                    seq = seq,
                    evaCode = s.evaCode.ifEmpty { "5100%03d".format(seq) },
                    name = s.name,
                    arrivalMin = (s.arrMin - sliced[0].depMin).coerceAtLeast(0),
                    departureMin = (s.depMin - sliced[0].depMin).coerceAtLeast(0),
                    arrivalTime = s.arrivalTime,
                    departureTime = s.departureTime,
                    metropolitanGroup = hub?.first,
                    isMainHub = hub?.second ?: false,
                    dayOffset = (s.depDay - baseDay).coerceAtLeast(0)
                )
            }
        }
    }

    private fun executeGet(url: String): String? {
        val req = Request.Builder()
            .url(url)
            .header("User-Agent", UA)
            .header("Accept-Language", "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7")
            .build()
        return client.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) null else resp.body?.string()
        }
    }

    private data class WindowFetchResult(
        val icTrains: List<TrainConnection>,
        val allDepMinutes: List<Int>
    )

    private fun fetchSingleWindowTrains(
        fromName: String,
        fromEva: String,
        toName: String,
        toEva: String,
        dateFormatted: String,
        timeStr: String,
        directOnly: Boolean
    ): WindowFetchResult {
        return try {
            val urlBuilder = "https://rozklad-pkp.pl/pl/tp".toHttpUrl().newBuilder()
                .addQueryParameter("REQ0JourneyStopsS0A", "1")
                .addQueryParameter("REQ0JourneyStopsS0G", fromName)
                .addQueryParameter("REQ0JourneyStopsZ0A", "1")
                .addQueryParameter("REQ0JourneyStopsZ0G", toName)
                .addQueryParameter("date", dateFormatted)
                .addQueryParameter("time", timeStr)
                .addQueryParameter("start", "start")
            if (fromEva.isNotEmpty()) urlBuilder.addQueryParameter("REQ0JourneyStopsS0ID", "A=1@L=$fromEva@")
            if (toEva.isNotEmpty()) urlBuilder.addQueryParameter("REQ0JourneyStopsZ0ID", "A=1@L=$toEva@")
            if (directOnly) urlBuilder.addQueryParameter("REQ0HafasNoOfChanges", "0")

            val html = executeGet(urlBuilder.build().toString()) ?: return WindowFetchResult(emptyList(), emptyList())
            val trains = ArrayList<TrainConnection>()
            val allDepMinutes = ArrayList<Int>()
            for (m in TP_ROW_SPLIT.findAll(html)) {
                val chunk = m.groupValues[1]
                val depTime = DEP_TIME_REGEX.find(chunk)?.groupValues?.get(1).orEmpty()
                if (depTime.isEmpty()) continue
                allDepMinutes.add(RouteHelpers.timeToMinutes(depTime))

                var cleanName = ""
                for (a in ALT_ATTR_REGEX.findAll(chunk)) {
                    val c = RouteHelpers.cleanPkpTrainName(a.groupValues[1])
                    if (c.isNotEmpty()) {
                        cleanName = c
                        break
                    }
                }
                if (cleanName.isEmpty()) continue

                val arrTime = ARR_TIME_REGEX.find(chunk)?.groupValues?.get(1).orEmpty()
                val duration = DUR_REGEX.find(chunk)?.groupValues?.get(1).orEmpty()
                val changes = CHANGES_REGEX.find(chunk)?.groupValues?.get(1)?.toIntOrNull() ?: 0

                var trainDisplay = cleanName
                val subName = NAME_REGEX.find(chunk)?.groupValues?.get(1)?.trim().orEmpty()
                if (subName.length >= 3 && listOf("KIERUNEK", "ODLEG", "WAGON", "KLIMATYZACJA").none { it in subName }) {
                    trainDisplay = "$cleanName $subName"
                }

                trains.add(
                    TrainConnection(
                        trainNumber = trainDisplay,
                        departure = depTime,
                        arrival = arrTime,
                        duration = duration,
                        changes = changes
                    )
                )
            }
            WindowFetchResult(trains, allDepMinutes)
        } catch (_: Exception) {
            WindowFetchResult(emptyList(), emptyList())
        }
    }

    private suspend fun fetchAdaptiveFullDay(
        fromName: String,
        fromEva: String,
        toName: String,
        toEva: String,
        dateFormatted: String,
        directOnly: Boolean
    ): List<TrainConnection> = coroutineScope {
        val queriedMinutes = HashSet<Int>()
        val resultsByQueryMin = java.util.TreeMap<Int, WindowFetchResult>()

        suspend fun runBatch(minutesList: List<Int>) {
            val fresh = minutesList.filter { it in 0..1439 && queriedMinutes.add(it) }
            if (fresh.isEmpty()) return
            val fetched = fresh.map { qMin ->
                val tStr = "%02d:%02d".format(qMin / 60, qMin % 60)
                async { qMin to fetchSingleWindowTrains(fromName, fromEva, toName, toEva, dateFormatted, tStr, directOnly) }
            }.awaitAll()
            for ((qMin, res) in fetched) {
                resultsByQueryMin[qMin] = res
            }
        }

        runBatch(FULL_DAY_WINDOWS.map { RouteHelpers.timeToMinutes(it) })

        // HAFAS /pl/tp returns only ~3 connections per query (including regional carriers).
        // Adaptively probe any time gap where a window hit the 3-row cap before reaching the next window.
        for (round in 1..4) {
            val entries = resultsByQueryMin.entries.toList()
            val gapProbes = ArrayList<Int>()
            for (i in entries.indices) {
                val (qMin, res) = entries[i]
                if (res.allDepMinutes.size < 3) continue
                val maxDep = res.allDepMinutes.maxOrNull() ?: continue
                if (maxDep < qMin) continue
                val nextEntry = entries.getOrNull(i + 1)
                val nextBoundary = if (nextEntry != null) {
                    val nextMinDep = nextEntry.value.allDepMinutes.filter { it >= qMin }.minOrNull()
                    if (nextMinDep != null) minOf(nextEntry.key, nextMinDep) else nextEntry.key
                } else {
                    1435
                }
                val probeMin = maxDep + 1
                if (probeMin in (qMin + 1) until nextBoundary && probeMin !in queriedMinutes) {
                    gapProbes.add(probeMin)
                }
            }
            if (gapProbes.isEmpty()) break
            runBatch(gapProbes.take(12))
        }

        resultsByQueryMin.values.flatMap { it.icTrains }
    }

    suspend fun fetchLiveTrainConnections(
        fromStation: String,
        toStation: String,
        travelDate: String,
        directOnly: Boolean = true
    ): List<TrainConnection> = withContext(Dispatchers.IO) {
        val (fromName, fromEva) = stationsRepo.getStationInfo(fromStation)
        val (toName, toEva) = stationsRepo.getStationInfo(toStation)
        val dateFormatted = formatHafasDate(travelDate)

        coroutineScope {
            val directDeferred = async {
                fetchAdaptiveFullDay(fromName, fromEva, toName, toEva, dateFormatted, true)
            }
            val transferDeferred = if (!directOnly) {
                async {
                    fetchAdaptiveFullDay(fromName, fromEva, toName, toEva, dateFormatted, false)
                }
            } else null

            val directTrains = directDeferred.await()
            val transferTrains = transferDeferred?.await().orEmpty()

            val allTrains = ArrayList<TrainConnection>()
            val seen = HashSet<Pair<String, String>>()

            for (t in directTrains + transferTrains) {
                val codeStr = TRAIN_CODE_PREFIX_REGEX.find(t.trainNumber)?.value ?: t.trainNumber
                if (seen.add(codeStr to t.departure)) {
                    allTrains.add(t)
                }
            }
            allTrains.sortedBy { it.departure }
        }
    }

    private fun querySq(fromEva: String, fromName: String, trainNo: String, dateFormatted: String, timeStr: String): String? {
        return try {
            val url = "https://rozklad-pkp.pl/pl/sq".toHttpUrl().newBuilder()
                .addQueryParameter("input", fromEva.ifEmpty { fromName })
                .addQueryParameter("date", dateFormatted)
                .addQueryParameter("time", timeStr)
                .addQueryParameter("boardType", "dep")
                .addQueryParameter("start", "start")
                .build()
                .toString()
            val html = executeGet(url) ?: return null
            extractTiUrlFromSqHtml(html, trainNo)
        } catch (_: Exception) {
            null
        }
    }

    suspend fun resolveRealRoute(
        fromStation: String,
        toStation: String,
        travelDate: String,
        trainNumber: String,
        depTime: String? = null
    ): List<StationStop> = withContext(Dispatchers.IO) {
        val cacheKey = "${trainNumber}_${travelDate}_${fromStation}_$toStation"
        routeCache[cacheKey]?.let { return@withContext it }

        val (fromName, fromEva) = stationsRepo.getStationInfo(fromStation)
        val (toName, toEva) = stationsRepo.getStationInfo(toStation)
        val dateFormatted = formatHafasDate(travelDate)

        var tiUrl = if (!depTime.isNullOrBlank()) {
            querySq(fromEva, fromName, trainNumber, dateFormatted, depTime)
        } else null

        if (tiUrl == null) {
            val results = coroutineScope {
                FULL_DAY_WINDOWS.map { w ->
                    async { querySq(fromEva, fromName, trainNumber, dateFormatted, w) }
                }.awaitAll()
            }
            tiUrl = results.firstOrNull { !it.isNullOrEmpty() }
        }

        if (tiUrl != null) {
            try {
                val html = executeGet(tiUrl)
                if (!html.isNullOrBlank()) {
                    val liveStops = parseStopsFromTiHtml(html, fromStation, toStation, fromEva, toEva)
                    if (liveStops.size >= 2) {
                        routeCache[cacheKey] = liveStops
                        return@withContext liveStops
                    }
                }
            } catch (_: Exception) {
            }
        }

        val fromLow = fromStation.lowercase()
        val toLow = toStation.lowercase()
        val corridorKey = when {
            "warszaw" in fromLow && ("gda" in toLow || "gdyn" in toLow || "sopot" in toLow) -> "WARSZAWA_GDANSK"
            "warszaw" in fromLow && "krak" in toLow -> "WARSZAWA_KRAKOW"
            "warszaw" in fromLow && "pozna" in toLow -> "WARSZAWA_POZNAN"
            "pozna" in fromLow && "wroc" in toLow -> "POZNAN_WROCLAW"
            else -> null
        }

        val corridor = corridorKey?.let { KNOWN_REAL_CORRIDORS[it] }
        if (corridor != null) {
            val stops = corridor.mapIndexed { idx, (name, eva, times) ->
                val resolvedName = when (idx) {
                    0 -> fromName
                    corridor.lastIndex -> toName
                    else -> name
                }
                val hub = RouteHelpers.METROPOLITAN_HUBS[resolvedName]
                StationStop(
                    seq = idx,
                    evaCode = eva,
                    name = resolvedName,
                    arrivalMin = times.first,
                    departureMin = times.second,
                    metropolitanGroup = hub?.first,
                    isMainHub = hub?.second ?: false
                )
            }
            routeCache[cacheKey] = stops
            return@withContext stops
        }

        listOf(
            StationStop(0, fromEva, fromName, 0, 0, departureTime = depTime ?: "12:00"),
            StationStop(1, toEva, toName, 60, 60)
        )
    }
}
