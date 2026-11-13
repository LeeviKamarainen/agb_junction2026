package com.agb.glassescam

import android.graphics.Bitmap
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
            )
        }
    }
}

@Composable
private fun GalleryGrid(photos: List<File>, onClose: () -> Unit, onOpen: (Int) -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(16.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("Gallery (${photos.size})")
        OutlinedButton(onClick = onClose) { Text("Close") }
    }
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

@Composable
private fun GalleryPager(
    photos: List<File>,
    startIndex: Int,
    onBack: () -> Unit,
    onDelete: (File) -> Unit,
) {
    val pagerState = rememberPagerState(initialPage = startIndex) { photos.size }

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
    HorizontalPager(
        state = pagerState,
        modifier = Modifier.fillMaxSize(),
        key = { photos[it].name },
    ) { page ->
        PhotoImage(
            file = photos[page],
            maxSide = 2048, // full-size view, downsampled to a screen-friendly size
            contentScale = ContentScale.Fit,
            modifier = Modifier.fillMaxSize(),
        )
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
