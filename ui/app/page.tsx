"use client";

import { useState, useEffect, useCallback } from "react";
import { IncidentInbox } from "./components/incident-inbox";
import { IncidentContextPanel } from "./components/incident-context-panel";
import { WorkflowPlaybook } from "./components/workflow-playbook";
import { ApprovalQueue } from "./components/approval-queue";
import { ChatPanel } from "./components/chat-panel";
import { fetchIncident } from "./lib/api";
import type { IncidentDetail } from "./lib/types";
import {
  Inbox,
  LayoutDashboard,
  Settings,
  Truck,
  UserX,
  Clock,
  Copy,
  Check,
  ExternalLink,
} from "lucide-react";

// ── Nav Rail (slim vertical sidebar) ──────────────────────────────────

function NavRail() {
  return (
    <nav className="w-14 bg-[#3d4a37] flex flex-col items-center py-3 gap-1 flex-shrink-0">
      {/* Logo */}
      <div className="w-8 h-8 rounded-lg bg-white/15 flex items-center justify-center mb-4">
        <span className="text-sm font-bold text-white">H</span>
      </div>

      {/* Nav items */}
      <NavButton icon={Inbox} label="Inbox" active />
      <NavButton icon={LayoutDashboard} label="Dashboard" />
      <NavButton icon={Settings} label="Settings" />

      {/* Spacer */}
      <div className="flex-1" />

      {/* User avatar */}
      <div className="w-8 h-8 rounded-full bg-[#e07a5f] flex items-center justify-center cursor-default" title="Supply Chain Operator">
        <span className="text-xs font-bold text-white">OP</span>
      </div>
    </nav>
  );
}

function NavButton({
  icon: Icon,
  label,
  active,
}: {
  icon: React.ElementType;
  label: string;
  active?: boolean;
}) {
  return (
    <button
      title={label}
      className={`
        w-10 h-10 rounded-lg flex items-center justify-center transition-colors
        ${active
          ? "bg-white/15 text-white"
          : "text-white/40 hover:text-white/70 hover:bg-white/8"
        }
      `}
    >
      <Icon className="w-[18px] h-[18px]" strokeWidth={1.8} />
    </button>
  );
}

// ── Incident Banner ──────────────────────────────────────────────────

