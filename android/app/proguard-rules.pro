-keepattributes *Annotation*,Signature,InnerClasses,EnclosingMethod
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}
-keep class pl.siadaicie.app.scraper.EbiletWebViewScanner$GrmJsBridge {
    *;
}
-dontwarn org.jsoup.**
