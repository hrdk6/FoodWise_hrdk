import { type QueryKey, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { type Schemas, api, unwrap } from "./client";

type S = Schemas;

function useInvalidate() {
  const qc = useQueryClient();
  return (...keys: QueryKey[]) => Promise.all(keys.map((k) => qc.invalidateQueries({ queryKey: k })));
}

// --------------------------------------------------------------------------- tenant

export const useTenant = () => useQuery({ queryKey: ["tenant"], queryFn: () => unwrap(api.GET("/api/v1/tenant")) });

export function usePatchTenant() {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (body: S["TenantPatch"]) => unwrap(api.PATCH("/api/v1/tenant", { body })),
    onSuccess: () => inv(["tenant"], ["readiness"]),
  });
}

// ----- dataset browser (every table the models learn from)

export const useDatasetSummary = (canteenId: string | undefined) =>
  useQuery({
    enabled: !!canteenId,
    queryKey: ["dataset", "summary", canteenId],
    queryFn: () => unwrap(api.GET("/api/v1/dataset/summary", { params: { query: { canteen_id: canteenId! } } })),
    staleTime: 60_000,
  });

export type DatasetQuery = { canteen_id: string; table: string; page: number; page_size: number; start?: string; end?: string; meal?: string; q?: string };

export const useDatasetRows = (q: DatasetQuery | null) =>
  useQuery({
    enabled: !!q?.canteen_id,
    queryKey: ["dataset", "rows", q],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/dataset/rows", {
          params: { query: { ...q!, start: q!.start || null, end: q!.end || null, meal: q!.meal || null, q: q!.q || null } },
        }),
      ),
    placeholderData: (prev) => prev,
  });

export const useReadiness = () =>
  useQuery({ queryKey: ["readiness"], queryFn: () => unwrap(api.GET("/api/v1/tenant/readiness")) });

export const useCanteens = () => useQuery({ queryKey: ["canteens"], queryFn: () => unwrap(api.GET("/api/v1/canteens")) });

export function usePatchCanteen() {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: S["CanteenPatch"] }) =>
      unwrap(api.PATCH("/api/v1/canteens/{canteen_id}", { params: { path: { canteen_id: id } }, body })),
    onSuccess: () => inv(["canteens"], ["readiness"], ["plan"]),
  });
}

export const useDishes = () =>
  useQuery({ queryKey: ["dishes"], queryFn: () => unwrap(api.GET("/api/v1/dishes")), staleTime: 5 * 60_000 });

export function useCalendar(start: string, end: string) {
  return useQuery({
    queryKey: ["calendar", start, end],
    queryFn: () => unwrap(api.GET("/api/v1/calendar", { params: { query: { start, end } } })),
  });
}

export function useAddCalendar() {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (body: S["CalendarIn"]) => unwrap(api.POST("/api/v1/calendar", { body })),
    onSuccess: () => inv(["calendar"], ["plan"]),
  });
}

export function useWeather(canteenId: string | undefined, start: string, end: string) {
  return useQuery({
    enabled: !!canteenId,
    queryKey: ["weather", canteenId, start, end],
    queryFn: () => unwrap(api.GET("/api/v1/weather", { params: { query: { canteen_id: canteenId!, start, end } } })),
  });
}

// --------------------------------------------------------------------------- plans

export function usePlan(canteenId: string | undefined, day: string) {
  return useQuery({
    enabled: !!canteenId,
    queryKey: ["plan", canteenId, day],
    queryFn: () => unwrap(api.GET("/api/v1/forecasts/plan", { params: { query: { canteen_id: canteenId!, day } } })),
  });
}

export function useGeneratePlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: S["GenerateIn"]) => unwrap(api.POST("/api/v1/forecasts/generate", { body })),
    onSuccess: (plan) => qc.setQueryData(["plan", plan.canteen_id, plan.date], plan),
  });
}

export function useDecide() {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: S["DecisionIn"] }) =>
      unwrap(api.PATCH("/api/v1/forecasts/{forecast_id}", { params: { path: { forecast_id: id } }, body })),
    onSuccess: () => inv(["plan"], ["sheet"]),
  });
}

export function useApproveAll() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: S["ApproveAllIn"]) => unwrap(api.POST("/api/v1/forecasts/approve", { body })),
    onSuccess: (plan) => {
      qc.setQueryData(["plan", plan.canteen_id, plan.date], plan);
      qc.invalidateQueries({ queryKey: ["sheet"] });
    },
  });
}

