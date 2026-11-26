package pl.siadaicie.app.domain

import java.time.LocalDate
import java.time.format.DateTimeFormatter

data class StationInfo(
    val evaCode: String,
    val name: String,
    val normalizedName: String,
    val metropolitanGroup: String? = null,
    val isMainHub: Boolean = false,
    val icId: String? = null
)

data class StationStop(
    val seq: Int,
    val evaCode: String,
    val name: String,
    val arrivalMin: Int,
    val departureMin: Int,
    val arrivalTime: String = "",
    val departureTime: String = "",
    val metropolitanGroup: String? = null,
    val isMainHub: Boolean = false,
    val dayOffset: Int = 0
)

data class RouteSegment(
    val segIdx: Int,
    val fromStation: StationStop,
    val toStation: StationStop,
    val durationMin: Int,
    val fromStationName: String = fromStation.name,
    val toStationName: String = toStation.name,
    val departureTime: String = fromStation.departureTime,
    val arrivalTime: String = toStation.arrivalTime,
    val trainNumber: String = ""
)

data class TrainConnection(
    val trainNumber: String,
    val departure: String,
    val arrival: String,
    val duration: String,
    val changes: Int
)

data class SeatFilterPrefs(
    val allowBike: Boolean = true,
    val allowPregnant: Boolean = false,
    val allowChild: Boolean = false,
    val allowElderly: Boolean = false,
    val allowPrmAssist: Boolean = false,
    val allowWheelchair: Boolean = false
) {
    fun activeSpecialCount(): Int =
        listOf(allowBike, allowPregnant, allowChild, allowElderly, allowPrmAssist, allowWheelchair).count { it }
}

data class SegmentAvailabilityMap(
    val segmentIndex: Int,
    val fromStation: String,
    val toStation: String,
    val departureTime: String,
    val durationMin: Int,
    val fromEva: String = "",
    val toEva: String = "",
    val segmentDate: String = "",
    val carriages: Map<String, List<String>> = emptyMap(),
    val seatTags: Map<String, List<String>> = emptyMap(),
    val status: String = "OK",
    val errorReason: String? = null,
    val standingStatus: String? = null
)

data class FullTrainAvailabilityMap(
    val trainNumber: String,
    val travelDate: String,
    val fastPathUsed: Boolean = false,
    val totalRouteSegments: Int = 0,
    val noSeatmapMode: Boolean = false,
    val fullRouteStandingStatus: String? = null,
    val segments: List<SegmentAvailabilityMap> = emptyList()
)

object RouteHelpers {
    val METROPOLITAN_HUBS: Map<String, Pair<String, Boolean>> = mapOf(
        "Warszawa Zachodnia" to ("WARSZAWA" to false),
        "Warszawa Centralna" to ("WARSZAWA" to true),
        "Warszawa Śródmieście" to ("WARSZAWA" to false),
        "Warszawa Wschodnia" to ("WARSZAWA" to false),
        "Gdańsk Główny" to ("TROJMIASTO" to true),
        "Gdańsk Wrzeszcz" to ("TROJMIASTO" to false),
        "Gdańsk Oliwa" to ("TROJMIASTO" to false),
        "Sopot" to ("TROJMIASTO" to false),
        "Gdynia Główna" to ("TROJMIASTO" to true),
        "Katowice" to ("GOP" to true),
        "Sosnowiec Główny" to ("GOP" to false),
        "Zabrze" to ("GOP" to false),
        "Gliwice" to ("GOP" to true)
    )

    private val BRAND_REGEX = Regex("^(EIP|EIC|IC|TLK)\\s*(\\d{3,5})$", RegexOption.IGNORE_CASE)

    fun normalizePolishName(input: String): String {
        val sb = StringBuilder(input.length)
        for (ch in input.trim()) {
            sb.append(
                when (ch) {
                    'ą', 'Ą' -> 'a'
                    'ć', 'Ć' -> 'c'
                    'ę', 'Ę' -> 'e'
                    'ł', 'Ł' -> 'l'
                    'ń', 'Ń' -> 'n'
                    'ó', 'Ó' -> 'o'
                    'ś', 'Ś' -> 's'
                    'ź', 'Ź', 'ż', 'Ż' -> 'z'
                    else -> ch.lowercaseChar()
                }
            )
        }
        return sb.toString()
    }

    fun isSameMetroHub(stationA: String, stationB: String): Boolean {
        val normA = normalizePolishName(stationA)
        val normB = normalizePolishName(stationB)
        var groupA: String? = null
        var groupB: String? = null
        for ((name, meta) in METROPOLITAN_HUBS) {
            val normKey = normalizePolishName(name)
            if (normKey == normA) groupA = meta.first
            if (normKey == normB) groupB = meta.first
        }
        return groupA != null && groupA == groupB
    }

    fun cleanPkpTrainName(raw: String): String {
        val collapsed = raw.trim().replace(Regex("\\s+"), " ")
        val m = BRAND_REGEX.matchEntire(collapsed) ?: return ""
        return "${m.groupValues[1].uppercase()} ${m.groupValues[2]}"
    }

    fun timeToMinutes(tStr: String): Int {
        val parts = tStr.trim().split(":")
        if (parts.size < 2) return 0
        val hh = parts[0].toIntOrNull() ?: return 0
        val mm = parts[1].toIntOrNull() ?: return 0
        return hh * 60 + mm
    }

    fun computeDurationMin(fromStop: StationStop, toStop: StationStop, fallbackMin: Int = 30): Int {
        val depVal = fromStop.departureMin
        val arrVal = toStop.arrivalMin
        val diff = arrVal - depVal
        if (diff > 0) return diff
        return fallbackMin.coerceAtLeast(1)
    }

    fun computeSegmentDate(travelDate: String, dayOffset: Int = 0): String {
        if (dayOffset == 0) return travelDate
        return try {
            val dt = LocalDate.parse(travelDate.trim(), DateTimeFormatter.ISO_LOCAL_DATE)
            dt.plusDays(dayOffset.toLong()).format(DateTimeFormatter.ISO_LOCAL_DATE)
        } catch (_: Exception) {
            travelDate
        }
    }

    fun buildRouteSegments(stops: List<StationStop>, trainNumber: String = ""): List<RouteSegment> {
        if (stops.size < 2) return emptyList()
        return (0 until stops.size - 1).map { i ->
            val sFrom = stops[i]
            val sTo = stops[i + 1]
            val dur = (sTo.arrivalMin - sFrom.departureMin).coerceAtLeast(1)
            RouteSegment(
                segIdx = i,
                fromStation = sFrom,
                toStation = sTo,
                durationMin = dur,
                fromStationName = sFrom.name,
                toStationName = sTo.name,
                departureTime = sFrom.departureTime,
                arrivalTime = sTo.arrivalTime,
                trainNumber = trainNumber
            )
        }
    }
}
