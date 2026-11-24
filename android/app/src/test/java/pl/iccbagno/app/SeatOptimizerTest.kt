package pl.iccbagno.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import pl.iccbagno.app.domain.FullTrainAvailabilityMap
import pl.iccbagno.app.domain.SegmentAvailabilityMap
import pl.iccbagno.app.optimizer.OptimizationProfile
import pl.iccbagno.app.optimizer.OptimizerConfig
import pl.iccbagno.app.optimizer.SeatMeta
import pl.iccbagno.app.optimizer.SeatOptimizer

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
}
