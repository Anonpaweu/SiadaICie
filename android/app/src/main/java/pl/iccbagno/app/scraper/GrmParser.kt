package pl.iccbagno.app.scraper

import org.json.JSONObject
import org.jsoup.Jsoup
import org.jsoup.parser.Parser
import pl.iccbagno.app.domain.SeatFilterPrefs

object GrmParser {
    const val TARGET_CLASS = 2

    const val TAG_BIKE = "BIKE"
    const val TAG_PREGNANT = "PREGNANT"
    const val TAG_CHILD = "CHILD"
    const val TAG_ELDERLY = "ELDERLY"
    const val TAG_PRM_ASSIST = "PRM_ASSIST"
    const val TAG_WHEELCHAIR = "WHEELCHAIR"
    const val TAG_QUIET = "QUIET"
    const val TAG_WINDOW = "WINDOW"
    const val TAG_AISLE = "AISLE"
    const val TAG_TABLE = "TABLE"

    private val RESTRICTED_TAGS = setOf(
        TAG_BIKE,
        TAG_PREGNANT,
        TAG_CHILD,
        TAG_ELDERLY,
        TAG_PRM_ASSIST,
        TAG_WHEELCHAIR
    )

    private val PRM_WHEELCHAIR_MARKERS = listOf(
        "na wózku",
        "na wozku",
        "wózku inwalidzkim",
        "wozku inwalidzkim",
        "opiekuna osoby na wózku",
        "opiekuna osoby na wozku",
        "wózek inwalidzki",
        "wozek inwalidzki"
    )

    private val SOLD_OUT_MARKERS = listOf(
        "brak_miejsc",
        "brak miejsc",
        "brak wolnych miejsc",
        "brak dostępnych wagonów",
        "wyprzedan",
        "wyczerpano pulę miejsc"
    )

    private val SEAT_ARIA_REGEX = Regex("miejsce\\s+(\\d+)", RegexOption.IGNORE_CASE)
    private val TIME_HH_MM_REGEX = Regex("^(\\d{1,2}):(\\d{2})$")
    private val DATE_YYYY_MM_DD_REGEX = Regex("^(\\d{4})-(\\d{2})-(\\d{2})$")
    private val GRM_TEMPLATE_REGEX = Regex("^(https?://[^/]+)/grm/(?:sklad|wagon/svg)/wbnet/([^/]+)/([^/]+)/")

    data class GrmUrlTemplate(
        val host: String,
        val category: String,
        val trainNo: String
    )

    fun isPrmWheelchairLabel(ariaLabel: String?): Boolean {
        val low = (ariaLabel ?: "").lowercase()
        return PRM_WHEELCHAIR_MARKERS.any { it in low }
    }

    fun isRestrictedTag(tag: String): Boolean = tag in RESTRICTED_TAGS

    fun formatSeatTagPl(tag: String): String = when (tag) {
        TAG_BIKE -> "Rower"
        TAG_PREGNANT -> "Kobieta w ciąży"
        TAG_CHILD -> "Z dzieckiem"
        TAG_ELDERLY -> "Senior"
        TAG_PRM_ASSIST -> "Niepełnosprawność"
        TAG_WHEELCHAIR -> "Osoba na wózku"
        TAG_QUIET -> "Strefa ciszy"
        TAG_WINDOW -> "Okno"
        TAG_AISLE -> "Korytarz"
        TAG_TABLE -> "Stolik"
        else -> tag
    }

    /**
     * Tier hierarchy:
     * 0 = Normal / Quiet zone / comfort-only
     * 1 = Bike
     * 2 = Pregnant / Child / Elderly / PRM without wheelchair
     * 3 = Wheelchair
     */
    fun seatPriorityTier(tags: List<String>): Int = when {
        TAG_WHEELCHAIR in tags -> 3
        TAG_PREGNANT in tags || TAG_CHILD in tags || TAG_ELDERLY in tags || TAG_PRM_ASSIST in tags -> 2
        TAG_BIKE in tags -> 1
        else -> 0
    }

    fun seatTierPenalty(tags: List<String>): Double = when (seatPriorityTier(tags)) {
        1 -> 3.0
        2 -> 8.0
        3 -> 15.0
        else -> 0.0
    }

