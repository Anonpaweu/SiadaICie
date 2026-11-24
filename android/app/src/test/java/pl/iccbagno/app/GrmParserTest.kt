package pl.iccbagno.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import pl.iccbagno.app.scraper.GrmParser

class GrmParserTest {

    @Test
    fun parseGrmWagonSvg_filtersByClassAndExcludesPrm() {
        val svg = """
            <svg viewBox="0 0 100 100">
                <g aria-label="Miejsce 11, klasa 2, wolne" data-class="second class"><text>11</text></g>
                <g aria-label="Miejsce 12, klasa 1, wolne" data-class="first class"><text>12</text></g>
                <g aria-label="Miejsce 14, dla osoby na wózku, klasa 2, wolne"><text>14</text></g>
                <g aria-label="Miejsce 15, zajęte"><text>15</text></g>
                <g aria-label="Miejsce 18, klasa 2, wolne"><text>18</text></g>
            </svg>
        """.trimIndent()

        val class2 = GrmParser.parseGrmWagonSvg(svg, ticketClass = 2)
        assertEquals(listOf("11", "18"), class2)

        val class1 = GrmParser.parseGrmWagonSvg(svg, ticketClass = 1)
        assertEquals(listOf("12"), class1)
    }

    @Test
    fun selectGrmTargetWagons_skipsUnavailableAndWars() {
        val json = JSONObject(
            """
            {
              "klasa2": [8, 7, 6, 5],
              "wagonyNiedostepne": [7],
              "wagonySchemat": {"8": "1327", "7": "1326", "6": "999", "5": "1325"},
              "wagony": [
                {"nr": 6, "typ": "WR"},
                {"nr": 8, "typ": "B"}
              ]
            }
            """.trimIndent()
        )
        val (targets, hasKey) = GrmParser.selectGrmTargetWagons(json, ticketClass = 2)
        assertTrue(hasKey)
        assertEquals(listOf("8" to "1327", "5" to "1325"), targets)
    }

    @Test
    fun errorClassifiersAndUrlBuilderWorkProperly() {
        assertTrue(GrmParser.isSoldOutReason("BRAK_MIEJSC_KLASA_2: Brak dostępnych wagonów Klasy 2 w składzie."))
        assertTrue(GrmParser.isSeatmapWagonsUnavailableError("Brak dostępnych wagonów Klasy 2 w składzie."))
        assertFalse(GrmParser.isSoldOutReason("SCRAPE_ERROR: Timeout 35s"))
        assertEquals("15:10", GrmParser.shiftQueryTime("15:20", -10))
        assertEquals("00:00", GrmParser.shiftQueryTime("00:05", -10))

        val url = GrmParser.buildEicSearchUrl("5100065", "5100028", "24.11.2026", "14:00")
        assertTrue(url.startsWith("https://ebilet.intercity.pl/wyszukiwanie?"))
        assertTrue("dwyj=2026-11-24" in url)
        assertTrue("time=13:50" in url)
    }
}
