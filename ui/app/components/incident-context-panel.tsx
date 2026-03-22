"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { retryIncident } from "@/app/lib/api";
import { ActionTimeline } from "./action-timeline";
import type { IncidentDetail } from "@/app/lib/types";
import {
  UserX,
  RotateCcw,
  Copy,
  Check,
  MapPin,
  Package,
  User,
  ChevronDown,
  ChevronRight,
} from "lucide-react";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

export function IncidentContextPanel({
  incident,
  onRefresh,
}: {
  incident: IncidentDetail;
  onRefresh: () => void;
}) {
  const [retrying, setRetrying] = useState(false);
  const [metaOpen, setMetaOpen] = useState(false);

  const isDelay = incident.type === "shipment_delay";
  const hasFailedActions = incident.actions.some((a) => a.status === "failed");
  const payload = incident.payload as Record<string, string> | null;

  const handleRetry = async () => {
    setRetrying(true);
    try {
      await retryIncident(incident.id);
      onRefresh();
    } finally {
      setRetrying(false);
    }
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Panel header */}
      <div className="flex-shrink-0 px-4 pt-4 pb-3 border-b border-border bg-card">
        <div className="flex items-center justify-between">
          <h2 className="font-heading text-sm font-medium tracking-tight text-muted-foreground uppercase">
            Context
          </h2>
          {hasFailedActions && (
            <button
              onClick={handleRetry}
              disabled={retrying}
              className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium bg-white border border-border rounded-md hover:bg-muted transition-colors disabled:opacity-50"
            >
              <RotateCcw
                className={`w-3 h-3 ${retrying ? "animate-spin" : ""}`}
              />
              Retry failed
            </button>
          )}
        </div>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {/* Entity cards — grouped by domain entity */}
        {isDelay ? (
          <DelayEntityCards payload={payload} />
        ) : (
          <AbsenceEntityCards payload={payload} />
        )}

        {/* Collapsible metadata */}
        <div>
          <button
            onClick={() => setMetaOpen(!metaOpen)}
            className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground hover:text-foreground transition-colors w-full"
          >
            {metaOpen ? (
              <ChevronDown className="w-3 h-3" />
            ) : (
              <ChevronRight className="w-3 h-3" />
            )}
            Incident metadata
          </button>
          {metaOpen && (
            <div className="mt-2 space-y-1.5 pl-4.5">
              <MetaRow label="Idempotency key" value={incident.idempotency_key} mono />
              <MetaRow label="Correlation ID" value={incident.correlation_id} mono copyable />
              <MetaRow label="Source" value={incident.source} />
              <MetaRow label="Created" value={formatDate(incident.created_at)} />
              <MetaRow label="Updated" value={formatDate(incident.updated_at)} />
              {incident.resolved_at && (
                <MetaRow label="Resolved" value={formatDate(incident.resolved_at)} />
              )}
            </div>
          )}
        </div>

        <Separator />

        {/* Action history */}
        <div>
          <div className="flex items-center gap-2 mb-3">
            <div className="w-4 h-4 rounded bg-muted flex items-center justify-center">
              <span className="text-[8px] font-bold text-muted-foreground">
                H
              </span>
            </div>
            <h3 className="text-xs font-semibold text-foreground">
              Action history
            </h3>
          </div>
          <ActionTimeline actions={incident.actions} />
        </div>
      </div>
    </div>
  );
}

// ── Delay entity cards ─────────────────────────────────────────────

