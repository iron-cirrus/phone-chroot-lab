#!/bin/bash
set -e
: "${KS_PASS:?definir KS_PASS (mot de passe du keystore, a choisir et ne jamais publier)}"
B=/root/apk-build; W=$B/webui; SDK=/usr/lib/android-sdk; AJ=$SDK/platforms/android-23/android.jar
cd $W; rm -rf out; mkdir -p out/classes out/dex res/mipmap-xxhdpi
convert -size 144x144 xc:none -fill "#171c24" -stroke "#58a6ff" -strokewidth 4 -draw "roundrectangle 4,4 140,140 30,30" -stroke none -fill "#e6edf3" -draw "roundrectangle 26,34 118,92 14,14" -draw "polygon 44,90 44,116 70,90" -fill "#171c24" -draw "circle 52,63 52,69" -draw "circle 72,63 72,69" -draw "circle 92,63 92,69" res/mipmap-xxhdpi/ic_launcher.png
javac --release 8 -encoding UTF-8 -Xlint:-options -cp $AJ -d out/classes $(find src -name "*.java")
java -cp $B/r8.jar com.android.tools.r8.D8 --release --min-api 23 --lib $AJ --output out/dex $(find out/classes -name '*.class')
aapt package -f -M AndroidManifest.xml -S res -I $AJ -F out/unsigned.apk
(cd out/dex && aapt add -f $W/out/unsigned.apk classes.dex >/dev/null)
zipalign -f -p 4 out/unsigned.apk out/aligned.apk
apksigner sign --ks $B/pilote.jks --ks-pass "pass:$KS_PASS" --key-pass "pass:$KS_PASS" --out $B/webui.apk out/aligned.apk
apksigner verify $B/webui.apk && ls -la $B/webui.apk