function IncidentBanner({ incident }: { incident: IncidentDetail }) {
  const [copied, setCopied] = useState(false);
  const isDelay = incident.type === "shipment_delay";
  const payload = incident.payload as Record<string, string> | null;

  const title = isDelay ? "Shipment Delay" : "Worker Absence";
  const subtitle = isDelay
    ? [payload?.po_number, payload?.supplier_name].filter(Boolean).join(" · ")
    : [payload?.worker_name, payload?.site_id].filter(Boolean).join(" · ");

  const handleCopy = () => {
    navigator.clipboard.writeText(incident.correlation_id);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const severityColors: Record<string, string> = {
    critical: "bg-red-100 text-red-700",
    high: "bg-amber-100 text-amber-700",
    medium: "bg-yellow-100 text-yellow-700",
    low: "bg-muted text-muted-foreground",
  };

  const statusColors: Record<string, string> = {
    open: "bg-blue-50 text-blue-700",
    in_progress: "bg-blue-50 text-blue-700",
    resolved: "bg-[#5c6b55]/10 text-[#5c6b55]",
    escalated: "bg-red-50 text-red-700",
  };

  const TypeIcon = isDelay ? Truck : UserX;

  return (
    <div className="px-5 py-3 border-b border-border bg-card flex items-center gap-3 flex-shrink-0">
      <div className="w-8 h-8 rounded-lg bg-muted flex items-center justify-center flex-shrink-0">
        <TypeIcon className="w-4 h-4 text-muted-foreground" />
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <h1 className="font-heading text-base font-medium tracking-tight truncate">
            {title}
          </h1>
          {subtitle && (
            <>
              <span className="text-muted-foreground text-sm">·</span>
              <span className="text-sm text-muted-foreground truncate">
                {subtitle}
              </span>
            </>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2 flex-shrink-0">
        <span
          className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded ${severityColors[incident.severity] ?? severityColors.low}`}
        >
          {incident.severity}
        </span>
        <span
          className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded ${statusColors[incident.status] ?? "bg-muted text-muted-foreground"}`}
        >
          {incident.status.replace(/_/g, " ")}
        </span>
        {incident.source && (
          <span className="text-[10px] text-muted-foreground flex items-center gap-1 px-2 py-0.5 rounded bg-muted">
            <ExternalLink className="w-2.5 h-2.5" />
            {incident.source}
          </span>
        )}
        <button
          onClick={handleCopy}
          className="text-[10px] font-mono text-muted-foreground hover:text-foreground flex items-center gap-1 transition-colors"
          title="Copy correlation ID"
        >
          {copied ? (
            <Check className="w-3 h-3 text-[#5c6b55]" />
          ) : (
            <Copy className="w-3 h-3" />
          )}
          {incident.correlation_id.slice(0, 8)}
        </button>
      </div>
    </div>
  );
}

// ── Incident Stats Bar ──────────────────────────────────────────────

function IncidentStatsBar({ incident }: { incident: IncidentDetail }) {
  const payload = incident.payload as Record<string, string> | null;
  const originalEta = payload?.original_eta;
  const currentEta = payload?.current_eta;
  const etaChanged = originalEta && currentEta && originalEta !== currentEta;
  const [mountTime] = useState(() => Date.now());
  const elapsed = Math.floor(
    (mountTime - new Date(incident.created_at).getTime()) / 60000
  );
  const elapsedStr =
    elapsed < 60 ? `${elapsed}m` : `${Math.floor(elapsed / 60)}h ${elapsed % 60}m`;

  return (
    <div className="px-5 py-3 border-b border-border bg-card/50 flex items-center gap-6 flex-shrink-0">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Clock className="w-3.5 h-3.5" />
        <span>Open for <span className="font-semibold text-foreground tabular-nums">{elapsedStr}</span></span>
      </div>

      {etaChanged && (
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span>ETA shifted</span>
          <span className="font-medium text-amber-700 line-through">{originalEta}</span>
          <span>→</span>
          <span className="font-semibold text-foreground">{currentEta}</span>
        </div>
      )}

      {/* Quick counts */}
      <div className="ml-auto flex items-center gap-4">
        <StatPill
          count={incident.actions.filter((a) => a.status === "completed").length}
          label="done"
          color="text-[#5c6b55]"
        />
        <StatPill
          count={incident.actions.filter((a) =>
            a.status === "in_progress" || a.status === "queued"
          ).length}
          label="running"
          color="text-blue-600"
        />
        <StatPill
          count={incident.actions.filter((a) => a.status === "needs_approval").length}
          label="needs you"
          color="text-amber-600"
        />
        <StatPill
          count={incident.actions.filter((a) => a.status === "failed").length}
          label="failed"
          color="text-red-600"
        />
      </div>
    </div>
  );
}

function StatPill({
  count,
  label,
  color,
}: {
  count: number;
  label: string;
  color: string;
}) {
  if (count === 0) return null;
  return (
    <div className="flex items-center gap-1.5 text-xs">
      <span className={`font-semibold tabular-nums ${color}`}>{count}</span>
      <span className="text-muted-foreground">{label}</span>
    </div>
  );
}

// ── Empty State ──────────────────────────────────────────────────────

function EmptyState() {
  return (
    <main className="flex-1 min-w-0 bg-background flex flex-col">
      <div className="flex flex-col items-center justify-center h-full text-center px-8">
        <div className="w-14 h-14 rounded-2xl bg-[#5c6b55]/10 flex items-center justify-center mb-4">
          <Truck className="w-7 h-7 text-[#5c6b55]/60" />
        </div>
        <h2 className="font-heading text-xl font-medium tracking-tight mb-2">
          Supply Chain Operations
        </h2>
        <p className="text-sm text-muted-foreground max-w-sm leading-relaxed">
          Select an incident from the inbox to view its coordination
          workflow, take action, or approve pending steps.
        </p>
        <div className="mt-6 flex items-center gap-6 text-xs text-muted-foreground">
          <div className="flex items-center gap-1.5">
            <div className="w-2 h-2 rounded-full bg-amber-400" />
            <span>Awaiting your approval</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-2 h-2 rounded-full bg-[#5c6b55]" />
            <span>Steps complete</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-2 h-2 rounded-full bg-blue-400" />
            <span>In progress</span>
          </div>
        </div>
      </div>
    </main>
  );
}

// ── Main Page ────────────────────────────────────────────────────────

export default function Home() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [incident, setIncident] = useState<IncidentDetail | null>(null);

  const loadIncident = useCallback(async () => {
    if (!selectedId) return;
    try {
      const data = await fetchIncident(selectedId);
      setIncident(data);
    } catch {
      /* ignore */
    }
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    fetchIncident(selectedId)
      .then(setIncident)
      .catch(() => {});
  }, [selectedId]);

  return (
    <div className="flex h-screen bg-background overflow-hidden">
      {/* Nav Rail */}
      <NavRail />

      {/* Inbox */}
      <aside className="w-[280px] border-r border-border bg-card flex-shrink-0 flex flex-col min-h-0 overflow-hidden">
        <IncidentInbox selectedId={selectedId} onSelect={setSelectedId} />
      </aside>

      {selectedId && incident ? (
        <>
          {/* Center: Incident Workspace */}
          <main className="flex-1 min-w-0 min-h-0 flex flex-col bg-background overflow-hidden">
            {/* Incident banner */}
            <IncidentBanner incident={incident} />

            {/* Workflow pipeline */}
            <WorkflowPlaybook
              actions={incident.actions}
              incidentType={incident.type}
            />

            {/* Stats bar */}
            <IncidentStatsBar incident={incident} />

            {/* Approval gate (center stage) */}
            <div className="px-5 pt-4 shrink-0">
              <ApprovalQueue
                actions={incident.actions}
                incidentType={incident.type}
                onResolved={loadIncident}
              />
            </div>

            {/* Chat / Activity feed */}
            <div className="flex-1 min-h-0 overflow-hidden">
              <ChatPanel
                incidentId={selectedId}
                onActionExecuted={loadIncident}
              />
            </div>
          </main>

          {/* Right: Context panel */}
          <aside className="w-[340px] border-l border-border bg-card flex-shrink-0 flex flex-col min-h-0 overflow-hidden">
            <IncidentContextPanel
              incident={incident}
              onRefresh={loadIncident}
            />
          </aside>
        </>
      ) : (
        <EmptyState />
      )}
    </div>
  );
}
