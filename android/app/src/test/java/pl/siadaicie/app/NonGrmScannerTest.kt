package pl.siadaicie.app

import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import pl.siadaicie.app.domain.FullTrainAvailabilityMap
import pl.siadaicie.app.domain.SegmentAvailabilityMap
import pl.siadaicie.app.domain.StationStop
import pl.siadaicie.app.optimizer.SeatOptimizer
import pl.siadaicie.app.scraper.NonGrmScanner

class NonGrmScannerTest {

    private fun sampleStops(): List<StationStop> = listOf(
        StationStop(0, "5100069", "Wrocław Główny", 0, 0, "14:37", "14:37"),
        StationStop(1, "5102560", "Oława", 18, 19, "14:55", "14:56"),
        StationStop(2, "5100046", "Opole Główne", 46, 48, "15:23", "15:25"),
        StationStop(3, "5100237", "Lubliniec", 80, 81, "15:57", "15:58"),
        StationStop(4, "5100007", "Częstochowa", 107, 110, "16:24", "16:27"),
        StationStop(5, "5100065", "Warszawa Centralna", 243, 243, "18:40", "18:40")
    )

    @Test
    fun `extractTrainDigitsAndCategory parses prefix and digits`() {
        assertEquals("6138" to "IC", NonGrmScanner.extractTrainDigitsAndCategory("IC 6138 Orzeszkowa"))
        assertEquals("1300" to "EIP", NonGrmScanner.extractTrainDigitsAndCategory("EIP 1300"))
        assertEquals("3520" to "IC", NonGrmScanner.extractTrainDigitsAndCategory("3520"))
    }

    @Test
    fun `checkGrmAvailable detects false and fails open on true or null`() = runBlocking {
        val falseTransport = object : NonGrmScanner.EicHttpTransport {
            override suspend fun get(url: String): String = """{"available":false}"""
            override suspend fun postJson(url: String, jsonBody: String): String? = null
        }
        val trueTransport = object : NonGrmScanner.EicHttpTransport {
            override suspend fun get(url: String): String = """{"available":true}"""
            override suspend fun postJson(url: String, jsonBody: String): String? = null
        }
        val nullTransport = object : NonGrmScanner.EicHttpTransport {
            override suspend fun get(url: String): String? = null
            override suspend fun postJson(url: String, jsonBody: String): String? = null
        }

        assertFalse(NonGrmScanner.checkGrmAvailable("IC 6138", "2026-11-27", falseTransport))
        assertTrue(NonGrmScanner.checkGrmAvailable("IC 6138", "2026-11-27", trueTransport))
        assertTrue(NonGrmScanner.checkGrmAvailable("IC 6138", "2026-11-27", nullTransport))
    }

    @Test
    fun `parseSprawdzCenyEntry classifies available, special 47, standing 48 and overbooked`() {
        val avail = JSONArray().apply {
            put(JSONObject().apply {
                put("klasa", 2)
                put("cena", 1820)
                put("komunikaty", JSONArray())
                put("komunikatTekst", "")
            })
        }
        val rAvail = NonGrmScanner.parseSprawdzCenyEntry(avail, 2)
        assertTrue(rAvail.hasStandardSeats)
        assertNull(rAvail.standingStatus)
        assertEquals(18.20, rAvail.pricePln!!, 0.001)

        val special = JSONArray().apply {
            put(JSONObject().apply {
                put("klasa", 2)
                put("cena", 4940)
                put("komunikatTekst", "Brak miejsc ogólnodostępnych. Dostępne miejsca specjalne")
                put("komunikaty", JSONArray().put(JSONObject().put("komunikatKod", 47)))
            })
        }
        val rSpecial = NonGrmScanner.parseSprawdzCenyEntry(special, 2)
        assertFalse(rSpecial.hasStandardSeats)
        assertEquals("SPECIAL_ONLY", rSpecial.standingStatus)
        assertEquals(49.40, rSpecial.pricePln!!, 0.001)

        val standing = JSONArray().apply {
            put(JSONObject().apply {
                put("klasa", 2)
                put("cena", 9685)
                put("komunikatTekst", "Można kupić bilet bez gwarancji miejsca siedzącego.")
                put("komunikaty", JSONArray().put(JSONObject().put("komunikatKod", 48)))
            })
        }
        val rStanding = NonGrmScanner.parseSprawdzCenyEntry(standing, 2)
        assertFalse(rStanding.hasStandardSeats)
        assertEquals("AVAILABLE", rStanding.standingStatus)

        val over = JSONArray().apply {
            put(JSONObject().apply {
                put("klasa", 2)
                put("cena", 0)
                put("komunikatTekst", "Brak miejsc")
                put("komunikaty", JSONArray().put(JSONObject().put("komunikatKod", 51)))
            })
        }
        val rOver = NonGrmScanner.parseSprawdzCenyEntry(over, 2)
        assertFalse(rOver.hasStandardSeats)
        assertEquals("OVERBOOKED", rOver.standingStatus)
    }