export const useWhatIf = () =>
  useMutation({ mutationFn: (body: S["ScenarioIn"]) => unwrap(api.POST("/api/v1/forecasts/whatif", { body })) });

export function useAccuracy(canteenId: string | undefined, days = 28) {
  return useQuery({
    enabled: !!canteenId,
    queryKey: ["accuracy", canteenId, days],
    queryFn: () => unwrap(api.GET("/api/v1/forecasts/accuracy", { params: { query: { canteen_id: canteenId!, days } } })),
  });
}

export function useDisruption() {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (body: S["DisruptionIn"]) => unwrap(api.POST("/api/v1/disruptions", { body })),
    onSuccess: () => inv(["plan"], ["calendar"]),
  });
}

// --------------------------------------------------------------------------- kitchen

export function useSheet(canteenId: string | undefined, day?: string) {
  return useQuery({
    enabled: !!canteenId,
    queryKey: ["sheet", canteenId, day ?? "today"],
    queryFn: () => unwrap(api.GET("/api/v1/logs/sheet", { params: { query: { canteen_id: canteenId!, day: day ?? null } } })),
    networkMode: "offlineFirst",
  });
}

export function useWaste(canteenId: string | undefined, days = 60) {
  return useQuery({
    queryKey: ["waste", canteenId ?? "all", days],
    queryFn: () => unwrap(api.GET("/api/v1/logs/waste", { params: { query: { canteen_id: canteenId ?? null, days } } })),
  });
}

export function useLive(canteenId: string | undefined) {
  return useQuery({
    enabled: !!canteenId,
    queryKey: ["live", canteenId],
    queryFn: () => unwrap(api.GET("/api/v1/live", { params: { query: { canteen_id: canteenId! } } })),
    refetchInterval: 30_000,
  });
}

export function useStartLive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: S["StartIn"]) => unwrap(api.POST("/api/v1/live/start", { body })),
    onSuccess: (s) => qc.setQueryData(["live", s.canteen_id], s),
  });
}

export function useCheckpoint() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, remaining }: { id: string; remaining: Record<string, number> }) =>
      unwrap(api.POST("/api/v1/live/{session_id}/checkpoint", { params: { path: { session_id: id } }, body: { remaining } })),
    onSuccess: (s) => qc.setQueryData(["live", s.canteen_id], s),
  });
}

// --------------------------------------------------------------------------- models

export function useModels(canteenId: string | undefined) {
  return useQuery({
    enabled: !!canteenId,
    queryKey: ["models", canteenId],
    queryFn: () => unwrap(api.GET("/api/v1/models", { params: { query: { canteen_id: canteenId! } } })),
  });
}

export function useModel(id: string | undefined) {
  return useQuery({
    enabled: !!id,
    queryKey: ["model", id],
    queryFn: () => unwrap(api.GET("/api/v1/models/{version_id}", { params: { path: { version_id: id! } } })),
  });
}

export const useTrain = () =>
  useMutation({ mutationFn: (canteenId: string) => unwrap(api.POST("/api/v1/models/train", { body: { canteen_id: canteenId } })) });

export function useJob(id: string | null) {
  return useQuery({
    enabled: !!id,
    queryKey: ["job", id],
    queryFn: () => unwrap(api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: id! } } })),
    refetchInterval: (q) => (q.state.data && ["succeeded", "dead", "failed"].includes(q.state.data.status) ? false : 1500),
  });
}

export const useGenpact = () =>
  useQuery({ queryKey: ["benchmark", "genpact"], queryFn: () => unwrap(api.GET("/api/v1/benchmark/genpact")) });

export const useRunGenpact = () => useMutation({ mutationFn: () => unwrap(api.POST("/api/v1/benchmark/genpact/run")) });

export function usePromote() {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (id: string) => unwrap(api.POST("/api/v1/models/{version_id}/promote", { params: { path: { version_id: id } } })),
    onSuccess: () => inv(["models"], ["model"], ["readiness"]),
  });
}

export function useRollback() {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (canteenId: string) => unwrap(api.POST("/api/v1/models/rollback", { body: { canteen_id: canteenId } })),
    onSuccess: () => inv(["models"], ["model"]),
  });
}

