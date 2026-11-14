package com.agb.glassescam

import android.content.Context

/**
 * Which segmentation endpoint to use, remembered across app starts.
 *
 * Normal:  POST /segment       with the point (x, y) = one pixel.
 * Grid:    POST /segment-grid  with the same point plus grid=3 and spacing=0.04, i.e. a 3x3 block of points
 *          around it (spacing is the gap between neighbouring points), which is steadier when a single pixel
 *          lands on an edge or a thin part.
 */
object SegmentSettings {

    private const val PREFS = "segment_settings"
    private const val KEY_GRID = "use_grid"

    /** Number of points per side of the grid (3 = 3x3). */
    const val GRID_SIZE = 3

    /** Gap between neighbouring grid points, as the API defines it (0.04 in the documented example). */
    const val GRID_SPACING = 0.04

    /** Read by [SegmentApi] on every request; set from the app's toggle. */
    @Volatile
    var useGrid: Boolean = false

    fun load(context: Context) {
        useGrid = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean(KEY_GRID, false)
    }

    fun setUseGrid(context: Context, enabled: Boolean) {
        useGrid = enabled
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean(KEY_GRID, enabled).apply()
    }
}
