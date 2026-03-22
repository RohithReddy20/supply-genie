"use client";

import { useEffect, useState, useCallback } from "react";
import { Separator } from "@/components/ui/separator";
import { fetchIncidents } from "@/app/lib/api";
import type { IncidentSummary, IncidentType } from "@/app/lib/types";
import {
  Truck,
  UserX,
  RefreshCw,
  ChevronRight,
  Clock,
  ShieldAlert,
} from "lucide-react";

const TYPE_META: Record<
  IncidentType,
  { label: string; icon: React.ElementType; color: string }
> = {
  shipment_delay: {
    label: "Shipment Delay",
    icon: Truck,
    color: "bg-blue-100 text-blue-600",
  },
  worker_absence: {
    label: "Worker Absence",
    icon: UserX,
    color: "bg-purple-100 text-purple-600",
  },
};

const SEVERITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3 } as const;

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

function getUrgencyClass(iso: string, status: string): string {
  if (status === "resolved") return "";
  const mins = (Date.now() - new Date(iso).getTime()) / 60000;
  if (mins > 60) return "border-l-2 border-l-red-500";
  if (mins > 30) return "border-l-2 border-l-amber-400";
  return "";
}

function getPayloadSubtitle(
  type: IncidentType,
  payload: Record<string, unknown> | null
): string {
  if (!payload) return "";
  if (type === "shipment_delay") {
    return [payload.po_number, payload.supplier_name]
      .filter(Boolean)
      .join(" · ") as string;
  }
  return [payload.worker_name, payload.site_id]
    .filter(Boolean)
    .join(" · ") as string;
}