// --------------------------------------------------------------------------- feedback

export function useFeedbackSummary(canteenId: string | undefined, days = 28) {
  return useQuery({
    enabled: !!canteenId,
    queryKey: ["feedback", canteenId, days],
    queryFn: () => unwrap(api.GET("/api/v1/feedback/summary", { params: { query: { canteen_id: canteenId!, days } } })),
  });
}

export function useComments(canteenId: string | undefined, dishId?: string, sentiment?: "positive" | "negative") {
  return useQuery({
    enabled: !!canteenId,
    queryKey: ["comments", canteenId, dishId, sentiment],
    queryFn: () =>
      unwrap(api.GET("/api/v1/feedback/comments", {
        params: { query: { canteen_id: canteenId!, dish_id: dishId ?? null, sentiment: sentiment ?? null, limit: 40 } },
      })),
  });
}

export function useQr(canteenId: string | undefined, day: string, meal: string | undefined) {
  return useQuery({
    enabled: !!canteenId && !!meal,
    queryKey: ["qr", canteenId, day, meal],
    queryFn: () => unwrap(api.GET("/api/v1/feedback/qr", { params: { query: { canteen_id: canteenId!, day, meal: meal! } } })),
  });
}

export function useRsvpStats(canteenId: string | undefined) {
  return useQuery({
    enabled: !!canteenId,
    queryKey: ["rsvp", canteenId],
    queryFn: () => unwrap(api.GET("/api/v1/feedback/rsvp", { params: { query: { canteen_id: canteenId!, days: 14 } } })),
  });
}

export function useVotes(canteenId: string | undefined) {
  return useQuery({
    enabled: !!canteenId,
    queryKey: ["votes", canteenId],
    queryFn: () => unwrap(api.GET("/api/v1/feedback/votes", { params: { query: { canteen_id: canteenId!, weeks: 4 } } })),
  });
}

export function useMenuSuggestions(canteenId: string | undefined) {
  return useQuery({
    enabled: !!canteenId,
    queryKey: ["menu-suggestions", canteenId],
    queryFn: () => unwrap(api.GET("/api/v1/feedback/menu-suggestions", { params: { query: { canteen_id: canteenId! } } })),
  });
}

export function usePutMenu() {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (body: S["MenuPut"]) => unwrap(api.PUT("/api/v1/menus", { body })),
    onSuccess: () => inv(["menus"], ["menu-suggestions"], ["plan"]),
  });
}

export function useMenus(canteenId: string | undefined, start: string, end: string) {
  return useQuery({
    enabled: !!canteenId,
    queryKey: ["menus", canteenId, start, end],
    queryFn: () => unwrap(api.GET("/api/v1/menus", { params: { query: { canteen_id: canteenId!, start, end } } })),
  });
}

// --------------------------------------------------------------------------- rescue

export function useIncidents(status: "active" | "closed" | "all" = "all", canteenId?: string) {
  return useQuery({
    queryKey: ["incidents", status, canteenId ?? "all"],
    queryFn: () => unwrap(api.GET("/api/v1/incidents", { params: { query: { status, canteen_id: canteenId ?? null } } })),
    refetchInterval: status === "active" ? 20_000 : false,
  });
}

export function useIncident(id: string | undefined) {
  return useQuery({
    enabled: !!id,
    queryKey: ["incident", id],
    queryFn: () => unwrap(api.GET("/api/v1/incidents/{incident_id}", { params: { path: { incident_id: id! } } })),
  });
}

export function useCreateIncident() {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (body: S["IncidentIn"]) => unwrap(api.POST("/api/v1/incidents", { body })),
    onSuccess: () => inv(["incidents"]),
  });
}

export function useIncidentDraft(canteenId: string | undefined, day: string, meal: string | undefined) {
  return useQuery({
    enabled: !!canteenId && !!meal,
    queryKey: ["incident-draft", canteenId, day, meal],
    queryFn: () => unwrap(api.GET("/api/v1/incidents/draft", { params: { query: { canteen_id: canteenId!, day, meal: meal! } } })),
  });
}

