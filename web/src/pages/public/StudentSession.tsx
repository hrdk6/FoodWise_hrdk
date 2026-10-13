import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Lock } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router";

import { ApiError, type Schemas, api, unwrap } from "@/api/client";
import { fmtDay } from "@/lib/format";
import { type StringKey, useLang } from "@/lib/i18n";
import { useToast } from "@/ui/overlays";
import { Button, Segmented, Skeleton, Textarea, cx } from "@/ui/primitives";
import { DishPhoto } from "@/ui/DishPhoto";

function deviceId() {
  const KEY = "fw-device";
  try {
    let id = localStorage.getItem(KEY);
    if (!id) {
      id = crypto.randomUUID();
      localStorage.setItem(KEY, id);
    }
    return id;
  } catch {
    return `session-${Math.random().toString(36).slice(2)}${Date.now()}`;
  }
}
const DEVICE = deviceId();
const H = { "X-Device-ID": DEVICE };

const RATINGS: { value: number; key: StringKey; glyph: string }[] = [
  { value: 5, key: "liked", glyph: "M7 14c1.4 2 3 3 5 3s3.6-1 5-3" },
  { value: 3, key: "okay", glyph: "M7.5 15.5h9" },
  { value: 1, key: "disliked", glyph: "M7 17c1.4-2 3-3 5-3s3.6 1 5 3" },
];
const ASPECTS = ["taste", "quantity", "temperature", "freshness", "variety", "hygiene"] as const;

