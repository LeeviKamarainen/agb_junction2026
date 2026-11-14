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

## Live segment (object placement on the glasses)

**Arm live segment** keeps the video stream open (504x896, 7 fps) and every N seconds (default 1.00 s, change
with the **-/+** buttons in 0.25 s steps, 0.25 to 10 s) grabs the next decoded frame, sends it to the segmentation
API at the calibrated eye point, and draws a 600x600 picture on the Ray-Ban Display screen: the cut-out of the
object found there, its green box / yellow rotated box, and a cyan crosshair at the eye point. Move the object (or
your head) until the cut-out sits on the crosshair. The phone shows the same picture under the buttons. It needs a
segmentation token (see above), the glasses' display, and a calibrated eye point (otherwise the photo centre).
Requests run one at a time; if a cycle takes longer than the interval the next one starts right after it.

While live mode runs the phone shows a large view of the same picture over the dimmed camera frame, the last
request's time, and:
- **Move crosshair to object**: line the camera up on the object, wait for the green box, then press it. The
  crosshair (= the calibrated eye point) jumps to the centre of the object's box and is saved, so it is also the
  segmentation point from then on. The glasses capture button or a Neural Band tap does the same without touching
  the phone (the phone's Capture button too).
- **Full camera feed on phone** (on by default) / **on glasses too** (off by default): with the feed on, the whole
  camera frame is drawn dimmed, the segmented object stays at full brightness with a green tint, plus the boxes and
  crosshair. With it off you only get the object's cut-out on black. On the glasses the feed lights up the whole
  display (it's see-through), so it is off by default. The frame updates once per cycle, not as smooth video.
- **Show crosshair** switch: hides/shows the crosshair on the glasses and the phone view. The segmentation point
  stays where it was calibrated either way.

Unverified / not documented by Meta: how fast the display can be updated, what happens if updates arrive faster
than it can take them, whether the video stream and display share Bluetooth without lag, and whether black shows as
see-through on the glasses (the picture uses a black background). If the display lags, raise the interval.
The Capture button and glasses button do nothing while live mode is running.

## Eye-point calibration

The camera is on the frame, so the middle of the photo is not what the wearer looks straight at. Calibrate once:
look at a target about 1-2 m away, take a photo, open it in the gallery, switch **Tap = set eye point** and tap
where the target is. A cyan crosshair marks the stored point (as a fraction of the photo, in app preferences).
From then on, new captures are segmented at that point instead of the photo centre, and **Eye crop** saves a copy
(`..._eye.jpg`) cropped so the eye point is exactly the middle (the largest window centred on it that fits
inside the photo, so a point far from the middle cuts more of the edge). **Reset eye point** goes back to the
centre. This is a fixed 2D offset: accurate near the calibration distance, drifting for much closer or
farther objects (parallax). The SDK has no eye tracking or camera-to-lens calibration data.

## Segmentation API

Each captured photo (quick shot or snapshot) is also sent to the SAM 3 segmentation API
(`POST <segment.url>`, multipart: `image`, `x`, `y`, header `Authorization: Bearer <token>`), with the point at
the image centre. The answer (`box`, `rotated_box`, `score`, `mask_png`) is saved next to the photo as
`<photo>.segment.json`. In the gallery, open a photo and **tap an object** to segment that point instead: the mask
(red), box (green) and rotated box (yellow) are drawn over it. Errors (401 bad token, 415 not an image, 422
point outside the image / no object / bad form field) are shown as text.

**3x3 grid toggle.** The switch "Segment with 3x3 point grid" (remembered across app starts) sends every request to
`<segment.url>-grid` (override with `segment.gridUrl` in `local.properties`) with the extra form fields `grid=3` and
`spacing=0.04`, so a 3x3 block of points around the chosen point is used instead of a single pixel. It applies to
live mode, the automatic call after each capture and tap-to-segment in the gallery. The response is assumed to
have the same shape as `/segment` (`box`, `rotated_box`, `score`, `mask_png`); the grid endpoint's own spec
wasn't provided, so check that if results look odd. Status lines show "(3x3 grid)" while it is on.

Setup: put `segment.token` (from `make gpu-token`) and `segment.url` in `local.properties`
(see `local.properties.example`). The token is compiled into the APK, so don't share builds publicly.

## Snapshot (fast, from the video stream)

**Arm snapshot (fast)** keeps the glasses' compressed video stream open (720x1280, 7 fps) and decodes it in the
background with `MediaCodec` (`SnapshotDecoder.kt`, no on-screen preview). The capture button, a Neural Band
gesture or the Capture button saves the NEXT decoded frame as a JPEG (typically well under a second, tens of KB over
the link instead of ~1.4 MB for a full photo). Nothing is stored on the glasses, so there is no backlog of
older photos. Trade-offs: max 720x1280 (stream frames are compressed, so softer than a real still), and the stream
uses battery and Bluetooth bandwidth while armed. Use **Arm quick shot** when you need a full-resolution still.
Unverified: the first frames after arming can look blocky until the stream's first keyframe arrives.

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