    @Test
    fun `planNonGrmLegs returns fast-path full route when seats exist end to end`() = runBlocking {
        val stops = sampleStops()
        val shortCodes = stops.associate { it.evaCode to (100 + it.seq) }
        val plan = NonGrmScanner.planNonGrmLegs(
            stops = stops,
            shortCodes = shortCodes,
            exactDeps = emptyMap(),
            travelDate = "2026-11-27",
            checkPairFn = { NonGrmScanner.SprawdzCenyResult(true, null, 49.40, "") }
        )
        assertTrue(plan.fastPathUsed)
        assertEquals(1, plan.legs.size)
        assertEquals(0, plan.legs[0].startIdx)
        assertEquals(5, plan.legs[0].endIdx)
        assertFalse(plan.legs[0].isStanding)
    }

    @Test
    fun `planNonGrmLegs splits around bottleneck and merges consecutive gaps`() = runBlocking {
        val stops = sampleStops()
        val sidToIdx = stops.associate { (100 + it.seq) to it.seq }
        val shortCodes = stops.associate { it.evaCode to (100 + it.seq) }

        val plan = NonGrmScanner.planNonGrmLegs(
            stops = stops,
            shortCodes = shortCodes,
            exactDeps = emptyMap(),
            travelDate = "2026-11-27",
            checkPairFn = { q ->
                val fIdx = sidToIdx[q.fromShortId] ?: 0
                val tIdx = sidToIdx[q.toShortId] ?: 0
                if (tIdx <= 2 || fIdx >= 4) {
                    NonGrmScanner.SprawdzCenyResult(true, null, 25.0, "")
                } else {
                    NonGrmScanner.SprawdzCenyResult(false, "SPECIAL_ONLY", 30.0, "special")
                }
            }
        )

        assertFalse(plan.fastPathUsed)
        assertEquals("SPECIAL_ONLY", plan.fullRouteStandingStatus)
        assertEquals(3, plan.legs.size)
        assertEquals(0 to 2, plan.legs[0].startIdx to plan.legs[0].endIdx)
        assertFalse(plan.legs[0].isStanding)
        assertEquals(2 to 4, plan.legs[1].startIdx to plan.legs[1].endIdx)
        assertTrue(plan.legs[1].isStanding)
        assertEquals("SPECIAL_ONLY", plan.legs[1].standingStatus)
        assertEquals(4 to 5, plan.legs[2].startIdx to plan.legs[2].endIdx)
        assertFalse(plan.legs[2].isStanding)
    }

