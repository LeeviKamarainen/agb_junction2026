"use client";

import {
  Brain,
  Camera,
  Check,
  Circle,
  CircleExclamationPoint,
  CircleQuestionMark,
  Glasses,
  Image as ImageIcon,
  type LucideIcon,
  Mic,
} from "lucide-react";
import {
  type CSSProperties,
  type KeyboardEvent,
  type Ref,
  useEffect,
  useRef,
  useState,
} from "react";

import type { FlexBoxNode, LayoutNode } from "./types";

// Icon names the backend uses (backend/app/glasses/screens.py). The Android app keeps its own map.
const icons: Record<string, LucideIcon> = {
  alert: CircleExclamationPoint,
  brain: Brain,
  camera: Camera,
  check: Check,
  glasses: Glasses,
  help: CircleQuestionMark,
  image: ImageIcon,
  mic: Mic,
};

// The glasses' display in pixels; smaller columns get a scaled-down copy.
const SIZE = 600;

const flexAlign = {
  start: "flex-start",
  center: "center",
  end: "flex-end",
  stretch: "stretch",
  "space-between": "space-between",
} as const;

type Props = {
  screen: FlexBoxNode | null;
  onAction: (action: string) => void;
  seeThrough: boolean;
  ref?: Ref<HTMLDivElement>;
  children?: React.ReactNode;
};

/**
 * The glasses' 600x600 display, drawing a layout from the backend. On the real glasses black is
 * transparent; "see-through" mimics that by blending the display over the webcam.
 */
export function Display({
  screen,
  onAction,
  seeThrough,
  ref,
  children,
}: Props) {
  const frameRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const observer = new ResizeObserver(([entry]) =>
      setScale(Math.min(1, entry.contentRect.width / SIZE)),
    );
    observer.observe(frame);
    return () => observer.disconnect();
  }, []);

  // The Neural Band's D-pad: arrows move focus between buttons, Enter (a pinch) presses.
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[
      event.key
    ];
    if (!step) return;
    const buttons = [
      ...event.currentTarget.querySelectorAll<HTMLButtonElement>(
        "button[data-action]",
      ),
    ];
    if (!buttons.length) return;
    event.preventDefault();
    const current = buttons.indexOf(
      document.activeElement as HTMLButtonElement,
    );
    const next =
      current === -1 ? 0 : (current + step + buttons.length) % buttons.length;
    buttons[next].focus();
  }

  return (
    <div
      ref={frameRef}
      className="gh-display-frame"
      style={{ height: SIZE * scale }}
    >
      <div
        className={`gh-display ${seeThrough ? "gh-see-through" : ""}`}
        style={scale < 1 ? { transform: `scale(${scale})` } : undefined}
      >
        {children}
        <div
          ref={ref}
          className="gh-display-content"
          onKeyDown={onKeyDown}
          role="region"
          aria-label="Glasses display"
          aria-live="polite"
        >
          {screen ? <Node node={screen} onAction={onAction} /> : null}
        </div>
      </div>
    </div>
  );
}

function Node({
  node,
  onAction,
}: {
  node: LayoutNode;
  onAction: (action: string) => void;
}) {
  switch (node.type) {
    case "flexbox": {
      const style: CSSProperties = {
        flexDirection: node.direction,
        gap: node.gap,
        padding: node.padding,
        alignItems: flexAlign[node.align],
        justifyContent: flexAlign[node.justify],
      };
      return (
        <div className="gh-flexbox" style={style}>
          {node.children.map((child, index) => (
            <Node key={index} node={child} onAction={onAction} />
          ))}
        </div>
      );
    }
    case "text":
      return (
        <p className={`gh-text gh-text-${node.style.toLowerCase()}`}>
          {node.value}
        </p>
      );
    case "icon": {
      const Icon = icons[node.name] ?? Circle;
      return <Icon className="gh-icon" aria-label={node.name} />;
    }
    case "image":
      return (
        // A plain <img>: the glasses get raw URLs from the backend, not optimised Next images.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          className="gh-image"
          src={node.src}
          alt={node.alt}
          width={node.width ?? undefined}
          height={node.height ?? undefined}
        />
      );
    case "button":
      return (
        <button
          type="button"
          className="gh-button"
          data-action={node.action}
          onClick={() => onAction(node.action)}
        >
          {node.label}
        </button>
      );
  }
}