function Face({ path }: { path: string }) {
  return (
    <svg viewBox="0 0 24 24" className="size-7" aria-hidden>
      <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="8.6" cy="9.5" r="1.2" fill="currentColor" />
      <circle cx="15.4" cy="9.5" r="1.2" fill="currentColor" />
      <path d={path} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

type Dish = Schemas["SessionDish"];

function DishCard({ token, dish, lang, t }: { token: string; dish: Dish; lang: string; t: (k: StringKey) => string }) {
  const toast = useToast();
  const [rating, setRating] = useState<number | null>(dish.my_rating ?? null);
  const [aspects, setAspects] = useState<Record<string, -1 | 1>>({});
  const [portion, setPortion] = useState<"too_much" | "right" | "too_little" | null>(null);
  const [text, setText] = useState("");
  const [more, setMore] = useState(false);
  const [sent, setSent] = useState(false);
  const send = useMutation({
    mutationFn: (body: Schemas["FeedbackIn"]) => unwrap(api.POST("/api/v1/public/feedback", { body, headers: H })),
  });
  const submit = async (r: number, extra?: Partial<Schemas["FeedbackIn"]>) => {
    try {
      await send.mutateAsync({ token, dish_id: dish.id, rating: r, aspects, portion, text: text || null, ...extra });
      return true;
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "Couldn't send. Try again.");
      return false;
    }
  };
  const tap = async (r: number) => {
    setRating(r);
    setMore(r <= 3);
    await submit(r);
  };
  const name = lang === "hi" && dish.name_hi ? dish.name_hi : dish.name;
  return (
    <li className="rim px-4 py-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <DishPhoto dish={dish} size="lg" showDiet />
          <p className="text-lg font-[650] leading-snug" lang={lang === "hi" && dish.name_hi ? "hi" : undefined}>
            {name}
          </p>
        </div>
        {rating ? (
          <span className="inline-flex items-center gap-1 text-sm text-leaf">
            <Check className="size-4" aria-hidden /> {t("sent")}
          </span>
        ) : null}
      </div>
      <div role="radiogroup" aria-label={`${t("howWas")} ${dish.name}`} className="grid grid-cols-3 gap-2">
        {RATINGS.map((r) => (
          <button
            key={r.value}
            role="radio"
            aria-checked={rating === r.value}
            onClick={() => tap(r.value)}
            className={cx(
              "flex flex-col items-center gap-1 rounded-[12px] border px-2 py-3 text-sm font-medium transition-colors",
              rating === r.value
                ? r.value === 5
                  ? "border-leaf bg-leaf text-white"
                  : r.value === 3
                    ? "border-ink bg-ink text-steel"
                    : "border-chilli bg-chilli text-white"
                : "border-rim bg-white text-ink-2 hover:border-rim-strong",
            )}
          >
            <Face path={r.glyph} />
            {t(r.key)}
          </button>
        ))}
      </div>
      {rating && !more && !sent ? (
        <button onClick={() => setMore(true)} className="mt-3 text-sm text-ink-2 underline underline-offset-4">
          {t("comment").replace(" (optional)", "").replace(" (वैकल्पिक)", "")}
        </button>
      ) : null}
      {rating && more && !sent ? (
        <div className="mt-4 flex flex-col gap-4">
          <div>
            <p className="mb-2 text-sm font-medium text-ink-2">{rating <= 3 ? t("whatWasOff") : t("howWas")}</p>
            <div className="flex flex-wrap gap-2">
              {ASPECTS.map((a) => {
                const on = aspects[a] !== undefined;
                return (
                  <button
                    key={a}
                    aria-pressed={on}
                    onClick={() =>
                      setAspects((x) => {
                        const n = { ...x };
                        if (on) delete n[a];
                        else n[a] = rating <= 3 ? -1 : 1;
                        return n;
                      })
                    }
                    className={cx("rounded-full border px-3.5 py-1.5 text-sm", on ? "border-ink bg-ink text-steel" : "border-rim bg-white text-ink-2")}
                  >
                    {t(a)}
                  </button>
                );
              })}
            </div>
          </div>
          <div>
            <p className="mb-2 text-sm font-medium text-ink-2">{t("portion")}</p>
            <Segmented
              size="sm"
              label={t("portion")}
              value={portion ?? ("" as "right")}
              onChange={(v) => setPortion(v)}
              options={[
                { value: "too_little", label: t("tooLittle") },
                { value: "right", label: t("right") },
                { value: "too_much", label: t("tooMuch") },
              ]}
            />
          </div>
          <div>
            <label htmlFor={`c-${dish.id}`} className="mb-1.5 block text-sm font-medium text-ink-2">
              {t("comment")}
            </label>
            <Textarea id={`c-${dish.id}`} value={text} onChange={(e) => setText(e.target.value)} maxLength={600} rows={2} />
            <p className="mt-1 text-xs text-muted">{t("commentHint")}</p>
          </div>
          <Button
            variant="primary"
            busy={send.isPending}
            onClick={async () => {
              if (await submit(rating)) {
                setSent(true);
                setMore(false);
              }
            }}
          >
            {t("send")}
          </Button>
        </div>
      ) : null}
    </li>
  );
}

export default function StudentSession() {
  const { token = "" } = useParams();
  const [lang, setLang, t] = useLang();
  const qc = useQueryClient();
  const toast = useToast();
  const session = useQuery({
    queryKey: ["session", token],
    queryFn: () => unwrap(api.GET("/api/v1/public/session/{token}", { params: { path: { token } }, headers: H })),
    retry: false,
  });
  const rsvp = useMutation({
    mutationFn: (body: Schemas["RsvpIn"]) => unwrap(api.POST("/api/v1/public/rsvp", { body, headers: H })),
    onSuccess: (slots) => qc.setQueryData(["session", token], (s: Schemas["SessionOut"] | undefined) => (s ? { ...s, rsvp: slots } : s)),
    onError: (e) => toast("error", e instanceof ApiError ? e.message : "Couldn't save."),
  });
  const vote = useMutation({
    mutationFn: (dish_id: string) => unwrap(api.POST("/api/v1/public/vote", { body: { token, dish_id }, headers: H })),
    onSuccess: (_, dish_id) => qc.setQueryData(["session", token], (s: Schemas["SessionOut"] | undefined) => (s && s.vote ? { ...s, vote: { ...s.vote, my_vote: dish_id } } : s)),
  });
  const s = session.data;
  return (
    <div className="min-h-dvh bg-steel">
      <div className="mx-auto max-w-md px-4 pb-16 pt-6">
        <header className="mb-6 flex items-start justify-between gap-3">
          <div>
            <p className="text-sm text-muted">{s ? `${s.college}` : "FoodWise"}</p>
            <h1 className="text-2xl font-[700]" style={{ fontStretch: "116%" }}>
              {s ? `${s.canteen}, ${s.meal_label}` : t("howWas")}
            </h1>
            {s ? <p className="text-sm text-muted">{fmtDay(s.date, { weekday: "long", day: "numeric", month: "long" })}</p> : null}
          </div>
          <Segmented
            size="sm"
            label="Language"
            value={lang}
            onChange={setLang}
            options={[
              { value: "en", label: "EN" },
              { value: "hi", label: <span lang="hi">हिं</span> },
            ]}
          />
        </header>

        {session.isLoading ? (
          <Skeleton className="h-80 w-full" />
        ) : session.error ? (
          <div className="rim px-5 py-6">
            <p className="text-lg font-[620]">{t("expired")}</p>
          </div>
        ) : (
          <>
            <p className="mb-3 text-lg font-[620]">{t("howWas")}</p>
            <ul className="flex flex-col gap-3">
              {s!.dishes.map((d) => (
                <DishCard key={d.id} token={token} dish={d} lang={lang} t={t} />
              ))}
            </ul>

            {s!.rsvp.length ? (
              <section className="mt-8" aria-labelledby="rsvp">
                <h2 id="rsvp" className="text-lg font-[650]">
                  {t("rsvpTitle")}
                </h2>
                <p className="mb-3 text-sm text-muted">{s!.rsvp[0].open ? t("rsvpHint") : t("rsvpClosed")}</p>
                <ul className="rim divide-y divide-rim">
                  {s!.rsvp.map((slot) => (
                    <li key={slot.meal} className="flex items-center justify-between gap-3 px-4 py-3">
                      <span className="font-medium">{slot.label}</span>
                      <Segmented
                        size="sm"
                        label={slot.label}
                        value={(slot.status ?? "") as "eating"}
                        onChange={(v) => slot.open && rsvp.mutate({ token, date: slot.date, meal: slot.meal, status: v })}
                        options={[
                          { value: "eating", label: t("eating") },
                          { value: "skipping", label: t("skipping") },
                        ]}
                      />
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            {s!.vote ? (
              <section className="mt-8" aria-labelledby="vote">
                <h2 id="vote" className="mb-3 text-lg font-[650]">
                  {t("vote")}
                </h2>
                <div className="grid grid-cols-2 gap-2">
                  {s!.vote.candidates.map((c) => {
                    const mine = s!.vote!.my_vote === c.id;
                    return (
                      <button
                        key={c.id}
                        aria-pressed={mine}
                        onClick={() => vote.mutate(c.id)}
                        className={cx("flex items-center gap-2.5 rounded-[12px] border px-3 py-3 text-left font-medium", mine ? "border-haldi bg-haldi-soft text-ink" : "border-rim bg-white text-ink-2")}
                      >
                        <DishPhoto dish={c} size="md" />
                        <span className="min-w-0">
                          <span className="block" lang={lang === "hi" && c.name_hi ? "hi" : undefined}>{lang === "hi" && c.name_hi ? c.name_hi : c.name}</span>
                          {mine ? <span className="mt-0.5 block text-xs text-haldi-deep">{t("voted")}</span> : null}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </section>
            ) : null}

            <p className="mt-10 flex items-center justify-center gap-1.5 text-center text-sm text-muted">
              <Lock className="size-3.5" aria-hidden /> {t("anonymous")}
            </p>
            {s!.dishes.some((d) => d.image_credit && d.image_credit !== "Photo by the kitchen") ? (
              <details className="mt-3 text-center text-xs text-muted">
                <summary className="cursor-pointer">Photo credits</summary>
                <ul className="mt-1 flex flex-col gap-0.5">
                  {s!.dishes
                    .filter((d) => d.image_credit && d.image_credit !== "Photo by the kitchen")
                    .map((d) => (
                      <li key={d.id}>
                        {d.name}: {d.image_credit}
                      </li>
                    ))}
                </ul>
              </details>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}
