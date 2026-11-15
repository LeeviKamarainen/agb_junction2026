// The glasses' frame as the wearer sees it from behind the lenses: black acetate around
// Wayfarer-style lenses (a straight brow line, angled outer top corners, tapering to rounded
// bottoms) under a solid bridge, open between the lenses below it, where the nose is. It is drawn
// over the picture, as real rims are: the picture fills the stage and the frame covers its edges.

export type FrameShape = {
  /** The box the outline is drawn in: the stage's own shape. */
  width: number;
  height: number;
  /** The opening the lenses leave; everything else is frame. Absolute M, L, Q and C only. */
  lenses: string;
  /** Soft highlights on the acetate, so it reads as a glossy object. */
  gloss: readonly { cx: number; cy: number; rx: number; ry: number }[];
};

/** A wide stage (16:10): both lenses, the display in the right one. */
export const WIDE: FrameShape = {
  width: 1600,
  height: 1000,
  lenses:
    "M 800 202 C 846 202, 874 184, 886 150 C 896 120, 904 97, 940 91 L 1460 70 " +
    "Q 1490 69, 1506 92 L 1540 150 Q 1549 166, 1548 186 C 1546 420, 1540 620, 1526 770 " +
    "C 1514 890, 1458 938, 1350 944 C 1200 952, 1080 952, 1000 940 C 930 932, 866 880, 800 872 " +
    "C 734 880, 670 932, 600 940 C 520 952, 400 952, 250 944 C 142 938, 86 890, 74 770 " +
    "C 60 620, 54 420, 52 186 Q 51 166, 60 150 L 94 92 Q 110 69, 140 70 L 660 91 " +
    "C 696 97, 704 120, 714 150 C 726 184, 754 202, 800 202 Z",
  gloss: [
    { cx: 420, cy: 34, rx: 330, ry: 15 },
    { cx: 1180, cy: 34, rx: 330, ry: 15 },
    { cx: 800, cy: 80, rx: 64, ry: 34 },
  ],
};

/** A phone's tall stage (4:5): the right lens alone, where the display is, its nose side thick
 * where the bridge joins; both lenses would leave the picture too small there. */
export const TALL: FrameShape = {
  width: 800,
  height: 1000,
  lenses:
    "M 70 120 Q 70 86, 104 84 L 650 64 Q 684 63, 702 90 L 746 156 Q 756 172, 755 196 " +
    "C 752 430, 744 640, 730 790 C 718 898, 664 946, 560 952 C 420 960, 250 958, 170 946 " +
    "C 100 934, 64 880, 60 790 C 56 600, 62 300, 70 120 Z",
  gloss: [{ cx: 400, cy: 30, rx: 320, ry: 14 }],
};

