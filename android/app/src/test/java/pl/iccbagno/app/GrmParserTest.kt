package pl.iccbagno.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
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

    @Test
    fun grmUrlTemplateAndDirectBuildersWorkProperly() {
        val skladUrl = "https://api-gateway.intercity.pl/grm/sklad/wbnet/IC/1644/202611241923/5100136/202611241520/5100143"
        val svgUrl = "https://api-gateway.intercity.pl/grm/wagon/svg/wbnet/IC/1644/8/1327,WITHOUT_COMPARTMENTS/202611241520/202611241923/5100136/5100143"
        val expected = GrmParser.GrmUrlTemplate(
            host = "https://api-gateway.intercity.pl",
            category = "IC",
            trainNo = "1644"
        )

        assertEquals(expected, GrmParser.parseGrmUrlTemplate(skladUrl))
        assertEquals(expected, GrmParser.parseGrmUrlTemplate(null, svgUrl))
        assertNull(GrmParser.parseGrmUrlTemplate("https://example.com/other"))

        assertEquals("202611240905", GrmParser.formatGrmDateTime("2026-11-24", "9:05"))
        assertEquals("202611241520", GrmParser.formatGrmDateTime("24.11.2026", "15:20"))
        assertNull(GrmParser.formatGrmDateTime("bad-date", "15:20"))
        assertNull(GrmParser.formatGrmDateTime("2026-11-24", "25:99"))

        val builtSklad = GrmParser.buildGrmSkladUrl(
            template = expected,
            fromEpa = "5100139",
            toEpa = "5100811",
            depDt = "202611241525",
            arrDt = "202611241545"
        )
        assertEquals(
            "https://api-gateway.intercity.pl/grm/sklad/wbnet/IC/1644/202611241545/5100139/202611241525/5100811",
            builtSklad
        )

        val builtSvg = GrmParser.buildGrmSvgUrl(
            template = expected,
            wagonNo = "3",
            schemaId = "1323",
            fromEpa = "5100139",
            toEpa = "5100811",
            depDt = "202611241525",
            arrDt = "202611241545"
        )
        assertEquals(
            "https://api-gateway.intercity.pl/grm/wagon/svg/wbnet/IC/1644/3/1323/202611241525/202611241545/5100139/5100811",
            builtSvg
        )

        assertEquals(0, GrmParser.computeArrivalDayOffset("23:55", "00:05", 1))
        assertEquals(1, GrmParser.computeArrivalDayOffset("00:15", "00:17", 1))
        assertEquals(0, GrmParser.computeArrivalDayOffset("15:20", "15:22", 0))
    }
}
