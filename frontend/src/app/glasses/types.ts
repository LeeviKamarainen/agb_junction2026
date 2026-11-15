import type { components } from "@/lib/api/schema";

type Schemas = components["schemas"];

export type GlassesReply = Schemas["GlassesReply"];
export type FlexBoxNode = Schemas["FlexBox"];
export type LayoutNode = FlexBoxNode["children"][number];
