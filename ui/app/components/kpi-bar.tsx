"use client";

import { useState, useEffect } from "react";
import { fetchKpis } from "@/app/lib/api";
import type { KPIDashboard } from "@/app/lib/types";
import {
  Activity,
  CheckCircle2,
  AlertTriangle,
  Clock,
  Phone,
  Zap,
} from "lucide-react";

function fmt(n: number, style: "pct" | "ms" | "s" | "int" = "int"): string {
  if (style === "pct") return `${(n * 100).toFixed(1)}%`;
  if (style === "ms") return n < 1000 ? `${n.toFixed(0)}ms` : `${(n / 1000).toFixed(1)}s`;
  if (style === "s") return n < 60 ? `${n.toFixed(0)}s` : `${(n / 60).toFixed(1)}m`;
  return n.toLocaleString();
}

function Metric({
  icon: Icon,
  label,
  value,
  accent,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string;
  accent?: string;
}) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5">
      <Icon className={`w-3.5 h-3.5 ${accent ?? "text-muted-foreground"}`} />
      <div className="flex items-baseline gap-1.5">
        <span className="text-xs font-semibold tabular-nums">{value}</span>
        <span className="text-[10px] text-muted-foreground">{label}</span>
      </div>
    </div>
  );
}

export function KpiBar() {
  const [kpis, setKpis] = useState<KPIDashboard | null>(null);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const data = await fetchKpis();
        if (active) setKpis(data);
      } catch {
        /* ignore */
      }
    };
    load();
    const interval = setInterval(load, 10_000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  if (!kpis) return null;

  const { incidents, actions, voice } = kpis;

  return (
    <div className="flex items-center border-b border-border bg-card/80 backdrop-blur-sm overflow-x-auto flex-shrink-0">
      <div className="flex items-center divide-x divide-border">
        <Metric
          icon={Activity}
          label="Incidents"
          value={fmt(incidents.total)}
        />
        <Metric
          icon={CheckCircle2}
          label="Auto-resolved"
          value={fmt(incidents.auto_resolution_rate, "pct")}
          accent="text-[#5c6b55]"
        />
        <Metric
          icon={AlertTriangle}
          label="Escalation"
          value={fmt(incidents.escalation_rate, "pct")}
          accent={incidents.escalation_rate > 0.2 ? "text-red-500" : "text-muted-foreground"}
        />
        {incidents.mean_time_to_resolution_s != null && (
          <Metric
            icon={Clock}
            label="MTTR"
            value={fmt(incidents.mean_time_to_resolution_s, "s")}
          />
        )}
        <Metric
          icon={Zap}
          label="Action success"
          value={fmt(actions.success_rate, "pct")}
          accent="text-[#5c6b55]"
        />
        {actions.avg_duration_ms != null && (
          <Metric
            icon={Clock}
            label="Avg action"
            value={fmt(actions.avg_duration_ms, "ms")}
          />
        )}
        {voice.total_sessions > 0 && (
          <>
            <Metric
              icon={Phone}
              label="Voice calls"
              value={fmt(voice.total_sessions)}
            />
            <Metric
              icon={Phone}
              label="Answer rate"
              value={fmt(voice.answer_rate, "pct")}
              accent="text-[#5c6b55]"
            />
          </>
        )}
      </div>
    </div>
  );
}
