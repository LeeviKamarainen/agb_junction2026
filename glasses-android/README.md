# GlassesCam (Android)

Minimal Android app that streams the camera from Meta glasses via the
[Meta Wearables Device Access Toolkit](https://wearables.developer.meta.com/docs/develop/dat/build-integration-android/)
(`mwdat-core` / `mwdat-camera` 1.0.1, from Maven Central) and can capture stills and POST them to a backend.

## One-time setup

1. **Android Studio** (recent; AGP 8.11 / Kotlin 2.2 / JDK 17). Open this `glasses-android` folder and let it sync.
   The Gradle wrapper jar isn't included; Android Studio will fetch Gradle 8.13 from `gradle-wrapper.properties`.
2. `copy local.properties.example local.properties`, then set `sdk.dir` if Studio hasn't already.
   Leave `mwdat.applicationId` / `mwdat.clientToken` at `0` for Developer Mode.
   Set `backend.url` to receive captured photos (emulator-to-host is `10.0.2.2`).
3. **Glasses + phone** (physical Android 12+ phone, minSdk is 31):
   - Pair the glasses in the Meta AI app and update firmware
     (check [version dependencies](https://wearables.developer.meta.com/docs/develop/dat/version-dependencies/)).
   - Meta AI app: Settings > App Info, tap the app version 5 times, enable **Developer Mode**.
   - Only one dev-mode app is registered at a time; registering a new one unregisters the previous.

## Run

1. Run the app on the phone, accept the Bluetooth prompt.
2. **Register**: jumps to the Meta AI app to approve; returns via the `glassescam://` deep link.
   Registration state and device count show at the top.
3. **Start stream**: asks for glasses camera permission (Meta AI app sheet), opens a session,
   and the live preview appears. **Capture** grabs a still and sends it through `PhotoSink`.

## Layout

| File | Role |
|---|---|
| `MainActivity.kt` | Bluetooth permission, `Wearables.initialize`, glasses permission launcher |
| `GlassesViewModel.kt` | Registration, session, camera, stream, photo capture, teardown |
| `HevcDecoder.kt` | `MediaCodec` HEVC decoder rendering frames to the preview `Surface` |
| `GlassesScreen.kt` | Compose UI |
| `PhotoSink.kt` | Where captures go (HTTP POST of a JPEG). Replace with your backend call |

## Photos and gallery

**Capture** takes a still with `stream.capturePhoto()` (separate from the lower-res live preview),
decodes it (HEIC or Bitmap), saves a JPEG (quality 95) to the app's private storage at
`<app files>/photos/IMG_<timestamp>.jpg`, then sends it through `PhotoSink`.
**Gallery** shows a thumbnail grid; tap a photo to swipe through them full-screen, and Delete removes one.
Pull the files off the phone with Android Studio's Device Explorer (data/data/com.agb.glassescam/files/photos).

## Quick shot (single photo, no stream)

**Arm quick shot** opens a session without starting video. The Capture button on screen, the glasses'
physical **capture button** (short press), or a **Neural Band** select/tap gesture then takes one
full-resolution photo (`camera.photo.capturePhoto(FULL, HIGH)`), saves it to the gallery and sends it to
`PhotoSink`. On Meta Ray-Ban Display glasses it also shows "Ready" / "Photo saved" on the glasses screen
(best effort; other glasses have no screen, so that step is skipped).

- Needs the experimental `mwdat-inputs` module and standalone photo capture: dev/beta channels only,
  not publishable yet. Inputs may need capability approval in the Wearables Developer Center.
- **Feedback:** the phone shows a colour-coded status card (capturing -> receiving with a % bar -> saving ->
  sending -> done) and buzzes/beeps when a capture starts, finishes or fails. Display glasses show
  "Capturing..." before the shot and "Photo saved" afterwards; there is nothing in between because
  display updates are held back during the transfer. Other glasses have no screen, so the buzz/beep is the cue.
- **"Glasses screen messages" switch (off by default).** Meta lists "do not combine capture with display" as a known
  issue on Ray-Ban Display, so quick shot only attaches the display when you turn this on (before arming).
- **"The glasses took the photo but never sent it" (CAPTURE_FAILURE).** The SDK gives up after 30 s with no transfer.
  Restart the glasses (Meta also says capture can break after a firmware update until you reboot them), and don't
  disarm mid-transfer.
- **One photo at a time.** Pressing again while a photo is still being received shows "Busy" instead of queueing.
- **Older photos.** The glasses keep any photo whose transfer was cut short (e.g. you disarmed mid-transfer) and
  re-send it every time you arm. They go first, over a slow link, so a new photo waits behind them. The app shows
  "Catching up on an older photo", saves those to the gallery without sending them to the backend, and
  blocks capture until they finish. Don't disarm while a photo is in flight, or you create a new backlog.
- Double press of the capture button is ignored (the SDK sends a short press first, so there's a 400 ms delay).
- Unknown until tested on hardware: whether the glasses also take their own native photo on a button press.
- Known issue: after a firmware update to v128, reboot the glasses or camera capture can fail.
- Display updates are held back while a capture transfers, so "Photo saved" appears after the transfer.

## Caveats

- **Not compiled or run yet.** Written from Meta's docs and CameraAccess sample without an Android SDK on
  this machine. Expect small compile fixes (SDK type/method names were not all verifiable).
- Live frames are HEVC (`compressVideo = true`); the preview is decoded with `MediaCodec`. If the picture
  stays black, compare with the sample's `HevcDecoder` and `csdCollector` (it feeds codec-config data explicitly).
- To analyze frames continuously, prefer repeated `capturePhoto()` (gives a `Bitmap`/HEIC) over
  decoding the HEVC stream yourself.
- Stream runs only while the app is foregrounded; the sample uses a `connectedDevice` foreground service to keep it alive.
- Bandwidth is Bluetooth Classic: the SDK lowers resolution, then frame rate (not below 15 fps) when constrained.
- **Mock glasses** switch: pairs a fake Ray-Ban Meta via `MockDeviceKit` and feeds it the phone's back camera
  (asks for the phone CAMERA permission). Use it to develop without glasses, or when the real ones are busy.
  Turn it off before using real glasses, since mock registration replaces the real one in dev mode.
  `CameraFacing.BACK` is a guess at the enum value; adjust if it doesn't compile.
