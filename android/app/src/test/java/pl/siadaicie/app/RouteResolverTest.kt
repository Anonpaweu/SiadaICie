package pl.siadaicie.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import pl.siadaicie.app.domain.RouteHelpers
import pl.siadaicie.app.scraper.ScheduleClient

class RouteResolverTest {

    @Test
    fun metroHubAndNameNormalizationWork() {
        assertTrue(RouteHelpers.isSameMetroHub("Warszawa Zachodnia", "Warszawa Centralna"))
        assertTrue(RouteHelpers.isSameMetroHub("Gdańsk Główny", "Sopot"))
        assertFalse(RouteHelpers.isSameMetroHub("Warszawa Centralna", "Żyrardów"))
        assertEquals("lodz fabryczna", RouteHelpers.normalizePolishName("Łódź Fabryczna"))
        assertEquals("IC 1644", RouteHelpers.cleanPkpTrainName("  ic   1644 "))
        assertEquals("", RouteHelpers.cleanPkpTrainName("KM 1234"))
    }

    @Test
    fun parseStopsFromTiHtmlHandlesMidnightAndSlicing() {
        val html = """
            <table>
              <tr><td></td><td><a href="/pl/sq?input=5100010">Gdynia Główna</a></td><td></td><td>23:20</td></tr>
              <tr><td></td><td><a href="/pl/sq?input=5100009">Gdańsk Główny</a></td><td>23:45</td><td>23:48</td></tr>
              <tr><td></td><td><a href="/pl/sq?input=5100065">Warszawa Centralna</a></td><td>03:30</td><td>03:35</td></tr>
              <tr><td></td><td><a href="/pl/sq?input=5100028">Kraków Główny</a></td><td>08:06</td><td></td></tr>
            </table>
        """.trimIndent()

        val stops = ScheduleClient.parseStopsFromTiHtml(
            htmlText = html,
            fromStation = "Gdańsk Główny",
            toStation = "Kraków Główny",
            fromEva = "5100009",
            toEva = "5100028"
        )
        assertEquals(3, stops.size)
        assertEquals("Gdańsk Główny", stops[0].name)
        assertEquals("Warszawa Centralna", stops[1].name)
        assertEquals(1, stops[1].dayOffset)
        assertEquals("Kraków Główny", stops[2].name)
        assertEquals(498, stops[2].arrivalMin) // 08:06 next day minus 23:48 = 498 min
    }
}