export function useIncidentAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (a:
      | { kind: "cancel"; id: string; reason: string }
      | { kind: "close"; id: string; disposal?: S["CloseIn"]["disposal"] }
      | { kind: "donate"; id: string; body: S["DonationIn"] }
      | { kind: "handed-over"; id: string; donationId: string; weight_kg: number }
      | { kind: "fell-through"; id: string; donationId: string; reason?: string }) => {
      const p = { path: { incident_id: a.id } };
      switch (a.kind) {
        case "cancel":
          return unwrap(api.POST("/api/v1/incidents/{incident_id}/cancel", { params: p, body: { reason: a.reason } }));
        case "close":
          return unwrap(api.POST("/api/v1/incidents/{incident_id}/close", { params: p, body: { disposal: a.disposal ?? null } }));
        case "donate":
          return unwrap(api.POST("/api/v1/incidents/{incident_id}/donations", { params: p, body: a.body }));
        case "handed-over":
          return unwrap(api.POST("/api/v1/incidents/{incident_id}/donations/{donation_id}/handed-over", {
            params: { path: { incident_id: a.id, donation_id: a.donationId } },
            body: { weight_kg: a.weight_kg },
          }));
        case "fell-through":
          return unwrap(api.POST("/api/v1/incidents/{incident_id}/donations/{donation_id}/fell-through", {
            params: { path: { incident_id: a.id, donation_id: a.donationId } },
            body: { reason: a.reason ?? null },
          }));
      }
    },
    onSuccess: (inc) => {
      qc.setQueryData(["incident", inc.id], inc);
      qc.invalidateQueries({ queryKey: ["incidents"] });
      qc.invalidateQueries({ queryKey: ["places"] });
    },
  });
}

// --------------------------------------------------------------------------- places nearby

export type NearbyQuery = Omit<S["NearbyIn"], "lat" | "lon" | "accuracy_m"> & { lat?: number | null; lon?: number | null };

export function useNearbyPlaces(q: NearbyQuery | null) {
  return useQuery({
    enabled: !!q?.canteen_id,
    queryKey: ["places", "nearby", q],
    queryFn: () => unwrap(api.POST("/api/v1/places/nearby", { body: q! })),
    staleTime: 5 * 60_000,
    placeholderData: (prev) => prev,
  });
}

export const usePlacesHistory = () =>
  useQuery({ queryKey: ["places", "history"], queryFn: () => unwrap(api.GET("/api/v1/places/history")) });

// --------------------------------------------------------------------------- impact, data, admin

export function useImpact(days = 90, canteenId?: string) {
  return useQuery({
    queryKey: ["impact", days, canteenId ?? "all"],
    queryFn: () => unwrap(api.GET("/api/v1/impact/summary", { params: { query: { days, canteen_id: canteenId ?? null } } })),
  });
}

export const useImports = () => useQuery({ queryKey: ["imports"], queryFn: () => unwrap(api.GET("/api/v1/imports")) });

export function useValidateImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: S["ValidateIn"] }) =>
      unwrap(api.POST("/api/v1/imports/{import_id}/validate", { params: { path: { import_id: id } }, body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["imports"] }),
  });
}

export function useCommitImport() {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: ({ id, create }: { id: string; create: boolean }) =>
      unwrap(api.POST("/api/v1/imports/{import_id}/commit", { params: { path: { import_id: id } }, body: { create_new_dishes: create } })),
    onSuccess: () => inv(["imports"], ["readiness"], ["dishes"]),
  });
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (body: S["PasswordIn"]) => unwrap(api.POST("/api/v1/auth/password", { body, headers: { "X-FoodWise-CSRF": "1" } })),
  });
}

export const useAudit = () =>
  useQuery({ queryKey: ["audit"], queryFn: () => unwrap(api.GET("/api/v1/audit", { params: { query: { limit: 80 } } })) });

export function useOutbox(relatedId?: string) {
  return useQuery({
    queryKey: ["outbox", relatedId ?? "all"],
    queryFn: () => unwrap(api.GET("/api/v1/outbox", { params: { query: { limit: 80, related_id: relatedId ?? null } } })),
  });
}

export const useWebhooks = () => useQuery({ queryKey: ["webhooks"], queryFn: () => unwrap(api.GET("/api/v1/webhooks")) });

export function useCreateWebhook() {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (body: S["WebhookIn"]) => unwrap(api.POST("/api/v1/webhooks", { body })),
    onSuccess: () => inv(["webhooks"]),
  });
}

export function useDeleteWebhook() {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE("/api/v1/webhooks/{hook_id}", { params: { path: { hook_id: id } } })),
    onSuccess: () => inv(["webhooks"]),
  });
}


