import "leaflet/dist/leaflet.css";

import { useEffect } from "react";
import { Circle, CircleMarker, MapContainer, Polyline, TileLayer, Tooltip, useMap } from "react-leaflet";

export type MapTone = "canteen" | "you" | "place" | "agreed" | "far";

export type MapPoint = {
  id: string;
  lat: number;
  lon: number;
  label: string;
  detail?: string;
  tone: MapTone;
};

const STYLE: Record<MapTone, { color: string; fill: string; r: number; weight: number; label: string }> = {
  canteen: { color: "#1C2A4A", fill: "#E6A21A", r: 11, weight: 3, label: "Canteen (pickup point)" },
  you: { color: "#F9FBFA", fill: "#3E6DB0", r: 8, weight: 3, label: "You" },
  agreed: { color: "#F9FBFA", fill: "#2F6E4A", r: 9, weight: 2, label: "Taking food" },
  place: { color: "#F9FBFA", fill: "#3A4763", r: 7, weight: 2, label: "Place nearby" },
  far: { color: "#F9FBFA", fill: "#A3B0AC", r: 6, weight: 2, label: "Too far for the safe window" },
};

function Fit({ points }: { points: MapPoint[] }) {
  const map = useMap();
  const key = points.map((p) => p.id).join("|");
  useEffect(() => {
    if (points.length < 2) return;
    const lats = points.map((p) => p.lat);
    const lons = points.map((p) => p.lon);
    map.fitBounds([[Math.min(...lats), Math.min(...lons)], [Math.max(...lats), Math.max(...lons)]], { padding: [28, 28], maxZoom: 15 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return null;
}

/** OpenStreetMap tiles carry their required attribution. */
export function MapView({
  points,
  center,
  height = 360,
  routes = [],
  accuracy,
  onSelect,
}: {
  points: MapPoint[];
  center: [number, number];
  height?: number;
  routes?: [number, number][][];
  accuracy?: { lat: number; lon: number; m: number } | null;
  onSelect?: (id: string) => void;
}) {
  const ordered = [...points].sort((a, b) => STYLE[a.tone].r - STYLE[b.tone].r);
  return (
    <div className="isolate overflow-hidden rounded-[10px] border border-rim" style={{ height }}>
      <MapContainer center={center} zoom={13} scrollWheelZoom={false} style={{ height: "100%", width: "100%" }}>
        <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
        <Fit points={ordered} />
        {accuracy && accuracy.m > 30 ? (
          <Circle center={[accuracy.lat, accuracy.lon]} radius={accuracy.m} pathOptions={{ color: "#3E6DB0", weight: 1, fillOpacity: 0.08 }} />
        ) : null}
        {routes.map((r, i) => (
          <Polyline key={i} positions={r} pathOptions={{ color: "#2F6E4A", weight: 3, dashArray: "6 6", opacity: 0.8 }} />
        ))}
        {ordered.map((p) => {
          const s = STYLE[p.tone];
          return (
            <CircleMarker
              key={p.id}
              center={[p.lat, p.lon]}
              radius={s.r}
              pathOptions={{ color: s.color, weight: s.weight, fillColor: s.fill, fillOpacity: 1 }}
              eventHandlers={onSelect ? { click: () => onSelect(p.id) } : undefined}
            >
              <Tooltip direction="top" offset={[0, -6]}>
                <span className="font-medium">{p.label}</span>
                {p.detail ? <span className="block text-xs">{p.detail}</span> : null}
              </Tooltip>
            </CircleMarker>
          );
        })}
      </MapContainer>
    </div>
  );
}

export function MapLegend({ tones }: { tones: MapTone[] }) {
  return (
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted">
      {tones.map((tone) => (
        <span key={tone} className="inline-flex items-center gap-1.5">
          <span className="inline-block size-3 rounded-full border-2" style={{ background: STYLE[tone].fill, borderColor: STYLE[tone].color === "#F9FBFA" ? STYLE[tone].fill : STYLE[tone].color }} />
          {STYLE[tone].label}
        </span>
      ))}
    </div>
  );
}
