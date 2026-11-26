package pl.siadaicie.app.ui

import android.app.Activity
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import androidx.core.view.WindowCompat

enum class ThemeMode(val label: String) {
    SYSTEM("Systemowy"),
    LIGHT("Jasny"),
    DARK("Ciemny")
}

@Immutable
data class RailColors(
    val isDark: Boolean,
    val canvas: Color,
    val card: Color,
    val cardTonal: Color,
    val hairline: Color,
    val textPrimary: Color,
    val textSecondary: Color,
    val brandNavy: Color,
    val accent: Color,
    val accentContainer: Color,
    val seatEmerald: Color,
    val seatEmeraldContainer: Color,
    val corridorAmber: Color,
    val corridorAmberContainer: Color,
    val errorRed: Color,
    val errorContainer: Color
)

private val LightRailColors = RailColors(
    isDark = false,
    canvas = Color(0xFFF6F7F9),
    card = Color(0xFFFFFFFF),
    cardTonal = Color(0xFFECEFF4),
    hairline = Color(0xFFDDE2EB),
    textPrimary = Color(0xFF141821),
    textSecondary = Color(0xFF5D6679),
    brandNavy = Color(0xFF1B3A6B),
    accent = Color(0xFFD95D24),
    accentContainer = Color(0xFFFCE8DF),
    seatEmerald = Color(0xFF0F8A5F),
    seatEmeraldContainer = Color(0xFFDFF4EB),
    corridorAmber = Color(0xFFB45309),
    corridorAmberContainer = Color(0xFFFEF3C7),
    errorRed = Color(0xFFBA1A1A),
    errorContainer = Color(0xFFFFDAD6)
)

private val DarkRailColors = RailColors(
    isDark = true,
    canvas = Color(0xFF111318),
    card = Color(0xFF1A1D24),
    cardTonal = Color(0xFF232730),
    hairline = Color(0xFF2E3340),
    textPrimary = Color(0xFFE7EAF0),
    textSecondary = Color(0xFF9BA3B4),
    brandNavy = Color(0xFF7BB0FF),
    accent = Color(0xFFF28B50),
    accentContainer = Color(0xFF3A2419),
    seatEmerald = Color(0xFF4ADE80),
    seatEmeraldContainer = Color(0xFF143123),
    corridorAmber = Color(0xFFFBBF24),
    corridorAmberContainer = Color(0xFF332611),
    errorRed = Color(0xFFFFB4AB),
    errorContainer = Color(0xFF4A1D1B)
)

val LocalRailColors = staticCompositionLocalOf { LightRailColors }

private val LightScheme = lightColorScheme(
    primary = Color(0xFF1B3A6B),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFDCE6F7),
    onPrimaryContainer = Color(0xFF0F2447),
    secondary = Color(0xFFD95D24),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFFCE8DF),
    onSecondaryContainer = Color(0xFF5B2209),
    tertiary = Color(0xFF0F8A5F),
    onTertiary = Color.White,
    background = Color(0xFFF6F7F9),
    onBackground = Color(0xFF141821),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF141821),
    surfaceVariant = Color(0xFFECEFF4),
    onSurfaceVariant = Color(0xFF5D6679),
    outline = Color(0xFFDDE2EB),
    error = Color(0xFFBA1A1A),
    onError = Color.White
)

private val DarkScheme = darkColorScheme(
    primary = Color(0xFF7BB0FF),
    onPrimary = Color(0xFF0B2447),
    primaryContainer = Color(0xFF1E3252),
    onPrimaryContainer = Color(0xFFDCE8FF),
    secondary = Color(0xFFF28B50),
    onSecondary = Color(0xFF331404),
    secondaryContainer = Color(0xFF3A2419),
    onSecondaryContainer = Color(0xFFFFDCC7),
    tertiary = Color(0xFF4ADE80),
    onTertiary = Color(0xFF072B17),
    background = Color(0xFF111318),
    onBackground = Color(0xFFE7EAF0),
    surface = Color(0xFF1A1D24),
    onSurface = Color(0xFFE7EAF0),
    surfaceVariant = Color(0xFF232730),
    onSurfaceVariant = Color(0xFF9BA3B4),
    outline = Color(0xFF2E3340),
    error = Color(0xFFFFB4AB),
    onError = Color(0xFF4A1D1B)
)

private val AppTypography = Typography(
    headlineSmall = TextStyle(fontSize = 22.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.3).sp),
    titleMedium = TextStyle(fontSize = 16.sp, fontWeight = FontWeight.SemiBold),
    titleSmall = TextStyle(fontSize = 14.sp, fontWeight = FontWeight.SemiBold),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 20.sp),
    bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 16.sp),
    labelMedium = TextStyle(fontSize = 12.sp, fontWeight = FontWeight.Medium)
)

@Composable
fun SiadaICieTheme(
    themeMode: ThemeMode = ThemeMode.SYSTEM,
    content: @Composable () -> Unit
) {
    val systemDark = isSystemInDarkTheme()
    val useDark = when (themeMode) {
        ThemeMode.SYSTEM -> systemDark
        ThemeMode.DARK -> true
        ThemeMode.LIGHT -> false
    }
    val railColors = if (useDark) DarkRailColors else LightRailColors
    val colorScheme = if (useDark) DarkScheme else LightScheme

    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as? Activity)?.window
            if (window != null) {
                val controller = WindowCompat.getInsetsController(window, view)
                controller.isAppearanceLightStatusBars = !useDark
                controller.isAppearanceLightNavigationBars = !useDark
            }
        }
    }

    CompositionLocalProvider(LocalRailColors provides railColors) {
        MaterialTheme(
            colorScheme = colorScheme,
            typography = AppTypography,
            content = content
        )
    }
}
