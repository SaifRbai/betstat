#!/usr/bin/env bash
# Construit android/build/RAB3OUNSTAT.apk sans Gradle : aapt2, javac, d8,
# zipalign, apksigner du SDK Android suffisent pour une seule activite.
#
#   ./android/construire.sh
#
# La cle de signature est creee au premier passage dans ~/.android/ et
# reutilisee ensuite : Android refuse une mise a jour signee par une autre cle.
set -euo pipefail

SDK="${ANDROID_HOME:-$HOME/Android/Sdk}"
OUTILS="$SDK/build-tools/35.0.0"
PLATEFORME="$SDK/platforms/android-35/android.jar"
CLE="$HOME/.android/foot_edge.jks"
ICI="$(cd "$(dirname "$0")" && pwd)"
SORTIE="$ICI/build"

rm -rf "$SORTIE" && mkdir -p "$SORTIE"/{res,classes,dex}

"$OUTILS/aapt2" compile --dir "$ICI/res" -o "$SORTIE/res/res.zip"
"$OUTILS/aapt2" link "$SORTIE/res/res.zip" -I "$PLATEFORME" \
    --manifest "$ICI/AndroidManifest.xml" --java "$SORTIE/gen" \
    -o "$SORTIE/brut.apk"

javac --release 11 -classpath "$PLATEFORME" -d "$SORTIE/classes" \
    $(find "$ICI/src" "$SORTIE/gen" -name '*.java')
"$OUTILS/d8" --min-api 26 --lib "$PLATEFORME" --output "$SORTIE/dex" \
    $(find "$SORTIE/classes" -name '*.class')
(cd "$SORTIE/dex" && zip -qj "$SORTIE/brut.apk" classes.dex)

if [ ! -f "$CLE" ]; then
    mkdir -p "$(dirname "$CLE")"
    keytool -genkeypair -keystore "$CLE" -alias foot_edge -keyalg RSA \
        -keysize 2048 -validity 10000 -storepass foot_edge -keypass foot_edge \
        -dname "CN=RAB3OUNSTAT" >/dev/null
fi
"$OUTILS/zipalign" -f 4 "$SORTIE/brut.apk" "$SORTIE/aligne.apk"
"$OUTILS/apksigner" sign --ks "$CLE" --ks-pass pass:foot_edge \
    --out "$SORTIE/RAB3OUNSTAT.apk" "$SORTIE/aligne.apk"
rm -f "$SORTIE"/{brut,aligne}.apk "$SORTIE/RAB3OUNSTAT.apk.idsig"

echo "APK : $SORTIE/RAB3OUNSTAT.apk"
