# Radio Remote PTT for Android

Reads the PTT button of a paired Bluetooth speaker-mic or PTT button that sends it over the Bluetooth Serial Port Profile
(`+PTT=P` pressed, `+PTT=R` released) and hands it to the Radio Remote page in Chrome on the same phone over `ws://127.0.0.1:17991`.

**Use it:** download `radio-remote-ptt-android.apk` from the latest release and install it. Pair the device in Android's
Bluetooth settings, open the app, allow Bluetooth and notifications, choose the device and press Start. It starts again after the
phone restarts. In Radio Remote press **Bind PTT** and then the device's button.

**Build it yourself:** JDK 17 and the Android SDK (platform 34); `./gradlew assembleRelease`. A release is signed with the key named in
`~/.radio-remote-signing/ptt.properties` (storeFile, storePassword, keyAlias, keyPassword); without it the APK is unsigned.
