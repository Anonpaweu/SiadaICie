package pl.siadaicie.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import pl.siadaicie.app.domain.SeatFilterPrefs
import pl.siadaicie.app.scraper.GrmParser

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

    @Test
    fun parseGrmWagonSvgWithMeta_extractsSpecialAndComfortTagsAndFilters() {
        val svg = """
            <svg xmlns:eic="http://www.intercity.pl/eic" viewBox="0 0 100 100">
                <g aria-label="Miejsce 31 klasa 2, Wolne, niewybrane" data-class="second class">
                    <eic:special ref="1" pl="Miejsce dla osoby z rowerem" carriagecode="24" iconcode="S1">Miejsce dla osoby z rowerem</eic:special>
                    <text>31</text>
                </g>
                <g aria-label="Miejsce 32 klasa 2, okno, strefa ciszy, Wolne" data-class="second class"><text>32</text></g>
                <g aria-label="Miejsce 33 klasa 2, miejsce dla kobiety w ciąży, Wolne"><text>33</text></g>
                <g aria-label="Miejsce 34 klasa 2, miejsce dla osoby z dzieckiem, Wolne"><text>34</text></g>
                <g aria-label="Miejsce 35 klasa 2, miejsce dla seniora, Wolne"><text>35</text></g>
                <g aria-label="Miejsce 36 klasa 2, miejsce dla osoby o ograniczonej sprawności, Wolne"><text>36</text></g>
                <g aria-label="Miejsce 37 klasa 2, dla osoby na wózku inwalidzkim, Wolne"><text>37</text></g>
            </svg>
        """.trimIndent()

        val (allSeats, tagsMap) = GrmParser.parseGrmWagonSvgWithMeta(svg, ticketClass = 2, includeWheelchair = true)
        assertEquals(listOf("31", "32", "33", "34", "35", "36", "37"), allSeats)
        assertEquals(listOf(GrmParser.TAG_BIKE), tagsMap["31"])
        assertEquals(listOf(GrmParser.TAG_QUIET, GrmParser.TAG_WINDOW), tagsMap["32"])
        assertEquals(listOf(GrmParser.TAG_PREGNANT), tagsMap["33"])
        assertEquals(listOf(GrmParser.TAG_CHILD), tagsMap["34"])
        assertEquals(listOf(GrmParser.TAG_ELDERLY), tagsMap["35"])
        assertEquals(listOf(GrmParser.TAG_PRM_ASSIST), tagsMap["36"])
        assertEquals(listOf(GrmParser.TAG_WHEELCHAIR), tagsMap["37"])

        // Default filters allow bike + normal/quiet, disallow pregnant/child/elderly/prm_assist/wheelchair
        val defaultPrefs = SeatFilterPrefs()
        assertTrue(GrmParser.isSeatAllowed(tagsMap["31"].orEmpty(), defaultPrefs))
        assertTrue(GrmParser.isSeatAllowed(tagsMap["32"].orEmpty(), defaultPrefs))
        assertFalse(GrmParser.isSeatAllowed(tagsMap["33"].orEmpty(), defaultPrefs))
        assertFalse(GrmParser.isSeatAllowed(tagsMap["34"].orEmpty(), defaultPrefs))
        assertFalse(GrmParser.isSeatAllowed(tagsMap["35"].orEmpty(), defaultPrefs))
        assertFalse(GrmParser.isSeatAllowed(tagsMap["36"].orEmpty(), defaultPrefs))
        assertFalse(GrmParser.isSeatAllowed(tagsMap["37"].orEmpty(), defaultPrefs))

        // Priority hierarchy: normal/quiet (0) < bike (1) < pregnant/child/elderly/prm (2) < wheelchair (3)
        assertEquals(0, GrmParser.seatPriorityTier(tagsMap["32"].orEmpty()))
        assertEquals(1, GrmParser.seatPriorityTier(tagsMap["31"].orEmpty()))
        assertEquals(2, GrmParser.seatPriorityTier(tagsMap["33"].orEmpty()))
        assertEquals(2, GrmParser.seatPriorityTier(tagsMap["36"].orEmpty()))
        assertEquals(3, GrmParser.seatPriorityTier(tagsMap["37"].orEmpty()))
    }
}
