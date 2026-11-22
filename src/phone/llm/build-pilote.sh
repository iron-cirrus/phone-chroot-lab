#!/bin/bash
# Compile l'app Pilote (ecj + d8 + aapt + apksigner) dans /root/apk-build
set -e
: "${KS_PASS:?definir KS_PASS (mot de passe du keystore, a choisir et ne jamais publier)}"
B=/root/apk-build; SDK=/usr/lib/android-sdk; AJ=$SDK/platforms/android-23/android.jar; BT=$SDK/build-tools/29.0.3
cd $B/app
rm -rf $B/out; mkdir -p $B/out/classes $B/out/dex
# icone
mkdir -p res/mipmap-xxhdpi
convert -size 144x144 xc:none -fill "#171c24" -stroke "#58a6ff" -strokewidth 4 -draw "roundrectangle 4,4 140,140 30,30" -stroke none -fill "#56d364" -draw "roundrectangle 26,66 42,116 5,5" -fill "#f0883e" -draw "roundrectangle 52,38 68,116 5,5" -fill "#58a6ff" -draw "roundrectangle 78,16 94,116 5,5" -fill "#a371f7" -draw "roundrectangle 104,50 120,116 5,5" res/mipmap-xxhdpi/ic_launcher.png
javac --release 8 -encoding UTF-8 -Xlint:-options -cp $AJ -d $B/out/classes $(find src -name "*.java")
java -cp $B/r8.jar com.android.tools.r8.D8 --release --min-api 23 --lib $AJ --output $B/out/dex $(find $B/out/classes -name '*.class')
aapt package -f -M AndroidManifest.xml -S res -I $AJ -F $B/out/unsigned.apk
(cd $B/out/dex && aapt add -f $B/out/unsigned.apk classes.dex >/dev/null)
zipalign -f -p 4 $B/out/unsigned.apk $B/out/aligned.apk
[ -f $B/pilote.jks ] || keytool -genkeypair -keystore $B/pilote.jks -storepass "$KS_PASS" -keypass "$KS_PASS" -alias pilote -keyalg RSA -keysize 2048 -validity 9000 -dname "CN=Pilote" >/dev/null 2>&1
apksigner sign --ks $B/pilote.jks --ks-pass "pass:$KS_PASS" --key-pass "pass:$KS_PASS" --out $B/pilote.apk $B/out/aligned.apk
apksigner verify $B/pilote.apk && ls -la $B/pilote.apk