    fun isSeatAllowed(tags: List<String>, prefs: SeatFilterPrefs): Boolean {
        if (TAG_WHEELCHAIR in tags && !prefs.allowWheelchair) return false
        if (TAG_PREGNANT in tags && !prefs.allowPregnant) return false
        if (TAG_CHILD in tags && !prefs.allowChild) return false
        if (TAG_ELDERLY in tags && !prefs.allowElderly) return false
        if (TAG_PRM_ASSIST in tags && !prefs.allowPrmAssist) return false
        if (TAG_BIKE in tags && !prefs.allowBike) return false
        return true
    }

    fun isSoldOutReason(reason: String?): Boolean {
        val low = (reason ?: "").lowercase()
        return SOLD_OUT_MARKERS.any { it in low }
    }

    fun isSeatmapWagonsUnavailableError(reason: String?): Boolean {
        val low = (reason ?: "").lowercase()
        return "brak dostępnych wagonów" in low || "brak dostepnych wagonow" in low
    }

    fun parseGrmWagonSvg(svgXml: String, ticketClass: Int = TARGET_CLASS): List<String> {
        return parseGrmWagonSvgWithMeta(svgXml, ticketClass, includeWheelchair = false).first
    }

    fun parseGrmWagonSvgWithMeta(
        svgXml: String,
        ticketClass: Int = TARGET_CLASS,
        includeWheelchair: Boolean = true
    ): Pair<List<String>, Map<String, List<String>>> {
        if (svgXml.isBlank()) return emptyList<String>() to emptyMap()

        val isClass1 = ticketClass == 1
        val seats = HashSet<String>()
        val seatTags = LinkedHashMap<String, LinkedHashSet<String>>()

        fun shouldSkipClass(ariaL: String, dClassL: String): Boolean {
            return if (isClass1) {
                "second class" in dClassL ||
                    (("klasa 2" in ariaL || "kl. 2" in ariaL) &&
                        "klasa 1" !in ariaL && "kl. 1" !in ariaL)
            } else {
                "first class" in dClassL ||
                    (("klasa 1" in ariaL || "kl. 1" in ariaL) &&
                        "klasa 2" !in ariaL && "kl. 2" !in ariaL)
            }
        }

        var cleaned = svgXml.trim()
        if ("<svg" in cleaned && !cleaned.startsWith("<svg") && !cleaned.startsWith("<?xml")) {
            val start = cleaned.indexOf("<svg")
            val end = cleaned.lastIndexOf("</svg>")
            if (start != -1 && end != -1 && end > start) {
                cleaned = cleaned.substring(start, end + "</svg>".length)
            }
        }

        val doc = Jsoup.parse(cleaned, "", Parser.xmlParser())
        for (g in doc.select("g")) {
            val aria = g.attr("aria-label")
            val dClass = g.attr("data-class")
            val ariaL = aria.lowercase()
            val dClassL = dClass.lowercase()

            if ("wolne" !in ariaL) continue
            if ("niedostepne" in ariaL || "niedostępne" in ariaL) continue
            if (shouldSkipClass(ariaL, dClassL)) continue

            val textParts = ArrayList<String>(4)
            textParts.add(ariaL)
            var iconCode = ""
            var carriageCode = ""
            for (el in g.allElements) {
                val tName = el.tagName()
                if (tName.endsWith("special", ignoreCase = true)) {
                    val pl = el.attr("pl").trim()
                    val en = el.attr("en").trim()
                    val txt = el.text().trim()
                    if (pl.isNotEmpty()) textParts.add(pl.lowercase())
                    if (en.isNotEmpty()) textParts.add(en.lowercase())
                    if (txt.isNotEmpty()) textParts.add(txt.lowercase())
                    if (iconCode.isEmpty()) iconCode = el.attr("iconcode").trim().uppercase()
                    if (carriageCode.isEmpty()) carriageCode = el.attr("carriagecode").trim()
                } else if (tName.equals("title", ignoreCase = true)) {
                    val txt = el.text().trim()
                    if (txt.isNotEmpty()) textParts.add(txt.lowercase())
                }
            }
            val combined = textParts.joinToString(" | ")
            val isWheelchair = isPrmWheelchairLabel(combined) || iconCode in setOf("6", "S6") || carriageCode == "6"
            if (isWheelchair && !includeWheelchair) continue

            var num = g.selectFirst("text")?.text()?.trim().orEmpty()
            if (num.isEmpty() || num.toIntOrNull() == null) {
                num = SEAT_ARIA_REGEX.find(aria)?.groupValues?.getOrNull(1).orEmpty()
            }
            if (num.toIntOrNull() != null) {
                seats.add(num)
                val tagSet = seatTags.getOrPut(num) { LinkedHashSet() }
                if (isWheelchair) {
                    tagSet.add(TAG_WHEELCHAIR)
                } else if ("niepełnosprawn" in combined || "niepelnosprawn" in combined || "ograniczon" in combined) {
                    tagSet.add(TAG_PRM_ASSIST)
                }
                if ("rower" in combined || iconCode == "S1" || carriageCode == "24") {
                    tagSet.add(TAG_BIKE)
                }
                if ("w ciąży" in combined || "w ciazy" in combined || "ciężarn" in combined || "ciezarn" in combined) {
                    tagSet.add(TAG_PREGNANT)
                }
                if ("z dzieckiem" in combined || "matk" in combined || "rodzin" in combined) {
                    tagSet.add(TAG_CHILD)
                }
                if ("starsz" in combined || "senior" in combined) {
                    tagSet.add(TAG_ELDERLY)
                }
                if ("strefa ciszy" in combined || "ciszy" in combined) {
                    tagSet.add(TAG_QUIET)
                }
                if ("okno" in combined) {
                    tagSet.add(TAG_WINDOW)
                }
                if ("korytarz" in combined) {
                    tagSet.add(TAG_AISLE)
                }
                if ("stolik" in combined) {
                    tagSet.add(TAG_TABLE)
                }
            }
        }

        val sortedSeats = seats.sortedBy { it.toIntOrNull() ?: Int.MAX_VALUE }
        val finalTags = LinkedHashMap<String, List<String>>()
        for (s in sortedSeats) {
            val tags = seatTags[s]?.toList().orEmpty()
            if (tags.isNotEmpty()) {
                finalTags[s] = tags
            }
        }
        return sortedSeats to finalTags
    }

