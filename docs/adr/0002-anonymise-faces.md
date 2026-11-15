# Faces are blurred as images arrive

Images from the glasses show bystanders, who have not agreed to be stored. So every image has its faces blurred the moment it arrives, before anything stores it, embeds it or shows it to a model:

- in the backend, wherever an image arrives: the frames of the glasses' memory mode and the Memory lab's Stream tab (`POST /api/glasses/frames` and the WebSocket's `memory_frame`), filled in `glasses/actions.py`, which the route and the WebSocket share, Memory lab uploads (`POST /api/memories/image`), the admin's stream wall, which only ever holds a frame after that blur (`glasses/feeds.py`), and the frames of published demo clips stored as demo memories (`app/demo.py`; demo images are blurred on upload too, videos are kept as uploaded);
- in the memory API on VM2: `POST /batches`, the video command line (`embed.keyframes`) and the images `/v1/embeddings` embeds.

All of them call `faces.anonymise`, one file kept identical in `backend/app/faces.py` and `memory/src/glass_memory/faces.py` (a backend test compares them): the two deploy separately, and a shared package would mean changing both deploy paths. It fails closed: an image it cannot read is refused, and neither app starts without the detector's model. Images stored before this change are kept as they were. The memory API's `/v1/chat/completions` still passes images to Gemma as they came: it keeps nothing, and it is a proxy other services share.

## Where

Not on the phone: the face detectors phones run are made for faces close to the camera. On Meta's own smart-glasses footage MediaPipe's average recall was 0.53, against 0.94 for EgoBlur and 0.91 for RetinaFace ([EgoBlur paper](https://arxiv.org/abs/2308.13093), Table VII), and ML Kit asks for faces 100 px tall. The web harness and the Memory lab would each need their own copy, and the server could not trust them anyway. Not only on the GPU server: every frame would then make a second trip there, and saving a photo would depend on it being up.

## A strong blur, after a fill

Blurred faces can be restored and recognised: [Revelio](https://arxiv.org/abs/2506.12344) restored faces blurred with an 81 px kernel and re-identified 95.9% of them against a database of known faces (0% without restoring). Pixelated faces are recognised too: 57% at 16 px blocks, against 0.19% by chance ([McPherson et al.](https://arxiv.org/abs/1609.00408)). So faces were first filled with one flat grey, which leaves nothing to restore. In use the grey blocks hid too much of the scenes, so each face is now blurred instead, accepting that risk: a Gaussian blur whose standard deviation is a fifth of the face's height, so the blur is as strong at every distance and eyes, nose and mouth merge into one smooth patch. No study we found says how strong a blur is strong enough. Each box is enlarged 1.3 times first, the default of the [deface](https://github.com/ORB-HD/deface) tool, so hair, ears and chin go too.

## Detector and settings

YuNet, OpenCV's face detector from [opencv_zoo](https://github.com/opencv/opencv_zoo) (MIT licence, 233 KB), runs on the CPU with OpenCV 5.0, which found the same faces as 4.14 one and a half times faster. Measured with `backend/scripts/face_recall.py` on 3,226 WIDER FACE validation photos (39,112 labelled faces) and on 313 COCO photos of rooms and objects with no people, all resized to the glasses' 896 px long side and saved at JPEG quality 50 like the video stream. A face counts as filled when the middle of its box is inside a fill.

The table gives the share of faces filled, by their height in the frame, and the fills per photo with no people; a few of those are faces on screens or in the background that the photos' captions did not mention. Times are for the search alone in one frame, on two threads of a desktop CPU (Ryzen 7 5800X), not the production VM; decoding, filling and encoding add about 3 ms.

| Upscale, minimum score | 10–16 px | 16–24 px | 24–32 px | 32–64 px | 64+ px | 16+ px | Fills, no people | ms |
|---|---|---|---|---|---|---|---|---|
| 1×, 0.3 | 75.7% | 86.3% | 90.2% | 93.1% | 96.5% | 91.2% | 0.93 | 13 |
| 1×, 0.4 | 67.7% | 81.4% | 86.7% | 90.8% | 95.5% | 88.2% | 0.49 | 13 |
| **1×, 0.5** | 58.5% | 76.7% | 83.0% | 88.3% | 94.3% | 85.0% | 0.25 | 13 |
| 1×, 0.6 | 48.1% | 70.2% | 78.6% | 86.2% | 92.5% | 81.3% | 0.15 | 13 |
| 1×, 0.9 (OpenCV's usual) | 0.0% | 2.3% | 15.3% | 39.4% | 62.7% | 28.5% | 0.00 | 13 |
| 1.5×, 0.5 | 69.6% | 81.0% | 85.9% | 89.6% | 94.1% | 87.3% | 0.46 | 37 |
| 2×, 0.5 | 72.3% | 82.1% | 85.3% | 89.4% | 93.5% | 87.3% | 0.60 | 67 |

Chosen at first: each image at its own size, with a minimum score of 0.5. Upscaling helped only faces under 32 px, and a lower minimum score gained as much for the same or fewer false fills, in a third to a fifth of the time. Between 0.4 and 0.5: the fills that 0.4 adds include several big enough to hide a bowl, a floor or a toilet, in the kind of photo the memory is for. Larger images are shrunk to a long side of 1792 px first: the search in a 12 MP photo then takes 0.11 s instead of 0.44 s and over 1 GB of memory.

Then made gentler, because the fills hid too much of the scenes and a few more missed faces are acceptable: a minimum score of 0.6, and no fill for a "face" taller than a third of the image's long side (a real one that big is within roughly 30 cm of the glasses). Measured with `backend/scripts/face_fill_area.py`, counting a face as covered when three quarters of its box is filled, and the share of each photo with no people that fills hide:

| Minimum score, tallest face filled | Faces 16+ px covered | 32+ px covered | Hidden, no people | Fills per photo, no people |
|---|---|---|---|---|
| 0.5, any size (before) | 80.8% | 87.3% | 0.72% | 0.25 |
| 0.5, a third of the image | 79.7% | 85.2% | 0.29% | 0.24 |
| **0.6, a third of the image** | 76.7% | 83.6% | 0.15% | 0.13 |
| 0.7, a third of the image | 72.1% | 81.1% | 0.09% | 0.07 |

Fills smaller than 1.3 times the face's box hide less but cover far fewer faces: 74% at 1.15 times and 41% at 1.0, at a minimum score of 0.5.

## What it costs and misses

- With the gentler settings, 23% of the faces 16 px or taller in WIDER's photos are not three quarters covered (19% at 0.5 with no cap). More of them are small, turned away or covered, and a face within about 30 cm is left on purpose. WIDER is a hard set (crowds, odd poses, blur), and frames from the glasses themselves have not been measured. If this is not enough, a stronger detector (RetinaFace, or EgoBlur on the GPU server) fits behind the same `detect`.
- False fills: 0.13 per photo with no one in it, hiding 0.15% of those photos on average, and none over a quarter of the frame. Before the cap, 2 of the 313 photos got a fill that big, and 5 had a "face" box over a quarter of the frame, on a soup bowl, a plate, a phone, a doughnut and a teddy bear's face.
- Images stored before this change keep their faces, and still reach Gemma when a question retrieves them.
- Uploads over 1 MB (phone photos, not the glasses' frames of about 100 KB) are spooled by the web framework to an unnamed temporary file until the request ends, before they are filled.
- Questions about people ("who did I talk to?") cannot be answered from the stored images.
- OpenCV and the NumPy it needs add about 240 MB to the backend's Docker image (its packages grew from 119 to 362 MB).
