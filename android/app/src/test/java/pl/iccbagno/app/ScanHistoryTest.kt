package pl.iccbagno.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import pl.iccbagno.app.data.ScanHistoryItem
import pl.iccbagno.app.data.ScanHistoryRepository
import pl.iccbagno.app.data.ScanHistorySerializer
import pl.iccbagno.app.domain.FullTrainAvailabilityMap
import pl.iccbagno.app.domain.SegmentAvailabilityMap
import java.nio.file.Files

class ScanHistoryTest {

    private fun makeItem(idx: Int): ScanHistoryItem {
        val map = FullTrainAvailabilityMap(
            trainNumber = "IC 100$idx",
            travelDate = "2026-11-29",
            fastPathUsed = idx % 2 == 0,
            totalRouteSegments = 1,
            segments = listOf(
                SegmentAvailabilityMap(
                    segmentIndex = 0,
                    fromStation = "Stacja $idx",
                    toStation = "Warszawa Centralna",
                    departureTime = "17:27",
                    durationMin = 120,
                    fromEva = "5100069",
                    toEva = "5100065",
                    segmentDate = "2026-11-29",
                    carriages = mapOf("18" to listOf("11", "12")),
                    status = "OK"
                )
            )
        )
        return ScanHistoryItem(
            id = ScanHistorySerializer.buildId("Stacja $idx", "Warszawa Centralna", "2026-11-29", "IC 100$idx", 2),
            fromStation = "Stacja $idx",
            toStation = "Warszawa Centralna",
            travelDate = "2026-11-29",
            trainNumber = "IC 100$idx",
            ticketClass = 2,
            savedAtEpochMs = 1_700_000_000_000L + idx,
            topStrategyTitle = "Zloty Strzal $idx",
            transfersCount = 0,
            totalStandingMin = 0,
            availabilityMap = map
        )
    }

    @Test
    fun repositoryCapsAtFiveItemsAndDeduplicatesLatest() {
        val tempDir = Files.createTempDirectory("iccbagno_hist_test").toFile()
        val repo = ScanHistoryRepository(tempDir.resolve("scan_history.json"))
        try {
            for (i in 1..7) {
                repo.saveItem(makeItem(i))
            }
            val loaded = repo.loadHistory()
            assertEquals(5, loaded.size)
            assertEquals("IC 1007", loaded[0].trainNumber)
            assertEquals("IC 1003", loaded[4].trainNumber)

            // Re-saving item 4 should move it to index 0 without duplicating
            repo.saveItem(makeItem(4).copy(topStrategyTitle = "Updated 4"))
            val updated = repo.loadHistory()
            assertEquals(5, updated.size)
            assertEquals("IC 1004", updated[0].trainNumber)
            assertEquals("Updated 4", updated[0].topStrategyTitle)
            assertEquals(listOf("11", "12"), updated[0].availabilityMap.segments[0].carriages["18"])

            assertTrue(repo.clearHistory().isEmpty())
            assertTrue(repo.loadHistory().isEmpty())
        } finally {
            tempDir.deleteRecursively()
        }
    }
}
