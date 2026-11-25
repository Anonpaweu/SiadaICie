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

    private fun makeItem(idx: Int, savedAtMs: Long = 1_700_000_000_000L + idx): ScanHistoryItem {
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
            savedAtEpochMs = savedAtMs,
            topStrategyTitle = "Zloty Strzal $idx",
            transfersCount = 0,
            totalStandingMin = 0,
            availabilityMap = map
        )
    }

    @Test
    fun repositoryCapsAtThreeItemsAndDeduplicatesLatest() {
        val tempDir = Files.createTempDirectory("iccbagno_hist_test").toFile()
        val nowMs = 1_700_000_000_000L + 100L
        val repo = ScanHistoryRepository(tempDir.resolve("scan_history.json")) { nowMs }
        try {
            for (i in 1..5) {
                repo.saveItem(makeItem(i))
            }
            val loaded = repo.loadHistory()
            assertEquals(3, loaded.size)
            assertEquals("IC 1005", loaded[0].trainNumber)
            assertEquals("IC 1003", loaded[2].trainNumber)

            // Re-saving item 3 should move it to index 0 without duplicating
            repo.saveItem(makeItem(3).copy(topStrategyTitle = "Updated 3"))
            val updated = repo.loadHistory()
            assertEquals(3, updated.size)
            assertEquals("IC 1003", updated[0].trainNumber)
            assertEquals("Updated 3", updated[0].topStrategyTitle)
            assertEquals(listOf("11", "12"), updated[0].availabilityMap.segments[0].carriages["18"])

            assertTrue(repo.clearHistory().isEmpty())
            assertTrue(repo.loadHistory().isEmpty())
        } finally {
            tempDir.deleteRecursively()
        }
    }

    @Test
    fun repositoryExpiresEntriesOlderThanOneHour() {
        val tempDir = Files.createTempDirectory("iccbagno_hist_ttl_test").toFile()
        var clockMs = 1_700_000_000_000L
        val repo = ScanHistoryRepository(tempDir.resolve("scan_history.json")) { clockMs }
        try {
            repo.saveItem(makeItem(1, savedAtMs = clockMs))
            clockMs += 30L * 60L * 1000L
            repo.saveItem(makeItem(2, savedAtMs = clockMs))

            // Both items still fresh at +30m
            assertEquals(2, repo.loadHistory().size)

            // Advance +31m (item 1 is 61m old -> expired; item 2 is 31m old -> kept)
            clockMs += 31L * 60L * 1000L
            val afterExpiry = repo.loadHistory()
            assertEquals(1, afterExpiry.size)
            assertEquals("IC 1002", afterExpiry[0].trainNumber)

            // Advance +30m more -> item 2 also expires (>60m)
            clockMs += 30L * 60L * 1000L
            assertTrue(repo.loadHistory().isEmpty())
        } finally {
            tempDir.deleteRecursively()
        }
    }
}
