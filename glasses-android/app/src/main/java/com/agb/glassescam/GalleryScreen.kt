package com.agb.glassescam

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.ui.platform.LocalContext
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.ColorMatrix
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import java.io.File

/** Photo gallery: thumbnail grid; tap one to swipe left/right through full-size photos. */
@Composable
fun GalleryScreen(
    photos: List<File>,
    onClose: () -> Unit,
    onDelete: (File) -> Unit,
    onRefresh: () -> Unit,
) {
    // null = grid, otherwise the index being viewed
    var openIndex by rememberSaveable { mutableStateOf<Int?>(null) }

    BackHandler {
        if (openIndex != null) openIndex = null else onClose()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.White)
            .statusBarsPadding(),
    ) {
        val index = openIndex
        if (index == null || photos.isEmpty()) {
            GalleryGrid(photos, onClose = onClose, onOpen = { openIndex = it })
        } else {
            GalleryPager(
                photos = photos,
                startIndex = index.coerceIn(0, photos.lastIndex),
                onBack = { openIndex = null },
                onDelete = onDelete,
                onRefresh = onRefresh,
            )
        }
    }
}

@Composable
private fun GalleryGrid(photos: List<File>, onClose: () -> Unit, onOpen: (Int) -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var note by remember { mutableStateOf("") }
    Row(
        modifier = Modifier.fillMaxWidth().padding(16.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("Gallery (${photos.size})")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            // Copies every photo (and its segmentation JSON) to Pictures/GlassesCam and Download/GlassesCam.
            OutlinedButton(
                enabled = photos.isNotEmpty(),
                onClick = {
                    note = "Exporting ${photos.size} photos..."
                    scope.launch {
                        val n = PhotoStore.exportToPhone(ctx, photos)
                        note = "Exported $n photos to Pictures/GlassesCam (segmentation files to Download/GlassesCam)."
                    }
                },
            ) { Text("Export all") }
            OutlinedButton(onClick = onClose) { Text("Close") }
        }
    }
    if (note.isNotBlank()) Text(note, modifier = Modifier.padding(horizontal = 16.dp))
    if (photos.isEmpty()) {
        Text("No photos yet. Start the stream and tap Capture.", modifier = Modifier.padding(16.dp))
        return
    }
    LazyVerticalGrid(
        columns = GridCells.Adaptive(110.dp),
        modifier = Modifier.fillMaxSize().padding(horizontal = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        items(photos.size, key = { photos[it].name }) { i ->
            PhotoImage(
                file = photos[i],
                maxSide = 400,
                contentScale = ContentScale.Crop,
                modifier = Modifier.aspectRatio(1f).clickable { onOpen(i) },
            )
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun GalleryPager(
    photos: List<File>,
    startIndex: Int,
    onBack: () -> Unit,
    onDelete: (File) -> Unit,
    onRefresh: () -> Unit,
) {
    val pagerState = rememberPagerState(initialPage = startIndex) { photos.size }
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    // Calibrate mode: a tap sets the eye point instead of segmenting.
    var calibrate by remember { mutableStateOf(false) }
    var eye by remember { mutableStateOf(EyeCalibration.get(ctx)) }
    var note by remember { mutableStateOf("") }

    Row(
        modifier = Modifier.fillMaxWidth().padding(16.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        OutlinedButton(onClick = onBack) { Text("Grid") }
        Text("${pagerState.currentPage + 1} / ${photos.size}")
        Button(onClick = { onDelete(photos[pagerState.currentPage.coerceIn(0, photos.lastIndex)]) }) {
            Text("Delete")
        }
    }
    FlowRow(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        OutlinedButton(onClick = { calibrate = !calibrate }) {
            Text(if (calibrate) "Tap = set eye point" else "Tap = segment")
        }
        OutlinedButton(onClick = {
            scope.launch {
                val page = pagerState.currentPage.coerceIn(0, photos.lastIndex)
                val out = PhotoStore.saveEyeCrop(photos[page], eye.first, eye.second)
                note = if (out != null) "Saved eye-centred crop ${out.name}" else "Eye point too close to an edge to crop"
                onRefresh()
            }
        }) { Text("Eye crop") }
        OutlinedButton(onClick = {
            scope.launch {
                val page = pagerState.currentPage.coerceIn(0, photos.lastIndex)
                val n = PhotoStore.exportToPhone(ctx, listOf(photos[page]))
                note = if (n > 0) "Exported ${photos[page].name} to Pictures/GlassesCam" else "Export failed"
            }
        }) { Text("Export this") }
        OutlinedButton(onClick = {
            EyeCalibration.reset(ctx)
            eye = EyeCalibration.get(ctx)
            note = "Eye point reset to the photo centre"
        }) { Text("Reset eye point") }
    }
    if (note.isNotBlank()) Text(note, modifier = Modifier.padding(horizontal = 16.dp))

    HorizontalPager(
        state = pagerState,
        modifier = Modifier.fillMaxSize(),
        key = { photos[it].name },
    ) { page ->
        SegmentablePhoto(
            file = photos[page],
            eye = eye,
            calibrate = calibrate,
            onEyeSet = { fx, fy ->
                EyeCalibration.set(ctx, fx, fy)
                eye = EyeCalibration.get(ctx)
                note = "Eye point set to (${(fx * 100).toInt()}%, ${(fy * 100).toInt()}%) of the photo. " +
                    "New captures now segment there."
            },
        )
    }
}

// Turns the API's black/white mask into a red, semi-transparent overlay (white -> opaque red, black -> clear).
private val MASK_TO_RED = ColorMatrix(
    floatArrayOf(
        0f, 0f, 0f, 0f, 255f,
        0f, 0f, 0f, 0f, 0f,
        0f, 0f, 0f, 0f, 0f,
        0.33f, 0.33f, 0.33f, 0f, 0f,
    ),
)

private fun describe(r: SegmentResult) =
    "Segmented: score ${String.format(java.util.Locale.US, "%.2f", r.score)}, box ${r.box}. Tap another object to redo."

private suspend fun loadMask(r: SegmentResult): ImageBitmap? = withContext(Dispatchers.Default) {
    r.maskPng?.let { BitmapFactory.decodeByteArray(it, 0, it.size)?.asImageBitmap() }
}

/**
 * A full-size photo where tapping an object sends the photo and that point to the segmentation API and
 * draws the returned mask (red), upright box (green) and rotated box (yellow) over it.
 */
@Composable
private fun SegmentablePhoto(
    file: File,
    eye: Pair<Float, Float>,
    calibrate: Boolean,
    onEyeSet: (Float, Float) -> Unit,
) {
    val scope = rememberCoroutineScope()
    var bitmap by remember(file) { mutableStateOf<Bitmap?>(null) }
    var imgW by remember(file) { mutableIntStateOf(0) }
    var imgH by remember(file) { mutableIntStateOf(0) }
    var result by remember(file) { mutableStateOf<SegmentResult?>(null) }
    var mask by remember(file) { mutableStateOf<ImageBitmap?>(null) }
    var point by remember(file) { mutableStateOf<Offset?>(null) }
    var status by remember(file) {
        mutableStateOf(if (SegmentApi.configured) "Tap the object you want to segment" else "Set segment.token in local.properties to segment")
    }
    var area by remember { mutableStateOf(IntSize.Zero) }

    LaunchedEffect(file) {
        bitmap = PhotoStore.decode(file, 2048)
        val size = PhotoStore.imageSize(file)
        imgW = size.first
        imgH = size.second
        PhotoStore.loadSegment(file)?.let { r ->
            result = r
            mask = loadMask(r)
            status = describe(r)
        }
    }

    Column(modifier = Modifier.fillMaxSize()) {
        Box(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth()
                .onSizeChanged { area = it }
                .pointerInput(file, imgW, imgH, calibrate) {
                    detectTapGestures { tap ->
                        if (imgW == 0 || imgH == 0 || area.width == 0) return@detectTapGestures
                        // Map the tap from screen pixels to pixels of the original photo.
                        val scale = minOf(area.width.toFloat() / imgW, area.height.toFloat() / imgH)
                        val offX = (area.width - imgW * scale) / 2f
                        val offY = (area.height - imgH * scale) / 2f
                        val px = (tap.x - offX) / scale
                        val py = (tap.y - offY) / scale
                        if (px < 0f || py < 0f || px > imgW || py > imgH) return@detectTapGestures
                        if (calibrate) {
                            // Calibration: this spot is what the wearer was looking straight at.
                            onEyeSet(px / imgW, py / imgH)
                            return@detectTapGestures
                        }
                        point = Offset(px, py)
                        status = "Segmenting at (${px.toInt()}, ${py.toInt()})..."
                        scope.launch {
                            val bytes = withContext(Dispatchers.IO) { file.readBytes() }
                            when (val out = SegmentApi.segment(bytes, px, py)) {
                                is SegmentOutcome.Ok -> {
                                    PhotoStore.saveSegmentJson(file, out.result.rawJson)
                                    result = out.result
                                    mask = loadMask(out.result)
                                    status = describe(out.result)
                                }
                                is SegmentOutcome.Fail -> status = "Segment failed: ${out.message}"
                            }
                        }
                    }
                },
        ) {
            bitmap?.let { b ->
                val image = remember(b) { b.asImageBitmap() }
                Image(
                    bitmap = image,
                    contentDescription = file.name,
                    contentScale = ContentScale.Fit,
                    modifier = Modifier.fillMaxSize(),
                )
            }
            Canvas(modifier = Modifier.fillMaxSize()) {
                if (imgW == 0 || imgH == 0) return@Canvas
                val scale = minOf(size.width / imgW, size.height / imgH)
                val dw = imgW * scale
                val dh = imgH * scale
                val ox = (size.width - dw) / 2f
                val oy = (size.height - dh) / 2f

                mask?.let { m ->
                    drawImage(
                        image = m,
                        srcSize = IntSize(m.width, m.height),
                        dstOffset = IntOffset(ox.toInt(), oy.toInt()),
                        dstSize = IntSize(dw.toInt(), dh.toInt()),
                        alpha = 0.5f,
                        colorFilter = ColorFilter.colorMatrix(MASK_TO_RED),
                    )
                }
                result?.let { r ->
                    val b = r.box
                    if (b.size == 4) {
                        drawRect(
                            color = Color.Green,
                            topLeft = Offset(ox + b[0] * scale, oy + b[1] * scale),
                            size = Size((b[2] - b[0]) * scale, (b[3] - b[1]) * scale),
                            style = Stroke(width = 3f),
                        )
                    }
                    if (r.rotatedBox.size == 4) {
                        val path = Path()
                        r.rotatedBox.forEachIndexed { i, (cx, cy) ->
                            val p = Offset(ox + cx * scale, oy + cy * scale)
                            if (i == 0) path.moveTo(p.x, p.y) else path.lineTo(p.x, p.y)
                        }
                        path.close()
                        drawPath(path, Color.Yellow, style = Stroke(width = 2f))
                    }
                }
                point?.let { p ->
                    drawCircle(Color.White, radius = 12f, center = Offset(ox + p.x * scale, oy + p.y * scale))
                    drawCircle(Color.Red, radius = 8f, center = Offset(ox + p.x * scale, oy + p.y * scale))
                }
                // Calibrated eye point (where the wearer looks straight ahead): cyan crosshair.
                val ex = ox + eye.first * dw
                val ey = oy + eye.second * dh
                drawLine(Color.Cyan, Offset(ex - 22f, ey), Offset(ex + 22f, ey), strokeWidth = 3f)
                drawLine(Color.Cyan, Offset(ex, ey - 22f), Offset(ex, ey + 22f), strokeWidth = 3f)
            }
        }
        Text(status, modifier = Modifier.padding(8.dp))
    }
}

@Composable
private fun PhotoImage(
    file: File,
    maxSide: Int,
    contentScale: ContentScale,
    modifier: Modifier = Modifier,
) {
    val bitmap by produceState<Bitmap?>(initialValue = null, file, maxSide) {
        value = PhotoStore.decode(file, maxSide)
    }
    Box(modifier = modifier.background(Color(0xFFEEEEEE)), contentAlignment = Alignment.Center) {
        bitmap?.let {
            Image(
                bitmap = it.asImageBitmap(),
                contentDescription = file.name,
                contentScale = contentScale,
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}