function DelayEntityCards({ payload }: { payload: Record<string, string> | null }) {
  if (!payload) return null;

  const supplierName = payload.supplier_name;
  const supplierPhone = payload.supplier_phone;
  const supplierEmail = payload.supplier_email;
  const region = payload.region;
  const poNumber = payload.po_number;
  const originalEta = payload.original_eta;
  const currentEta = payload.current_eta;
  const customerName = payload.customer_name;
  const customerEmail = payload.customer_email;

  return (
    <div className="space-y-3">
      {/* Supplier card */}
      {(supplierName || supplierPhone || supplierEmail) && (
        <EntityCard
          icon={MapPin}
          title="Supplier"
          rows={[
            supplierName && { label: "Name", value: supplierName },
            supplierPhone && { label: "Phone", value: supplierPhone },
            supplierEmail && { label: "Email", value: supplierEmail },
            region && { label: "Region", value: region },
          ].filter(Boolean) as EntityRow[]}
        />
      )}

      {/* Shipment card */}
      {(poNumber || originalEta) && (
        <EntityCard
          icon={Package}
          title="Shipment"
          rows={[
            poNumber && { label: "PO Number", value: poNumber, mono: true },
            originalEta && { label: "Original ETA", value: originalEta },
            currentEta && {
              label: "Current ETA",
              value: currentEta,
              highlight: currentEta !== originalEta,
            },
          ].filter(Boolean) as EntityRow[]}
        />
      )}

      {/* Customer card */}
      {(customerName || customerEmail) && (
        <EntityCard
          icon={User}
          title="Customer"
          rows={[
            customerName && { label: "Name", value: customerName },
            customerEmail && { label: "Email", value: customerEmail },
          ].filter(Boolean) as EntityRow[]}
        />
      )}
    </div>
  );
}

// ── Absence entity cards ───────────────────────────────────────────

function AbsenceEntityCards({ payload }: { payload: Record<string, string> | null }) {
  if (!payload) return null;

  const workerName = payload.worker_name;
  const role = payload.role;
  const siteId = payload.site_id;
  const shiftDate = payload.shift_date;
  const reason = payload.reason;

  return (
    <div className="space-y-3">
      <EntityCard
        icon={UserX}
        title="Worker"
        rows={[
          workerName && { label: "Name", value: workerName },
          role && { label: "Role", value: role },
          siteId && { label: "Site", value: siteId, mono: true },
          shiftDate && { label: "Shift date", value: shiftDate },
          reason && { label: "Reason", value: reason },
        ].filter(Boolean) as EntityRow[]}
      />
    </div>
  );
}

// ── Shared entity card ─────────────────────────────────────────────

interface EntityRow {
  label: string;
  value: string;
  mono?: boolean;
  highlight?: boolean;
}

function EntityCard({
  icon: Icon,
  title,
  rows,
}: {
  icon: React.ElementType;
  title: string;
  rows: EntityRow[];
}) {
  return (
    <Card className="bg-card border-border shadow-none">
      <CardHeader className="pb-1.5 pt-3 px-3.5">
        <div className="flex items-center gap-2">
          <Icon className="w-3.5 h-3.5 text-[#5c6b55]" />
          <p className="text-[10px] font-semibold tracking-widest uppercase text-muted-foreground">
            {title}
          </p>
        </div>
      </CardHeader>
      <CardContent className="px-3.5 pb-3">
        <div className="space-y-1.5">
          {rows.map((row) => (
            <div key={row.label} className="flex items-center gap-2">
              <span className="text-[11px] text-muted-foreground w-24 flex-shrink-0">
                {row.label}
              </span>
              <span
                className={`text-xs font-medium truncate ${row.mono ? "font-mono" : ""} ${
                  row.highlight ? "text-amber-600" : "text-foreground"
                }`}
              >
                {row.value}
              </span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Metadata row ───────────────────────────────────────────────────

function MetaRow({
  label,
  value,
  mono,
  copyable,
}: {
  label: string;
  value: string;
  mono?: boolean;
  copyable?: boolean;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex items-center gap-2 text-[11px]">
      <span className="text-muted-foreground w-28 flex-shrink-0">{label}</span>
      <span className={`text-foreground truncate ${mono ? "font-mono text-[10px]" : ""}`}>
        {value}
      </span>
      {copyable && (
        <button
          onClick={handleCopy}
          className="flex-shrink-0 text-muted-foreground hover:text-foreground transition-colors"
        >
          {copied ? <Check className="w-3 h-3 text-[#5c6b55]" /> : <Copy className="w-3 h-3" />}
        </button>
      )}
    </div>
  );
}
