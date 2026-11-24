package pl.iccbagno.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import pl.iccbagno.app.domain.FullTrainAvailabilityMap
import pl.iccbagno.app.domain.SeatFilterPrefs
import pl.iccbagno.app.domain.SegmentAvailabilityMap
import pl.iccbagno.app.optimizer.OptimizationProfile
import pl.iccbagno.app.optimizer.OptimizerConfig
import pl.iccbagno.app.optimizer.SeatMeta
import pl.iccbagno.app.optimizer.SeatOptimizer
import pl.iccbagno.app.scraper.GrmParser

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
}
