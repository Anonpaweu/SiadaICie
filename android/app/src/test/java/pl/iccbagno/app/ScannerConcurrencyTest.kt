package pl.iccbagno.app

import org.junit.Assert.assertEquals
import org.junit.Test
import pl.iccbagno.app.scraper.EbiletWebViewScanner

class ScannerConcurrencyTest {

    @Test
    fun resolveHardwareConcurrencyScalesSafelyWithRamAndCores() {
        assertEquals(2 to 2, EbiletWebViewScanner.resolveHardwareConcurrency(totalRamMb = 4096L, cpuCores = 4))
        assertEquals(2 to 2, EbiletWebViewScanner.resolveHardwareConcurrency(totalRamMb = 8192L, cpuCores = 4))
        assertEquals(3 to 3, EbiletWebViewScanner.resolveHardwareConcurrency(totalRamMb = 6144L, cpuCores = 8))
        assertEquals(3 to 3, EbiletWebViewScanner.resolveHardwareConcurrency(totalRamMb = 12288L, cpuCores = 6))
        assertEquals(3 to 4, EbiletWebViewScanner.resolveHardwareConcurrency(totalRamMb = 16384L, cpuCores = 8))
    }
}
