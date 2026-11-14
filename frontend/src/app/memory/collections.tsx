"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api/browser";

import type { CollectionCount } from "./types";

/** A collection name as the backend stores it: trimmed, lower case, `manual` when empty. */
export function cleanCollection(name: string): string {
  return name.trim().toLowerCase() || "manual";
}

export const COLLECTION_PATTERN = "[A-Za-z0-9][A-Za-z0-9_\\-]{0,39}";

/** Why the backend would refuse this collection name, or null when it is fine. */
export function collectionProblem(name: string): string | null {
  return new RegExp(`^${COLLECTION_PATTERN}$`).test(cleanCollection(name))
    ? null
    : "Use up to 40 letters, digits, _ and -, starting with a letter or digit.";
}

/** The collections that hold memories, fetched again whenever `version` changes. */
export function useCollections(version: number): CollectionCount[] {
  const [collections, setCollections] = useState<CollectionCount[]>([]);
  useEffect(() => {
    let current = true;
    void api
      .GET("/api/memories/collections", { cache: "no-store" })
      .then(({ data }) => {
        if (current && data) setCollections(data);
      })
      .catch(() => {}); // the picker just stays empty
    return () => {
      current = false;
    };
  }, [version]);
  return collections;
}

/**
 * Toggle chips for limiting something to some collections. Nothing selected means every
 * collection, which is the default.
 */
export function CollectionFilter({
  collections,
  selected,
  onChange,
  label = "Collections",
  disabled = false,
}: {
  collections: CollectionCount[];
  selected: string[];
  onChange: (selected: string[]) => void;
  label?: string;
  disabled?: boolean;
}) {
  if (collections.length === 0) return null;
  const chip =
    "rounded-full border px-2.5 py-0.5 text-xs transition-colors disabled:opacity-50";
  const on = "border-primary bg-primary/15 text-foreground";
  const off = "border-border text-muted-foreground hover:text-foreground";
  return (
    <div
      role="group"
      aria-label={label}
      className="flex flex-wrap items-center gap-1.5"
    >
      <span className="text-xs text-muted-foreground">{label}:</span>
      <button
        type="button"
        className={`${chip} ${selected.length === 0 ? on : off}`}
        aria-pressed={selected.length === 0}
        onClick={() => onChange([])}
        disabled={disabled}
      >
        All
      </button>
      {collections.map(({ name, count }) => {
        const pressed = selected.includes(name);
        return (
          <button
            key={name}
            type="button"
            className={`${chip} ${pressed ? on : off}`}
            aria-pressed={pressed}
            disabled={disabled}
            onClick={() =>
              onChange(
                pressed
                  ? selected.filter((item) => item !== name)
                  : [...selected, name],
              )
            }
          >
            {name} <span className="opacity-60">{count}</span>
          </button>
        );
      })}
    </div>
  );
}
