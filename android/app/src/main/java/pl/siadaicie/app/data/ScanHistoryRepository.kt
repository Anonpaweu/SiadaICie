package pl.siadaicie.app.data

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONArray
import org.json.JSONObject
import pl.siadaicie.app.domain.FullTrainAvailabilityMap
import pl.siadaicie.app.domain.SegmentAvailabilityMap
import pl.siadaicie.app.optimizer.OptimizationStrategy
import java.io.File

data class ScanHistoryItem(
    val id: String,
    val fromStation: String,
    val toStation: String,
    val travelDate: String,
    val trainNumber: String,
    val ticketClass: Int,
    val savedAtEpochMs: Long,
    val topStrategyTitle: String,
    val transfersCount: Int,
    val totalStandingMin: Int,
    val availabilityMap: FullTrainAvailabilityMap
)

object ScanHistorySerializer {
    const val MAX_ITEMS = 3
    const val MAX_AGE_MS = 60L * 60L * 1000L

    fun buildId(
        fromStation: String,
        toStation: String,
        travelDate: String,
        trainNumber: String,
        ticketClass: Int
    ): String = "${fromStation.trim()}|${toStation.trim()}|${travelDate.trim()}|${trainNumber.trim()}|$ticketClass"

    fun encode(items: List<ScanHistoryItem>): String {
        val arr = JSONArray()
        for (item in items.take(MAX_ITEMS)) {
            val mapObj = JSONObject().apply {
                put("trainNumber", item.availabilityMap.trainNumber)
                put("travelDate", item.availabilityMap.travelDate)
                put("fastPathUsed", item.availabilityMap.fastPathUsed)
                put("totalRouteSegments", item.availabilityMap.totalRouteSegments)
                put("noSeatmapMode", item.availabilityMap.noSeatmapMode)
                put("fullRouteStandingStatus", item.availabilityMap.fullRouteStandingStatus ?: JSONObject.NULL)
                val segArr = JSONArray()
                for (seg in item.availabilityMap.segments) {
                    val carObj = JSONObject()
                    for ((wagon, seats) in seg.carriages) {
                        carObj.put(wagon, JSONArray(seats))
                    }
                    val tagsObj = JSONObject()
                    for ((uid, tags) in seg.seatTags) {
                        tagsObj.put(uid, JSONArray(tags))
                    }
                    segArr.put(
                        JSONObject().apply {
                            put("segmentIndex", seg.segmentIndex)
                            put("fromStation", seg.fromStation)
                            put("toStation", seg.toStation)
                            put("departureTime", seg.departureTime)
                            put("durationMin", seg.durationMin)
                            put("fromEva", seg.fromEva)
                            put("toEva", seg.toEva)
                            put("segmentDate", seg.segmentDate)
                            put("carriages", carObj)
                            put("seatTags", tagsObj)
                            put("status", seg.status)
                            put("errorReason", seg.errorReason ?: JSONObject.NULL)
                            put("standingStatus", seg.standingStatus ?: JSONObject.NULL)
                        }
                    )
                }
                put("segments", segArr)
            }
            arr.put(
                JSONObject().apply {
                    put("id", item.id)
                    put("fromStation", item.fromStation)
                    put("toStation", item.toStation)
                    put("travelDate", item.travelDate)
                    put("trainNumber", item.trainNumber)
                    put("ticketClass", item.ticketClass)
                    put("savedAtEpochMs", item.savedAtEpochMs)
                    put("topStrategyTitle", item.topStrategyTitle)
                    put("transfersCount", item.transfersCount)
                    put("totalStandingMin", item.totalStandingMin)
                    put("availabilityMap", mapObj)
                }
            )
        }
        return arr.toString()
    }

