package pl.iccbagno.app.data

import android.content.Context
import android.database.sqlite.SQLiteDatabase
import pl.iccbagno.app.domain.RouteHelpers
import pl.iccbagno.app.domain.StationInfo
import java.io.File

class StationsRepository(private val context: Context) {
    private val dbFile: File
        get() = context.getDatabasePath("stations.db")

    @Synchronized
    private fun ensureDbCopied(): SQLiteDatabase {
        val target = dbFile
        if (!target.exists() || target.length() < 10_000L) {
            target.parentFile?.mkdirs()
            context.assets.open("stations.db").use { input ->
                target.outputStream().use { output ->
                    input.copyTo(output)
                }
            }
        }
        return SQLiteDatabase.openDatabase(target.absolutePath, null, SQLiteDatabase.OPEN_READONLY)
    }

    fun searchStations(query: String, limit: Int = 20): List<StationInfo> {
        val normQ = RouteHelpers.normalizePolishName(query)
        if (normQ.isEmpty()) return emptyList()
        val prefix = "$normQ%"
        val infix = "%$normQ%"
        val db = ensureDbCopied()
        val results = ArrayList<StationInfo>()
        db.use { conn ->
            val sql = """
                SELECT eva_code, name, normalized_name, metropolitan_group, is_main_hub, ic_id
                FROM stations
                WHERE normalized_name = ? OR normalized_name LIKE ? OR normalized_name LIKE ?
                ORDER BY
                    CASE
                        WHEN normalized_name = ? THEN 0
                        WHEN normalized_name LIKE ? THEN 1
                        ELSE 2
                    END ASC,
                    is_main_hub DESC,
                    length(name) ASC,
                    name ASC
                LIMIT ?
            """.trimIndent()
            conn.rawQuery(sql, arrayOf(normQ, prefix, infix, normQ, prefix, limit.toString())).use { c ->
                while (c.moveToNext()) {
                    results.add(
                        StationInfo(
                            evaCode = c.getString(0),
                            name = c.getString(1),
                            normalizedName = c.getString(2),
                            metropolitanGroup = if (c.isNull(3)) null else c.getString(3),
                            isMainHub = c.getInt(4) == 1,
                            icId = if (c.isNull(5)) null else c.getString(5)
                        )
                    )
                }
            }
        }
        return results
    }

    fun getStationInfo(stationName: String): Pair<String, String> {
        val raw = stationName.trim()
        val norm = RouteHelpers.normalizePolishName(raw)
        if (norm.isEmpty()) return stationName to ""
        val db = ensureDbCopied()
        db.use { conn ->
            val exactSql = """
                SELECT name, eva_code FROM stations
                WHERE name = ? OR normalized_name = ?
                ORDER BY is_main_hub DESC, length(name) ASC
                LIMIT 1
            """.trimIndent()
            conn.rawQuery(exactSql, arrayOf(raw, norm)).use { c ->
                if (c.moveToFirst()) return c.getString(0) to c.getString(1)
            }

            val prefixSql = """
                SELECT name, eva_code FROM stations
                WHERE normalized_name LIKE ?
                ORDER BY is_main_hub DESC, length(name) ASC
                LIMIT 1
            """.trimIndent()
            conn.rawQuery(prefixSql, arrayOf("$norm%")).use { c ->
                if (c.moveToFirst()) return c.getString(0) to c.getString(1)
            }

            val infixSql = """
                SELECT name, eva_code FROM stations
                WHERE normalized_name LIKE ?
                ORDER BY is_main_hub DESC, length(name) ASC
                LIMIT 1
            """.trimIndent()
            conn.rawQuery(infixSql, arrayOf("%$norm%")).use { c ->
                if (c.moveToFirst()) return c.getString(0) to c.getString(1)
            }
        }
        return stationName to ""
    }
}
