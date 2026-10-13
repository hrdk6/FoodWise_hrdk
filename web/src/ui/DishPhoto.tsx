import { useMemo, useState } from "react";

import { useDishes } from "@/api/hooks";

import { cx } from "./primitives";

/**
 * A dish's picture, set in a steel plate: a thin rim, then the food. When a dish has no picture
 * yet, the plate shows its initials. Optional diet mark (the familiar Indian veg / non-veg sign).
 */

type Size = "xs" | "sm" | "md" | "lg" | "xl";
const PX: Record<Size, number> = { xs: 28, sm: 36, md: 48, lg: 72, xl: 112 };

export type DishLike = { name: string; image_url?: string | null; image_credit?: string | null; diet?: string | null };

function initials(name: string) {
  const words = name.replace(/\(.*?\)/g, "").split(/\s+/).filter(Boolean);
  return (words.length > 1 ? words[0][0] + words[1][0] : words[0]?.slice(0, 2) ?? "").toUpperCase();
}

export function DietMark({ diet, className }: { diet?: string | null; className?: string }) {
  if (!diet) return null;
  const nonVeg = diet === "non_veg";
  const egg = diet === "egg";
  const color = nonVeg ? "#8A3B1F" : egg ? "#C8880E" : "#2F6E4A";
  const label = nonVeg ? "Non-vegetarian" : egg ? "Contains egg" : diet === "jain" ? "Jain" : "Vegetarian";
  return (
    <span role="img" aria-label={label} title={label} className={cx("inline-flex size-3.5 items-center justify-center rounded-[2px] border-[1.5px] bg-white", className)} style={{ borderColor: color }}>
      {nonVeg ? (
        <svg viewBox="0 0 10 10" className="size-2" aria-hidden>
          <path d="M5 1.5 9 8.5H1z" fill={color} />
        </svg>
      ) : (
        <span className="size-1.5 rounded-full" style={{ background: color }} />
      )}
    </span>
  );
}

export function DishPhoto({ dish, size = "md", showDiet = false, className }: { dish: DishLike; size?: Size; showDiet?: boolean; className?: string }) {
  const [failed, setFailed] = useState(false);
  const px = PX[size];
  const rim = size === "xs" ? 2 : size === "sm" ? 2.5 : size === "xl" ? 5 : 3.5;
  const src = !failed ? dish.image_url : null;
  return (
    <span className={cx("relative inline-block shrink-0", className)} style={{ width: px, height: px }}>
      <span
        className="absolute inset-0 rounded-full bg-gradient-to-br from-white via-[#E3E8E6] to-[#B9C3BF] shadow-[0_1px_2px_rgb(28_42_74/0.18)]"
        aria-hidden
      />
      <span className="absolute overflow-hidden rounded-full bg-steel-2 ring-1 ring-[#A9B4B0]/60" style={{ inset: rim }}>
        {src ? (
          <img
            src={src}
            alt=""
            title={dish.image_credit ? `${dish.name}. Photo: ${dish.image_credit}` : dish.name}
            width={px}
            height={px}
            loading="lazy"
            decoding="async"
            onError={() => setFailed(true)}
            className="size-full object-cover"
          />
        ) : (
          <span
            className="flex size-full items-center justify-center font-[700] text-ink-2"
            style={{ fontSize: Math.max(10, px * 0.3), fontStretch: "112%" }}
            title={dish.name}
            aria-hidden
          >
            {initials(dish.name)}
          </span>
        )}
      </span>
      {showDiet && dish.diet ? <DietMark diet={dish.diet} className="absolute -bottom-0.5 -right-0.5" /> : null}
    </span>
  );
}

/** Look up a dish's picture by id from the organisation's dish list (cached for five minutes). */
export function useDishLookup() {
  const dishes = useDishes();
  return useMemo(() => {
    const byId = new Map((dishes.data ?? []).map((d) => [d.id, d]));
    const byName = new Map((dishes.data ?? []).map((d) => [d.name.toLowerCase(), d]));
    return (idOrName: string | null | undefined, fallbackName = ""): DishLike => {
      const d = (idOrName && (byId.get(idOrName) ?? byName.get(idOrName.toLowerCase()))) || undefined;
      return d ? { name: d.name, image_url: d.image_url, image_credit: d.image_credit, diet: d.diet } : { name: fallbackName || idOrName || "" };
    };
  }, [dishes.data]);
}
