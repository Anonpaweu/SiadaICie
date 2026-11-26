package pl.siadaicie.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import pl.siadaicie.app.domain.FullTrainAvailabilityMap
import pl.siadaicie.app.domain.SeatFilterPrefs
import pl.siadaicie.app.domain.SegmentAvailabilityMap
import pl.siadaicie.app.optimizer.OptimizationProfile
import pl.siadaicie.app.optimizer.OptimizerConfig
import pl.siadaicie.app.optimizer.SeatMeta
import pl.siadaicie.app.optimizer.SeatOptimizer
import pl.siadaicie.app.scraper.GrmParser

class SeatOptimizerTest {

    @Test
    fun formatTransfersPl_handlesAllCases() {
        assertEquals("bez przesiadek", SeatOptimizer.formatTransfersPl(0))
        assertEquals("1 przesiadka", SeatOptimizer.formatTransfersPl(1))
        assertEquals("2 przesiadki", SeatOptimizer.formatTransfersPl(2))
        assertEquals("3 przesiadki", SeatOptimizer.formatTransfersPl(3))
        assertEquals("4 przesiadki", SeatOptimizer.formatTransfersPl(4))
        assertEquals("5 przesiadek", SeatOptimizer.formatTransfersPl(5))
        assertEquals("11 przesiadek", SeatOptimizer.formatTransfersPl(11))
        assertEquals("12 przesiadek", SeatOptimizer.formatTransfersPl(12))
        assertEquals("14 przesiadek", SeatOptimizer.formatTransfersPl(14))
        assertEquals("22 przesiadki", SeatOptimizer.formatTransfersPl(22))
    }

    @Test
    fun consecutiveStandingSegmentsMergedAndStandingTransferCostIsZero() {
        val matrix = mapOf(
            "11_14" to listOf(1, 0, 0, 0),
            "12_36" to listOf(0, 0, 0, 1)
        )
        val meta = mapOf(
            "11_14" to SeatMeta("11_14", 11, "14", 1),
            "12_36" to SeatMeta("12_36", 12, "36", 3)
        )
        val stations = listOf("Poznań Główny", "Września", "Konin", "Koło", "Warszawa Centralna")
        val durations = listOf(44, 21, 21, 95)

        val penaltyStanding = SeatOptimizer.calculateTransferPenalty(
            SeatMeta("STANDING", 0, "KORYTARZ"),
            SeatMeta("STANDING", 0, "KORYTARZ"),
            isStandingB = true,
            config = OptimizerConfig(profile = OptimizationProfile.BALANCED)
        )
        assertEquals(0.0, penaltyStanding, 0.0001)

        val strategies = SeatOptimizer.solveTopStrategies(matrix, meta, stations, durations, topK = 1)
        assertTrue(strategies.isNotEmpty())
        val best = strategies.first()
        assertEquals(1, best.transfersCount)
        assertEquals(42, best.totalStandingMin)
        assertEquals(3, best.steps.size)
        assertEquals("Optymalna kombinacja (1 przesiadka, korytarz 42 min)", best.title)
        assertTrue(best.steps[1].isStanding)
        assertEquals("Września", best.steps[1].fromStationName)
        assertEquals("Koło", best.steps[1].toStationName)
        assertEquals(42, best.steps[1].durationMin)
    }

    @Test
    fun goldenShotNoTransferStrategyProducedWithBookingUrl() {
        val availMap = FullTrainAvailabilityMap(
            trainNumber = "IC 1644",
            travelDate = "2026-11-30",
            fastPathUsed = true,
            totalRouteSegments = 1,
            segments = listOf(
                SegmentAvailabilityMap(
                    segmentIndex = 0,
                    fromStation = "Warszawa Centralna",
                    toStation = "Wrocław Główny",
                    departureTime = "15:20",
                    durationMin = 240,
                    fromEva = "5100065",
                    toEva = "5100069",
                    segmentDate = "2026-11-30",
                    carriages = mapOf("3" to listOf("14", "18"))
                )
            )
        )
        val matrix = SeatOptimizer.availabilityMapToMatrix(availMap)
        val strategies = SeatOptimizer.solveTopStrategies(
            availabilityMatrix = matrix.availabilityMatrix,
            seatsMeta = matrix.seatsMeta,
            stationNames = matrix.stationNames,
            segmentDurations = matrix.segmentDurations,
            stationEvas = mapOf("Warszawa Centralna" to "5100065", "Wrocław Główny" to "5100069"),
            departureTimes = listOf("15:20"),
            segmentDates = listOf("2026-11-30"),
            defaultTravelDate = "2026-11-30"
        )
        assertTrue(strategies.isNotEmpty())
        assertEquals("100% Trasy bez przesiadek (Złoty Strzał)", strategies[0].title)
        assertEquals(0, strategies[0].transfersCount)
        assertTrue(strategies[0].steps[0].bookingUrl?.contains("swyj=5100065") == true)
        assertTrue(strategies[0].steps[0].bookingUrl?.contains("time=15:10") == true)
    }

