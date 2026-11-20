import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, frameImageUrl, type ElementQuery } from "./client";
import type { Deviation, ObservationInput } from "@/lib/types";

export const useProjects = () => useQuery({ queryKey: ["projects"], queryFn: api.projects });
export const useProject = (pid: string) => useQuery({ queryKey: ["project", pid], queryFn: () => api.project(pid) });
export const useLbs = (pid: string) => useQuery({ queryKey: ["lbs", pid], queryFn: () => api.lbs(pid), staleTime: 300_000 });
export const useModels = (pid: string) => useQuery({ queryKey: ["models", pid], queryFn: () => api.models(pid) });
export const useSummary = (pid: string) => useQuery({ queryKey: ["summary", pid], queryFn: () => api.summary(pid) });
export const useByTrade = (pid: string) => useQuery({ queryKey: ["byTrade", pid], queryFn: () => api.byTrade(pid) });
export const useByZone = (pid: string, trade?: string) => useQuery({ queryKey: ["byZone", pid, trade ?? ""], queryFn: () => api.byZone(pid, trade) });
export const useTimeseries = (pid: string) => useQuery({ queryKey: ["timeseries", pid], queryFn: () => api.timeseries(pid) });
export const useActivities = (pid: string) => useQuery({ queryKey: ["activities", pid], queryFn: () => api.activities(pid) });
export const useEvm = (pid: string, scope: "trade" | "zone") => useQuery({ queryKey: ["evm", pid, scope], queryFn: () => api.evm(pid, scope) });
export const useForecast = (pid: string) => useQuery({ queryKey: ["forecast", pid], queryFn: () => api.forecast(pid) });
export const useDeviations = (pid: string) => useQuery({ queryKey: ["deviations", pid], queryFn: () => api.deviations(pid) });
export const useCaptures = (pid: string) => useQuery({ queryKey: ["captures", pid], queryFn: () => api.captures(pid) });
export const useFrames = (cid: string | undefined) => useQuery({ queryKey: ["frames", cid], queryFn: () => api.frames(cid!), enabled: !!cid, staleTime: 300_000 });
export const useOverlay = (fid: string | undefined, enabled = true) => useQuery({ queryKey: ["overlay", fid], queryFn: () => api.overlay(fid!), enabled: !!fid && enabled, staleTime: 300_000 });
export const useElements = (pid: string, q: ElementQuery, enabled = true) => useQuery({ queryKey: ["elements", pid, q], queryFn: () => api.elements(pid, q), enabled, placeholderData: (p) => p });
export const useElement = (uid: string | null) => useQuery({ queryKey: ["element", uid], queryFn: () => api.element(uid!), enabled: !!uid });
export const useFrameImage = (fid: string | undefined, w = 1536) => useQuery({ queryKey: ["frameImage", fid, w], queryFn: () => frameImageUrl(fid!, w), enabled: !!fid, staleTime: Infinity, gcTime: 600_000 });
export const useQueue = (pid: string) => useQuery({ queryKey: ["queue", pid], queryFn: () => api.verificationQueue(pid) });
export const useAcks = (pid: string) => useQuery({ queryKey: ["acks", pid], queryFn: () => api.acks(pid) });

export function useObserve(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ uid, body }: { uid: string; body: ObservationInput }) => api.observe(uid, body),
    onSuccess: (_d, v) => {
      for (const k of ["elements", "summary", "byTrade", "byZone", "deviations", "timeseries", "queue"]) qc.invalidateQueries({ queryKey: [k, pid] });
      qc.invalidateQueries({ queryKey: ["element", v.uid] });
    },
  });
}
export function usePatchDeviation(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<Deviation> }) => api.patchDeviation(id, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["deviations", pid] }); qc.invalidateQueries({ queryKey: ["summary", pid] }); },
  });
}
