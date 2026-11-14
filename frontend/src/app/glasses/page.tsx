import type { Metadata } from "next";

import "./glasses.css";
import { GlassesHarness } from "./harness";

export const metadata: Metadata = {
  title: "Glasses harness — Junction / 26",
  description:
    "Stand-in for the Meta Ray-Ban Display glasses: phone rear camera as the feed, phone mic as the microphone, and the backend's layouts on a 600x600 display.",
};

export default function GlassesPage() {
  return <GlassesHarness />;
}
