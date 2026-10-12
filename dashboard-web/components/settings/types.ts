import type { useQuery, useQueryClient } from "@tanstack/react-query";
import type { LLMSettings, UsageReport, User } from "@/lib/types";

export type SettingsQuery = ReturnType<typeof useQuery<LLMSettings>>;
export type MeQuery = ReturnType<typeof useQuery<User>>;
export type UsageQuery = ReturnType<typeof useQuery<UsageReport>>;
export type Toast = (m: string) => void;
export type Invalidate = ReturnType<typeof useQueryClient>;