    @Test
    fun seatHierarchyAndFiltersRespectNormalQuietBikeSpecialWheelchair() {
        // Compartment 3: seats 31 (BIKE), 32 (WHEELCHAIR), 33 (ELDERLY), 34 (QUIET - equivalent availability)
        // Ensures equivalence pruning keeps tier 0 quiet/normal seat 34 rather than pruning it behind 31/32/33.
        val availMap = FullTrainAvailabilityMap(
            trainNumber = "IC 6122",
            travelDate = "2026-11-29",
            fastPathUsed = true,
            totalRouteSegments = 1,
            segments = listOf(
                SegmentAvailabilityMap(
                    segmentIndex = 0,
                    fromStation = "Wrocław Główny",
                    toStation = "Warszawa Centralna",
                    departureTime = "17:27",
                    durationMin = 261,
                    carriages = mapOf("7" to listOf("31", "32", "33", "34")),
                    seatTags = mapOf(
                        "7_31" to listOf(GrmParser.TAG_BIKE),
                        "7_32" to listOf(GrmParser.TAG_WHEELCHAIR),
                        "7_33" to listOf(GrmParser.TAG_ELDERLY),
                        "7_34" to listOf(GrmParser.TAG_QUIET)
                    )
                )
            )
        )
        val matrix = SeatOptimizer.availabilityMapToMatrix(availMap)

        // With all allowed, quiet/normal seat 34 must beat bike 31, elderly 33, wheelchair 32
        val allAllowed = SeatFilterPrefs(
            allowBike = true,
            allowPregnant = true,
            allowChild = true,
            allowElderly = true,
            allowPrmAssist = true,
            allowWheelchair = true
        )
        val sAll = SeatOptimizer.solveTopStrategies(
            matrix.availabilityMatrix,
            matrix.seatsMeta,
            matrix.stationNames,
            matrix.segmentDurations,
            config = OptimizerConfig(seatFilters = allAllowed)
        )
        assertEquals("34", sAll[0].steps[0].seatNo)

        // When only bike + elderly + wheelchair exist (no normal), bike (31) must beat elderly (33) and wheelchair (32)
        val noNormalMap = availMap.copy(
            segments = listOf(
                availMap.segments[0].copy(carriages = mapOf("7" to listOf("31", "32", "33")))
            )
        )
        val noNormalMatrix = SeatOptimizer.availabilityMapToMatrix(noNormalMap)
        val sBike = SeatOptimizer.solveTopStrategies(
            noNormalMatrix.availabilityMatrix,
            noNormalMatrix.seatsMeta,
            noNormalMatrix.stationNames,
            noNormalMatrix.segmentDurations,
            config = OptimizerConfig(seatFilters = allAllowed)
        )
        assertEquals("31", sBike[0].steps[0].seatNo)
        assertEquals(listOf(GrmParser.TAG_BIKE), sBike[0].steps[0].seatTags)

        // When default prefs active and only seat 33 (elderly) & 32 (wheelchair) are free -> empty strategies
        val restrictedOnlyMap = availMap.copy(
            segments = listOf(
                availMap.segments[0].copy(carriages = mapOf("7" to listOf("32", "33")))
            )
        )
        val restrictedMatrix = SeatOptimizer.availabilityMapToMatrix(restrictedOnlyMap)
        val sNone = SeatOptimizer.solveTopStrategies(
            restrictedMatrix.availabilityMatrix,
            restrictedMatrix.seatsMeta,
            restrictedMatrix.stationNames,
            restrictedMatrix.segmentDurations,
            config = OptimizerConfig(seatFilters = SeatFilterPrefs())
        )
        assertTrue(sNone.isEmpty())

        // Enabling allowElderly picks seat 33 over disabled wheelchair seat 32
        val sElderly = SeatOptimizer.solveTopStrategies(
            restrictedMatrix.availabilityMatrix,
            restrictedMatrix.seatsMeta,
            restrictedMatrix.stationNames,
            restrictedMatrix.segmentDurations,
            config = OptimizerConfig(seatFilters = SeatFilterPrefs(allowElderly = true))
        )
        assertEquals("33", sElderly[0].steps[0].seatNo)
        assertEquals(listOf(GrmParser.TAG_ELDERLY), sElderly[0].steps[0].seatTags)
    }