    fun selectGrmTargetWagons(
        skladJson: JSONObject,
        ticketClass: Int = TARGET_CLASS
    ): Pair<List<Pair<String, String>>, Boolean> {
        val classKey = if (ticketClass == 1) "klasa1" else "klasa2"
        val hasKey = skladJson.has(classKey)

        val unavailable = HashSet<String>()
        val unavailArr = skladJson.optJSONArray("wagonyNiedostepne")
        if (unavailArr != null) {
            for (i in 0 until unavailArr.length()) {
                val item = unavailArr.opt(i)?.toString()?.trim().orEmpty()
                if (item.isNotEmpty() && item != "null") unavailable.add(item)
            }
        }

        val schematMap = HashMap<String, String>()
        val schematObj = skladJson.optJSONObject("wagonySchemat")
        if (schematObj != null) {
            val keys = schematObj.keys()
            while (keys.hasNext()) {
                val k = keys.next().trim()
                val v = schematObj.opt(k)?.toString()?.trim().orEmpty()
                if (k.isNotEmpty() && v.isNotEmpty() && v != "null") {
                    schematMap[k] = v
                }
            }
        }

        val serviceWagons = HashSet<String>()
        val wagonyArr = skladJson.optJSONArray("wagony")
        if (wagonyArr != null) {
            for (i in 0 until wagonyArr.length()) {
                val wObj = wagonyArr.optJSONObject(i) ?: continue
                val wNo = (wObj.opt("nr") ?: wObj.opt("number"))?.toString()?.trim().orEmpty()
                val wType = (wObj.opt("typ") ?: wObj.opt("type"))?.toString()?.uppercase().orEmpty()
                if (wNo.isNotEmpty() && listOf("WR", "WARS", "WL", "BC").any { it in wType }) {
                    serviceWagons.add(wNo)
                }
            }
        }

        val targets = ArrayList<Pair<String, String>>()
        val seen = HashSet<String>()
        val classArr = skladJson.optJSONArray(classKey)
        if (classArr != null) {
            for (i in 0 until classArr.length()) {
                val wNo = classArr.opt(i)?.toString()?.trim().orEmpty()
                if (wNo.isEmpty() || wNo == "null" || wNo in seen || wNo in unavailable || wNo in serviceWagons) {
                    continue
                }
                val schemaId = schematMap[wNo].orEmpty()
                if (schemaId.isEmpty()) continue
                seen.add(wNo)
                targets.add(wNo to schemaId)
            }
        }

        return targets to hasKey
    }