    @Test
    fun `SeatOptimizer preserves distinct AUTO legs without merging adjacent tickets`() {
        val map = FullTrainAvailabilityMap(
            trainNumber = "IC 6138",
            travelDate = "2026-11-27",
            noSeatmapMode = true,
            segments = listOf(
                SegmentAvailabilityMap(
                    segmentIndex = 0,
                    fromStation = "Wrocław Główny",
                    toStation = "Opole Główne",
                    departureTime = "14:37",
                    durationMin = 46,
                    fromEva = "5100069",
                    toEva = "5100046",
                    segmentDate = "2026-11-27",
                    carriages = mapOf("0" to listOf("AUTO_1")),
                    status = "OK"
                ),
                SegmentAvailabilityMap(
                    segmentIndex = 1,
                    fromStation = "Opole Główne",
                    toStation = "Lubliniec",
                    departureTime = "15:25",
                    durationMin = 31,
                    fromEva = "5100046",
                    toEva = "5100237",
                    segmentDate = "2026-11-27",
                    carriages = emptyMap(),
                    status = "SOLD_OUT",
                    standingStatus = "SPECIAL_ONLY"
                ),
                SegmentAvailabilityMap(
                    segmentIndex = 2,
                    fromStation = "Lubliniec",
                    toStation = "Częstochowa",
                    departureTime = "15:58",
                    durationMin = 30,
                    fromEva = "5100237",
                    toEva = "5100007",
                    segmentDate = "2026-11-27",
                    carriages = mapOf("0" to listOf("AUTO_2")),
                    status = "OK"
                ),
                SegmentAvailabilityMap(
                    segmentIndex = 3,
                    fromStation = "Częstochowa",
                    toStation = "Warszawa Centralna",
                    departureTime = "16:27",
                    durationMin = 136,
                    fromEva = "5100007",
                    toEva = "5100065",
                    segmentDate = "2026-11-27",
                    carriages = mapOf("0" to listOf("AUTO_3")),
                    status = "OK"
                )
            )
        )

        val matrix = SeatOptimizer.availabilityMapToMatrix(map)
        val strategies = SeatOptimizer.solveTopStrategies(
            availabilityMatrix = matrix.availabilityMatrix,
            seatsMeta = matrix.seatsMeta,
            stationNames = matrix.stationNames,
            segmentDurations = matrix.segmentDurations,
            defaultTravelDate = map.travelDate,
            segmentStandingStatuses = map.segments.map { it.standingStatus }
        )

        assertEquals(1, strategies.size)
        val steps = strategies[0].steps
        assertEquals(4, steps.size)
        assertEquals("AUTO_1", steps[0].seatNo)
        assertEquals("Wrocław Główny", steps[0].fromStationName)
        assertEquals("Opole Główne", steps[0].toStationName)
        assertNull(steps[0].standingStatus)
        assertTrue(steps[1].isStanding)
        assertEquals("SPECIAL_ONLY", steps[1].standingStatus)
        assertEquals("AUTO_2", steps[2].seatNo)
        assertEquals("AUTO_3", steps[3].seatNo)
        assertTrue(strategies[0].carriagesUsed.isEmpty())
    }

    @Test
    fun `SeatOptimizer propagates worst OVERBOOKED standingStatus across merged segments`() {
        val map = FullTrainAvailabilityMap(
            trainNumber = "IC 6138",
            travelDate = "2026-11-27",
            noSeatmapMode = true,
            segments = listOf(
                SegmentAvailabilityMap(0, "A", "B", "10:00", 30, carriages = mapOf("0" to listOf("AUTO_1")), status = "OK"),
                SegmentAvailabilityMap(1, "B", "C", "10:30", 25, carriages = emptyMap(), status = "SOLD_OUT", standingStatus = "AVAILABLE"),
                SegmentAvailabilityMap(2, "C", "D", "10:55", 20, carriages = emptyMap(), status = "SOLD_OUT", standingStatus = "OVERBOOKED"),
                SegmentAvailabilityMap(3, "D", "E", "11:15", 40, carriages = mapOf("0" to listOf("AUTO_2")), status = "OK")
            )
        )
        val matrix = SeatOptimizer.availabilityMapToMatrix(map)
        val strategies = SeatOptimizer.solveTopStrategies(
            availabilityMatrix = matrix.availabilityMatrix,
            seatsMeta = matrix.seatsMeta,
            stationNames = matrix.stationNames,
            segmentDurations = matrix.segmentDurations,
            defaultTravelDate = map.travelDate,
            segmentStandingStatuses = map.segments.map { it.standingStatus }
        )
        assertEquals(1, strategies.size)
        val steps = strategies[0].steps
        assertEquals(3, steps.size)
        assertTrue(steps[1].isStanding)
        assertEquals("OVERBOOKED", steps[1].standingStatus)
    }
}