// --------------------------------------------------------------------------- dish pictures

export function useImageCandidates(dishId: string | null, q?: string) {
  return useQuery({
    enabled: !!dishId,
    queryKey: ["image-candidates", dishId, q ?? ""],
    queryFn: () =>
      unwrap(api.GET("/api/v1/dishes/{dish_id}/image/candidates", { params: { path: { dish_id: dishId! }, query: { q: q || null } } })),
    staleTime: 10 * 60_000,
    retry: false,
  });
}

export function useDishImage() {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: async (a: { kind: "choose"; id: string; title: string } | { kind: "upload"; id: string; file: File } | { kind: "remove" | "auto"; id: string }) => {
      const p = { path: { dish_id: a.id } };
      switch (a.kind) {
        case "choose":
          return unwrap(api.POST("/api/v1/dishes/{dish_id}/image", { params: p, body: { title: a.title } }));
        case "upload": {
          const form = new FormData();
          form.append("file", a.file);
          return unwrap(api.PUT("/api/v1/dishes/{dish_id}/image/upload", {
            params: p,
            body: { file: "" } as never,
            bodySerializer: () => form,
          }));
        }
        case "remove":
          return unwrap(api.DELETE("/api/v1/dishes/{dish_id}/image", { params: p }));
        case "auto":
          return unwrap(api.POST("/api/v1/dishes/{dish_id}/image/auto", { params: p }));
      }
    },
    onSuccess: () => inv(["dishes"]),
  });
}

// --------------------------------------------------------------------------- admin console

const live = { refetchInterval: 15_000, refetchIntervalInBackground: false } as const;

export const useOpsOverview = () => useQuery({ queryKey: ["ops", "overview"], queryFn: () => unwrap(api.GET("/api/v1/ops/overview")), ...live });
export const useOpsModels = (days = 30) =>
  useQuery({ queryKey: ["ops", "models", days], queryFn: () => unwrap(api.GET("/api/v1/ops/models", { params: { query: { days } } })), refetchInterval: 60_000 });
export const useOpsVersion = (id: string | null) =>
  useQuery({
    enabled: !!id,
    queryKey: ["ops", "version", id],
    queryFn: () => unwrap(api.GET("/api/v1/ops/models/versions/{version_id}", { params: { path: { version_id: id! } } })),
  });
export const useOpsApi = (minutes: number) =>
  useQuery({ queryKey: ["ops", "api", minutes], queryFn: () => unwrap(api.GET("/api/v1/ops/api", { params: { query: { minutes } } })), ...live, placeholderData: (p) => p });
export const useOpsJobs = () => useQuery({ queryKey: ["ops", "jobs"], queryFn: () => unwrap(api.GET("/api/v1/ops/jobs")), ...live });
export const useOpsDatabase = () => useQuery({ queryKey: ["ops", "database"], queryFn: () => unwrap(api.GET("/api/v1/ops/database")), refetchInterval: 30_000 });
export const useOpsOrganisations = () => useQuery({ queryKey: ["ops", "organisations"], queryFn: () => unwrap(api.GET("/api/v1/ops/organisations")) });
export const useOpsSecurity = () => useQuery({ queryKey: ["ops", "security"], queryFn: () => unwrap(api.GET("/api/v1/ops/security")), refetchInterval: 60_000 });
export const useOpsVitals = (hours: number) =>
  useQuery({ queryKey: ["ops", "vitals", hours], queryFn: () => unwrap(api.GET("/api/v1/ops/web-vitals", { params: { query: { hours } } })), refetchInterval: 60_000, placeholderData: (p) => p });

export function useOpsAction() {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (a: { kind: "retrain" | "monitor"; canteenId: string } | { kind: "retry"; jobId: string }): Promise<unknown> => {
      if (a.kind === "retry") return unwrap(api.POST("/api/v1/ops/jobs/{job_id}/retry", { params: { path: { job_id: a.jobId } } }));
      const path = a.kind === "retrain" ? "/api/v1/ops/models/{canteen_id}/retrain" : "/api/v1/ops/models/{canteen_id}/monitor";
      return unwrap(api.POST(path, { params: { path: { canteen_id: a.canteenId } } }));
    },
    onSuccess: () => inv(["ops"]),
  });
}
