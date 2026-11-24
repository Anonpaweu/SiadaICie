package pl.iccbagno.app.scraper

import org.json.JSONObject
import org.jsoup.Jsoup
import org.jsoup.parser.Parser

object GrmParser {
    const val TARGET_CLASS = 2

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

    fun isPrmWheelchairLabel(ariaLabel: String?): Boolean {
        val low = (ariaLabel ?: "").lowercase()
        return PRM_WHEELCHAIR_MARKERS.any { it in low }
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
        if (svgXml.isBlank()) return emptyList()

        val isClass1 = ticketClass == 1
        val seats = HashSet<String>()

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
            if (isPrmWheelchairLabel(ariaL)) continue

            var num = g.selectFirst("text")?.text()?.trim().orEmpty()
            if (num.isEmpty() || num.toIntOrNull() == null) {
                num = SEAT_ARIA_REGEX.find(aria)?.groupValues?.getOrNull(1).orEmpty()
            }
            if (num.toIntOrNull() != null) {
                seats.add(num)
            }
        }

        return seats.sortedBy { it.toIntOrNull() ?: Int.MAX_VALUE }
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
}