    @Test
    fun shortRoutePartialAvailabilityNeverReturnsEmptyStrategies() {
        val stations = listOf("Poznań Główny", "Swarzędz", "Września", "Słupca", "Konin")
        val durations = listOf(9, 21, 12, 14)
        val avail = mapOf(
            "12_13" to listOf(1, 0, 0, 0),
            "14_32" to listOf(0, 1, 1, 0)
        )
        val meta = mapOf(
            "12_13" to SeatMeta("12_13", 12, "13", 1),
            "14_32" to SeatMeta("14_32", 14, "32", 3)
        )
        val strategies = SeatOptimizer.solveTopStrategies(avail, meta, stations, durations)
        assertTrue(strategies.isNotEmpty())
        assertTrue(strategies.all { st -> st.steps.any { !it.isStanding } })
        val best = strategies[0]
        assertTrue(best.steps.any { it.seatUid == "14_32" })
        assertEquals(23, best.totalStandingMin)
    }

    @Test
    fun standingRouteDiversityPicksDistinctCarriages() {
        val stations = listOf("Poznań Główny", "Swarzędz", "Września", "Słupca", "Konin")
        val durations = listOf(9, 21, 12, 14)
        val avail = mapOf(
            "12_11" to listOf(1, 0, 0, 0),
            "14_32" to listOf(0, 1, 1, 0),
            "22_93" to listOf(1, 0, 0, 0)
        )
        val meta = mapOf(
            "12_11" to SeatMeta("12_11", 12, "11", 1),
            "14_32" to SeatMeta("14_32", 14, "32", 3),
            "22_93" to SeatMeta("22_93", 22, "93", 9)
        )
        val strategies = SeatOptimizer.solveTopStrategies(avail, meta, stations, durations, topK = 3)
        assertEquals(3, strategies.size)
        assertEquals(listOf(14, 12, 22), strategies.map { it.carriagesUsed[0] })
    }

    @Test
    fun minimalTransfersDoesNotAddMoreTransfersThanBalancedOnStandingRoute() {
        val stations = listOf("A", "B", "C", "D", "E", "F")
        val durations = listOf(35, 30, 40, 45, 50)
        val avail = mapOf(
            "10_11" to listOf(0, 1, 1, 0, 0),
            "10_12" to listOf(0, 0, 0, 1, 1),
            "11_21" to listOf(0, 0, 1, 1, 1)
        )
        val meta = mapOf(
            "10_11" to SeatMeta("10_11", 10, "11", 1),
            "10_12" to SeatMeta("10_12", 10, "12", 2),
            "11_21" to SeatMeta("11_21", 11, "21", 2)
        )
        val bal = SeatOptimizer.solveTopStrategies(
            avail, meta, stations, durations,
            config = OptimizerConfig(profile = OptimizationProfile.BALANCED)
        )
        val minTr = SeatOptimizer.solveTopStrategies(
            avail, meta, stations, durations,
            config = OptimizerConfig(profile = OptimizationProfile.MINIMAL_TRANSFERS)
        )
        assertTrue(bal.isNotEmpty() && minTr.isNotEmpty())
        assertTrue(minTr[0].transfersCount <= bal[0].transfersCount)
    }

    @Test
    fun standingStepIncludesBookingUrl() {
        val stations = listOf("Poznań Główny", "Swarzędz", "Słupca", "Konin")
        val durations = listOf(9, 33, 14)
        val avail = mapOf("14_32" to listOf(0, 1, 0))
        val meta = mapOf("14_32" to SeatMeta("14_32", 14, "32", 3))
        val strategies = SeatOptimizer.solveTopStrategies(
            availabilityMatrix = avail,
            seatsMeta = meta,
            stationNames = stations,
            segmentDurations = durations,
            stationEvas = mapOf(
                "Poznań Główny" to "5100081",
                "Swarzędz" to "5103667",
                "Słupca" to "5100359",
                "Konin" to "5100026"
            ),
            departureTimes = listOf("17:44", "17:53", "18:26"),
            segmentDates = listOf("2026-11-27", "2026-11-27", "2026-11-27"),
            defaultTravelDate = "2026-11-27"
        )
        assertTrue(strategies.isNotEmpty())
        val firstStanding = strategies[0].steps.first { it.isStanding }
        assertTrue(firstStanding.bookingUrl?.contains("swyj=5100081") == true)
        assertTrue(firstStanding.bookingUrl?.contains("sprzy=5103667") == true)
    }