export function IncidentInbox({
  selectedId,
  onSelect,
}: {
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const res = await fetchIncidents();
      setIncidents(res.items);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, [load]);

  const sorted = [...incidents].sort((a, b) => {
    const sDiff =
      (SEVERITY_ORDER[a.severity] ?? 99) -
      (SEVERITY_ORDER[b.severity] ?? 99);
    if (sDiff !== 0) return sDiff;
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });

  const needsAction = sorted.filter(
    (i) => i.status === "in_progress" || i.status === "open"
  );
  const escalated = sorted.filter((i) => i.status === "escalated");
  const resolved = sorted.filter((i) => i.status === "resolved");

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-4 pt-4 pb-3 flex-shrink-0">
        <div className="flex items-center justify-between mb-1">
          <h2 className="font-heading text-lg font-medium tracking-tight">
            Inbox
          </h2>
          <button
            onClick={load}
            className="p-1.5 rounded-md hover:bg-muted transition-colors"
            title="Refresh"
          >
            <RefreshCw
              className={`w-3.5 h-3.5 text-muted-foreground ${loading ? "animate-spin" : ""}`}
            />
          </button>
        </div>
        <p className="text-[11px] text-muted-foreground">
          {sorted.length} incident{sorted.length !== 1 ? "s" : ""}
        </p>
      </div>

      <Separator />

      {/* Groups */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {loading && sorted.length === 0 ? (
          <div className="flex items-center justify-center py-12">
            <div className="w-5 h-5 border-2 border-muted-foreground border-t-transparent rounded-full animate-spin" />
          </div>
        ) : (
          <div className="py-1">
            {needsAction.length > 0 && (
              <IncidentGroup
                label="Needs your action"
                count={needsAction.length}
                incidents={needsAction}
                selectedId={selectedId}
                onSelect={onSelect}
                accent="amber"
              />
            )}

            {escalated.length > 0 && (
              <IncidentGroup
                label="Escalated"
                count={escalated.length}
                incidents={escalated}
                selectedId={selectedId}
                onSelect={onSelect}
                accent="red"
              />
            )}

            {resolved.length > 0 && (
              <IncidentGroup
                label="Recently resolved"
                count={resolved.length}
                incidents={resolved}
                selectedId={selectedId}
                onSelect={onSelect}
                accent="gray"
              />
            )}

            {sorted.length === 0 && (
              <div className="flex flex-col items-center justify-center py-16 text-center px-4">
                <div className="w-12 h-12 rounded-full bg-[#5c6b55]/10 flex items-center justify-center mb-3">
                  <CheckCircle2 className="w-6 h-6 text-[#5c6b55]/60" />
                </div>
                <p className="text-sm font-medium text-muted-foreground">
                  All clear
                </p>
                <p className="text-xs text-muted-foreground/60 mt-1">
                  No active incidents
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function IncidentGroup({
  label,
  count,
  incidents,
  selectedId,
  onSelect,
  accent,
}: {
  label: string;
  count: number;
  incidents: IncidentSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  accent: "amber" | "red" | "gray" | "olive";
}) {
  const accentDot: Record<string, string> = {
    amber: "bg-amber-500",
    red: "bg-red-500",
    gray: "bg-gray-400",
    olive: "bg-[#5c6b55]",
  };

  return (
    <div>
      <div className="px-4 py-2 flex items-center gap-2">
        <div className={`w-1.5 h-1.5 rounded-full ${accentDot[accent]}`} />
        <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
          {label}
        </span>
        <span className="text-[10px] text-muted-foreground">({count})</span>
      </div>
      {incidents.map((inc) => {
        const meta = TYPE_META[inc.type];
        const Icon = meta.icon;
        const isSelected = selectedId === inc.id;
        const subtitle = getPayloadSubtitle(inc.type, inc.payload);
        const summary = inc.actions_summary;
        const urgencyClass = getUrgencyClass(inc.created_at, inc.status);

        return (
          <button
            key={inc.id}
            onClick={() => onSelect(inc.id)}
            className={`
              w-full text-left px-4 py-3 transition-all
              hover:bg-muted/60
              ${isSelected ? "bg-[#5c6b55]/5 border-l-2 border-l-[#5c6b55]" : urgencyClass}
            `}
          >
            <div className="flex items-start gap-3">
              <div
                className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5 ${meta.color}`}
              >
                <Icon className="w-3.5 h-3.5" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-foreground truncate">
                    {meta.label}
                  </span>
                  <span
                    className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded flex-shrink-0
                    ${
                      inc.severity === "critical"
                        ? "bg-red-100 text-red-700"
                        : inc.severity === "high"
                        ? "bg-amber-100 text-amber-700"
                        : inc.severity === "medium"
                        ? "bg-yellow-100 text-yellow-700"
                        : "bg-muted text-muted-foreground"
                    }
                    `}
                  >
                    {inc.severity}
                  </span>
                </div>

                {/* Payload subtitle */}
                {subtitle && (
                  <p className="text-[10px] text-muted-foreground truncate mt-0.5">
                    {subtitle}
                  </p>
                )}

                {/* Progress bar + approval count */}
                {summary && summary.total > 0 && (
                  <div className="flex items-center gap-2 mt-1.5">
                    <div className="flex-1 h-1 bg-border rounded-full overflow-hidden">
                      <div
                        className="h-full bg-[#5c6b55] rounded-full transition-all duration-500"
                        style={{
                          width: `${(summary.completed / summary.total) * 100}%`,
                        }}
                      />
                    </div>
                    <span className="text-[9px] text-muted-foreground tabular-nums flex-shrink-0">
                      {summary.completed}/{summary.total}
                    </span>
                    {summary.needs_approval > 0 && (
                      <span className="flex items-center gap-0.5 text-[9px] text-amber-600 flex-shrink-0">
                        <ShieldAlert className="w-2.5 h-2.5" />
                        {summary.needs_approval}
                      </span>
                    )}
                  </div>
                )}

                {/* Time */}
                <div className="flex items-center gap-1 mt-1">
                  <Clock className="w-2.5 h-2.5 text-muted-foreground/50" />
                  <span className="text-[10px] text-muted-foreground">
                    {timeAgo(inc.created_at)}
                  </span>
                </div>
              </div>
              <ChevronRight
                className={`w-3.5 h-3.5 flex-shrink-0 mt-1 transition-transform ${
                  isSelected ? "text-[#5c6b55]" : "text-muted-foreground/30"
                }`}
              />
            </div>
          </button>
        );
      })}
    </div>
  );
}

function CheckCircle2({
  className,
}: {
  className?: string;
}) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
      <polyline points="22 4 12 14.01 9 11.01" />
    </svg>
  );
}
