"use client";

import { useEffect, useState, useCallback } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { fetchIncidents } from "@/app/lib/api";
import { IncidentStatusBadge, SeverityBadge } from "./status-badge";
import type { IncidentSummary, IncidentStatus, IncidentType } from "@/app/lib/types";
import {
  AlertTriangle,
  Truck,
  UserX,
  RefreshCw,
} from "lucide-react";

const TYPE_LABELS: Record<IncidentType, { label: string; icon: React.ElementType }> = {
  shipment_delay: { label: "Shipment Delay", icon: Truck },
  worker_absence: { label: "Worker Absence", icon: UserX },
};

const STATUS_FILTERS: { value: IncidentStatus | ""; label: string }[] = [
  { value: "", label: "All" },
  { value: "open", label: "Open" },
  { value: "in_progress", label: "In Progress" },
  { value: "resolved", label: "Resolved" },
  { value: "escalated", label: "Escalated" },
];

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export function IncidentList({
  selectedId,
  onSelect,
}: {
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [statusFilter, setStatusFilter] = useState<IncidentStatus | "">("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const res = await fetchIncidents(statusFilter || undefined);
      setIncidents(res.items);
      setTotal(res.total);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, [load]);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-5 pt-5 pb-3">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="font-heading text-xl font-medium tracking-tight">
              Incidents
            </h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              {total} total
            </p>
          </div>
          <button
            onClick={load}
            className="p-2 rounded-md hover:bg-muted transition-colors"
            title="Refresh"
          >
            <RefreshCw className="w-4 h-4 text-muted-foreground" />
          </button>
        </div>

        {/* Status filter pills */}
        <div className="flex gap-1.5 flex-wrap">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setStatusFilter(f.value)}
              className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                statusFilter === f.value
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted text-muted-foreground hover:text-foreground"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      <Separator />

      {/* List */}
      <ScrollArea className="flex-1">
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <div className="w-5 h-5 border-2 border-muted-foreground border-t-transparent rounded-full animate-spin" />
          </div>
        ) : incidents.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
            <AlertTriangle className="w-8 h-8 mb-2 opacity-40" />
            <p className="text-sm">No incidents found</p>
          </div>
        ) : (
          <div className="py-1">
            {incidents.map((inc) => {
              const typeMeta = TYPE_LABELS[inc.type];
              const Icon = typeMeta.icon;
              const isSelected = selectedId === inc.id;

              return (
                <button
                  key={inc.id}
                  onClick={() => onSelect(inc.id)}
                  className={`w-full text-left px-5 py-3.5 transition-colors hover:bg-muted/60 ${
                    isSelected ? "bg-muted border-r-2 border-r-[#5c6b55]" : ""
                  }`}
                >
                  <div className="flex items-start gap-3">
                    <div className="mt-0.5 flex-shrink-0">
                      <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center">
                        <Icon className="w-4 h-4 text-muted-foreground" />
                      </div>
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium truncate">
                          {typeMeta.label}
                        </span>
                        <SeverityBadge severity={inc.severity} />
                      </div>
                      <div className="flex items-center gap-2 mt-1">
                        <IncidentStatusBadge status={inc.status} />
                        <span className="text-xs text-muted-foreground">
                          {timeAgo(inc.created_at)}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground mt-1 font-mono truncate">
                        {inc.idempotency_key}
                      </p>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </ScrollArea>
    </div>
  );
}