    fun decode(rawJson: String): List<ScanHistoryItem> {
        if (rawJson.isBlank()) return emptyList()
        return try {
            val arr = JSONArray(rawJson)
            val result = ArrayList<ScanHistoryItem>(arr.length())
            for (i in 0 until arr.length()) {
                val obj = arr.optJSONObject(i) ?: continue
                val mapObj = obj.optJSONObject("availabilityMap") ?: continue
                val segArr = mapObj.optJSONArray("segments") ?: JSONArray()
                val segs = ArrayList<SegmentAvailabilityMap>(segArr.length())
                for (j in 0 until segArr.length()) {
                    val sObj = segArr.optJSONObject(j) ?: continue
                    val carObj = sObj.optJSONObject("carriages") ?: JSONObject()
                    val carriages = LinkedHashMap<String, List<String>>()
                    val keys = carObj.keys()
                    while (keys.hasNext()) {
                        val w = keys.next()
                        val seatsArr = carObj.optJSONArray(w) ?: continue
                        val seatList = ArrayList<String>(seatsArr.length())
                        for (k in 0 until seatsArr.length()) {
                            val s = seatsArr.optString(k, "")
                            if (s.isNotEmpty()) seatList.add(s)
                        }
                        carriages[w] = seatList
                    }
                    val tagsJsonObj = sObj.optJSONObject("seatTags")
                    val seatTags = LinkedHashMap<String, List<String>>()
                    if (tagsJsonObj != null) {
                        val tKeys = tagsJsonObj.keys()
                        while (tKeys.hasNext()) {
                            val uid = tKeys.next()
                            val tArr = tagsJsonObj.optJSONArray(uid) ?: continue
                            val tList = ArrayList<String>(tArr.length())
                            for (k in 0 until tArr.length()) {
                                val t = tArr.optString(k, "")
                                if (t.isNotEmpty()) tList.add(t)
                            }
                            if (tList.isNotEmpty()) seatTags[uid] = tList
                        }
                    }
                    segs.add(
                        SegmentAvailabilityMap(
                            segmentIndex = sObj.optInt("segmentIndex", j),
                            fromStation = sObj.optString("fromStation", ""),
                            toStation = sObj.optString("toStation", ""),
                            departureTime = sObj.optString("departureTime", ""),
                            durationMin = sObj.optInt("durationMin", 30),
                            fromEva = sObj.optString("fromEva", ""),
                            toEva = sObj.optString("toEva", ""),
                            segmentDate = sObj.optString("segmentDate", ""),
                            carriages = carriages,
                            seatTags = seatTags,
                            status = sObj.optString("status", "OK"),
                            errorReason = if (sObj.isNull("errorReason")) null else sObj.optString("errorReason"),
                            standingStatus = if (sObj.isNull("standingStatus")) null else sObj.optString("standingStatus")
                        )
                    )
                }
                val map = FullTrainAvailabilityMap(
                    trainNumber = mapObj.optString("trainNumber", ""),
                    travelDate = mapObj.optString("travelDate", ""),
                    fastPathUsed = mapObj.optBoolean("fastPathUsed", false),
                    totalRouteSegments = mapObj.optInt("totalRouteSegments", segs.size),
                    noSeatmapMode = mapObj.optBoolean("noSeatmapMode", false),
                    fullRouteStandingStatus = if (mapObj.isNull("fullRouteStandingStatus")) null else mapObj.optString("fullRouteStandingStatus"),
                    segments = segs
                )
                result.add(
                    ScanHistoryItem(
                        id = obj.optString("id", ""),
                        fromStation = obj.optString("fromStation", ""),
                        toStation = obj.optString("toStation", ""),
                        travelDate = obj.optString("travelDate", ""),
                        trainNumber = obj.optString("trainNumber", ""),
                        ticketClass = obj.optInt("ticketClass", 2),
                        savedAtEpochMs = obj.optLong("savedAtEpochMs", 0L),
                        topStrategyTitle = obj.optString("topStrategyTitle", ""),
                        transfersCount = obj.optInt("transfersCount", 0),
                        totalStandingMin = obj.optInt("totalStandingMin", 0),
                        availabilityMap = map
                    )
                )
            }
            result.take(MAX_ITEMS)
        } catch (_: Exception) {
            emptyList()
        }
    }
}

class ScanHistoryRepository(
    private val file: File,
    private val nowProvider: () -> Long = { System.currentTimeMillis() }
) {
    constructor(context: Context) : this(File(context.filesDir, "scan_history.json"))

    @Synchronized
    fun loadHistory(): List<ScanHistoryItem> {
        if (!file.exists()) return emptyList()
        return try {
            val decoded = ScanHistorySerializer.decode(file.readText(Charsets.UTF_8))
            val now = nowProvider()
            val fresh = decoded
                .filter { (now - it.savedAtEpochMs) <= ScanHistorySerializer.MAX_AGE_MS }
                .take(ScanHistorySerializer.MAX_ITEMS)
            if (fresh.size != decoded.size) {
                if (fresh.isEmpty()) {
                    clearHistory()
                } else {
                    writeItems(fresh)
                }
            }
            fresh
        } catch (_: Exception) {
            emptyList()
        }
    }

    @Synchronized
    fun saveItem(item: ScanHistoryItem): List<ScanHistoryItem> {
        val current = loadHistory().filter { it.id != item.id }
        val updated = (listOf(item) + current).take(ScanHistorySerializer.MAX_ITEMS)
        writeItems(updated)
        return updated
    }

    @Synchronized
    fun clearHistory(): List<ScanHistoryItem> {
        try {
            if (file.exists()) file.delete()
        } catch (_: Exception) {
        }
        return emptyList()
    }

    private fun writeItems(items: List<ScanHistoryItem>) {
        try {
            file.parentFile?.mkdirs()
            val encoded = ScanHistorySerializer.encode(items)
            val tmp = File(file.parentFile, "${file.name}.tmp")
            tmp.writeText(encoded, Charsets.UTF_8)
            if (!tmp.renameTo(file)) {
                file.writeText(encoded, Charsets.UTF_8)
                tmp.delete()
            }
        } catch (_: Exception) {
        }
    }
}

object ScanHistoryStore {
    private val _history = MutableStateFlow<List<ScanHistoryItem>>(emptyList())
    val history: StateFlow<List<ScanHistoryItem>> = _history.asStateFlow()

    fun refresh(context: Context) {
        _history.value = ScanHistoryRepository(context.applicationContext).loadHistory()
    }

    fun saveScan(
        context: Context,
        fromStation: String,
        toStation: String,
        travelDate: String,
        trainNumber: String,
        ticketClass: Int,
        map: FullTrainAvailabilityMap,
        strategies: List<OptimizationStrategy>
    ) {
        val top = strategies.firstOrNull() ?: return
        val item = ScanHistoryItem(
            id = ScanHistorySerializer.buildId(fromStation, toStation, travelDate, trainNumber, ticketClass),
            fromStation = fromStation,
            toStation = toStation,
            travelDate = travelDate,
            trainNumber = trainNumber,
            ticketClass = ticketClass,
            savedAtEpochMs = System.currentTimeMillis(),
            topStrategyTitle = top.title,
            transfersCount = top.transfersCount,
            totalStandingMin = top.totalStandingMin,
            availabilityMap = map
        )
        _history.value = ScanHistoryRepository(context.applicationContext).saveItem(item)
    }

    fun clear(context: Context) {
        _history.value = ScanHistoryRepository(context.applicationContext).clearHistory()
    }
}
