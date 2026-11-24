-keepattributes *Annotation*,Signature,InnerClasses,EnclosingMethod
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}
-keepclassmembers class pl.iccbagno.app.scraper.GrmJsBridge {
    <methods>;
}
-dontwarn org.jsoup.**