    fun shiftQueryTime(departureTime: String, offsetMinutes: Int = -10): String {
        val clean = departureTime.trim().ifEmpty { "12:00" }
        val m = TIME_HH_MM_REGEX.matchEntire(clean) ?: return clean
        val hh = m.groupValues[1].toIntOrNull() ?: return clean
        val mm = m.groupValues[2].toIntOrNull() ?: return clean
        val total = (hh * 60 + mm + offsetMinutes).coerceIn(0, 23 * 60 + 59)
        return "%02d:%02d".format(total / 60, total % 60)
    }

    fun normalizeIsoDate(dateStr: String): String {
        var clean = dateStr.trim()
        if ("." in clean) {
            val parts = clean.split(".")
            if (parts.size == 3 && parts[2].length == 4) {
                clean = "${parts[2]}-${parts[1]}-${parts[0]}"
            }
        }
        return clean
    }

    fun buildEicSearchUrl(
        fromEva: String,
        toEva: String,
        dateStr: String,
        departureTime: String,
        directOnly: Boolean = true
    ): String {
        val cleanDate = normalizeIsoDate(dateStr)
        val queryTime = shiftQueryTime(departureTime, offsetMinutes = -10)
        val polbez = if (directOnly) "1" else "0"
        return "https://ebilet.intercity.pl/wyszukiwanie?" +
            "dwyj=$cleanDate&swyj=${fromEva.trim()}&sprzy=${toEva.trim()}&" +
            "time=$queryTime&przy=0&sprzez=&ticket100=1010&ticket50=&polbez=$polbez"
    }

    fun formatGrmDateTime(dateStr: String, hhMm: String): String? {
        val cleanDate = normalizeIsoDate(dateStr)
        val mDate = DATE_YYYY_MM_DD_REGEX.matchEntire(cleanDate) ?: return null
        val mTime = TIME_HH_MM_REGEX.matchEntire(hhMm.trim()) ?: return null
        val hh = mTime.groupValues[1].toIntOrNull() ?: return null
        val mm = mTime.groupValues[2].toIntOrNull() ?: return null
        if (hh !in 0..23 || mm !in 0..59) return null
        return "${mDate.groupValues[1]}${mDate.groupValues[2]}${mDate.groupValues[3]}%02d%02d".format(hh, mm)
    }

    fun parseGrmUrlTemplate(skladUrl: String?, svgUrl: String? = null): GrmUrlTemplate? {
        for (candidate in listOfNotNull(skladUrl, svgUrl)) {
            val trimmed = candidate.trim()
            if (trimmed.isEmpty()) continue
            val m = GRM_TEMPLATE_REGEX.find(trimmed)
            if (m != null) {
                return GrmUrlTemplate(
                    host = m.groupValues[1],
                    category = m.groupValues[2],
                    trainNo = m.groupValues[3]
                )
            }
        }
        return null
    }

    fun buildGrmSkladUrl(
        template: GrmUrlTemplate,
        fromEpa: String,
        toEpa: String,
        depDt: String,
        arrDt: String
    ): String {
        return "${template.host}/grm/sklad/wbnet/${template.category}/${template.trainNo}/" +
            "$arrDt/${fromEpa.trim()}/$depDt/${toEpa.trim()}"
    }

    fun buildGrmSvgUrl(
        template: GrmUrlTemplate,
        wagonNo: String,
        schemaId: String,
        fromEpa: String,
        toEpa: String,
        depDt: String,
        arrDt: String
    ): String {
        return "${template.host}/grm/wagon/svg/wbnet/${template.category}/${template.trainNo}/" +
            "${wagonNo.trim()}/${schemaId.trim()}/$depDt/$arrDt/${fromEpa.trim()}/${toEpa.trim()}"
    }

    fun computeArrivalDayOffset(
        arrivalTime: String,
        departureTime: String,
        depDayOffset: Int
    ): Int {
        val mArr = TIME_HH_MM_REGEX.matchEntire(arrivalTime.trim()) ?: return depDayOffset.coerceAtLeast(0)
        val mDep = TIME_HH_MM_REGEX.matchEntire(departureTime.trim()) ?: return depDayOffset.coerceAtLeast(0)
        val arrMin = (mArr.groupValues[1].toIntOrNull() ?: 0) * 60 + (mArr.groupValues[2].toIntOrNull() ?: 0)
        val depMin = (mDep.groupValues[1].toIntOrNull() ?: 0) * 60 + (mDep.groupValues[2].toIntOrNull() ?: 0)
        return if (arrMin > depMin + 300) {
            (depDayOffset - 1).coerceAtLeast(0)
        } else {
            depDayOffset.coerceAtLeast(0)
        }
    }
}