    @Test
    fun bikeSuggestionTriggersOnlyWhenBikeDisabledAndStrictlyImprovesJourney() {
        val mapWithBike = FullTrainAvailabilityMap(
            trainNumber = "IC 6120",
            travelDate = "2026-11-27",
            segments = listOf(
                SegmentAvailabilityMap(
                    segmentIndex = 0,
                    fromStation = "Wrocław Główny",
                    toStation = "Oleśnica",
                    departureTime = "19:30",
                    durationMin = 25,
                    carriages = mapOf("7" to listOf("12")),
                    seatTags = mapOf("7_12" to listOf(GrmParser.TAG_BIKE))
                ),
                SegmentAvailabilityMap(
                    segmentIndex = 1,
                    fromStation = "Oleśnica",
                    toStation = "Warszawa Centralna",
                    departureTime = "19:55",
                    durationMin = 210,
                    carriages = mapOf("5" to listOf("44"), "7" to listOf("12")),
                    seatTags = mapOf("7_12" to listOf(GrmParser.TAG_BIKE))
                )
            )
        )
        val matrix = SeatOptimizer.availabilityMapToMatrix(mapWithBike)
        val noBikePrefs = SeatFilterPrefs(allowBike = false)
        val currentNoBike = SeatOptimizer.solveTopStrategies(
            matrix.availabilityMatrix,
            matrix.seatsMeta,
            matrix.stationNames,
            matrix.segmentDurations,
            config = OptimizerConfig(seatFilters = noBikePrefs)
        )
        assertEquals(25, currentNoBike[0].totalStandingMin)

        // When allowBike = false, suggests 100% seated without transfers
        val hint = SeatOptimizer.computeBikeSuggestion(
            mapWithBike,
            OptimizationProfile.BALANCED,
            noBikePrefs,
            currentNoBike
        )
        assertNotNull(hint)
        assertTrue(hint!!.contains("100%"))

        // When allowBike = true, never suggests
        assertNull(
            SeatOptimizer.computeBikeSuggestion(
                mapWithBike,
                OptimizationProfile.BALANCED,
                SeatFilterPrefs(allowBike = true),
                currentNoBike
            )
        )

        // When only ELDERLY / WHEELCHAIR exist (no BIKE), never suggests them
        val mapElderlyOnly = mapWithBike.copy(
            segments = mapWithBike.segments.map { seg ->
                seg.copy(seatTags = mapOf("7_12" to listOf(GrmParser.TAG_ELDERLY)))
            }
        )
        assertNull(
            SeatOptimizer.computeBikeSuggestion(
                mapElderlyOnly,
                OptimizationProfile.BALANCED,
                noBikePrefs,
                currentNoBike
            )
        )
    }

    @Test
    fun standingStepInfersSpecialOnlyWhenFilteredOutSpecialSeatsExist() {
        val map = FullTrainAvailabilityMap(
            trainNumber = "IC 1644",
            travelDate = "2026-11-27",
            segments = listOf(
                SegmentAvailabilityMap(
                    segmentIndex = 0,
                    fromStation = "Koluszki",
                    toStation = "Częstochowa Stradom",
                    departureTime = "16:15",
                    durationMin = 70,
                    carriages = mapOf("5" to listOf("44"))
                ),
                SegmentAvailabilityMap(
                    segmentIndex = 1,
                    fromStation = "Częstochowa Stradom",
                    toStation = "Opole Główne",
                    departureTime = "17:25",
                    durationMin = 55,
                    carriages = mapOf("7" to listOf("15")),
                    seatTags = mapOf("7_15" to listOf(GrmParser.TAG_ELDERLY))
                )
            )
        )
        val matrix = SeatOptimizer.availabilityMapToMatrix(map)

        // Default prefs exclude ELDERLY -> second segment becomes standing with SPECIAL_ONLY
        val resDefault = SeatOptimizer.solveTopStrategies(
            matrix.availabilityMatrix,
            matrix.seatsMeta,
            matrix.stationNames,
            matrix.segmentDurations,
            config = OptimizerConfig(seatFilters = SeatFilterPrefs())
        )
        assertEquals(2, resDefault[0].steps.size)
        assertTrue(resDefault[0].steps[1].isStanding)
        assertEquals("SPECIAL_ONLY", resDefault[0].steps[1].standingStatus)

        // Enabling allowElderly turns it into a seated step
        val resElderly = SeatOptimizer.solveTopStrategies(
            matrix.availabilityMatrix,
            matrix.seatsMeta,
            matrix.stationNames,
            matrix.segmentDurations,
            config = OptimizerConfig(seatFilters = SeatFilterPrefs(allowElderly = true))
        )
        assertEquals(2, resElderly[0].steps.size)
        assertTrue(!resElderly[0].steps[1].isStanding)
        assertEquals("15", resElderly[0].steps[1].seatNo)
    }
}
