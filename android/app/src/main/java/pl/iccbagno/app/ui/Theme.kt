package pl.iccbagno.app.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

val PkpDarkBg = Color(0xFF0B1221)
val PkpSurface = Color(0xFF131D35)
val PkpSurfaceAlt = Color(0xFF1B2845)
val PkpBorder = Color(0xFF28395F)
val PkpOrange = Color(0xFFF97316)
val PkpOrangeHover = Color(0xFFEA580C)
val PkpEmerald = Color(0xFF10B981)
val PkpTextPrimary = Color(0xFFF1F5F9)
val PkpTextSecondary = Color(0xFF94A3B8)
val PkpRed = Color(0xFFEF4444)

private val PkpColorScheme = darkColorScheme(
    primary = PkpOrange,
    onPrimary = Color.White,
    secondary = PkpEmerald,
    onSecondary = Color.White,
    background = PkpDarkBg,
    onBackground = PkpTextPrimary,
    surface = PkpSurface,
    onSurface = PkpTextPrimary,
    surfaceVariant = PkpSurfaceAlt,
    onSurfaceVariant = PkpTextSecondary,
    outline = PkpBorder,
    error = PkpRed
)

@Composable
fun IccBagnoTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = PkpColorScheme,
        content = content
    )
}
