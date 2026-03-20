import { Badge } from "@/components/ui/badge";
import type { ActionStatus, IncidentStatus, Severity } from "@/app/lib/types";

const incidentStatusConfig: Record<
  IncidentStatus,
  { label: string; className: string }
> = {
  open: {
    label: "Open",
    className: "bg-amber-50 text-amber-700 border-amber-200",
  },
  in_progress: {
    label: "In Progress",
    className: "bg-blue-50 text-blue-700 border-blue-200",
  },
  resolved: {
    label: "Resolved",
    className: "bg-[#eef2ed] text-[#5c6b55] border-[#c8d4c3]",
  },
  escalated: {
    label: "Escalated",
    className: "bg-red-50 text-red-700 border-red-200",
  },
};

const severityConfig: Record<Severity, { label: string; className: string }> = {
  low: { label: "Low", className: "bg-gray-50 text-gray-600 border-gray-200" },
  medium: {
    label: "Medium",
    className: "bg-amber-50 text-amber-700 border-amber-200",
  },
  high: {
    label: "High",
    className: "bg-orange-50 text-orange-700 border-orange-200",
  },
  critical: {
    label: "Critical",
    className: "bg-red-50 text-red-700 border-red-200",
  },
};

const actionStatusConfig: Record<
  ActionStatus,
  { label: string; className: string }
> = {
  pending: {
    label: "Pending",
    className: "bg-gray-50 text-gray-600 border-gray-200",
  },
  queued: {
    label: "Queued",
    className: "bg-blue-50 text-blue-600 border-blue-200",
  },
  in_progress: {
    label: "Running",
    className: "bg-blue-50 text-blue-700 border-blue-200",
  },
  completed: {
    label: "Completed",
    className: "bg-[#eef2ed] text-[#5c6b55] border-[#c8d4c3]",
  },
  failed: {
    label: "Failed",
    className: "bg-red-50 text-red-700 border-red-200",
  },
  needs_approval: {
    label: "Awaiting Approval",
    className: "bg-amber-50 text-amber-700 border-amber-200",
  },
  skipped: {
    label: "Skipped",
    className: "bg-gray-50 text-gray-400 border-gray-200",
  },
};

export function IncidentStatusBadge({ status }: { status: IncidentStatus }) {
  const config = incidentStatusConfig[status];
  return (
    <Badge variant="outline" className={`text-[11px] ${config.className}`}>
      {config.label}
    </Badge>
  );
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  const config = severityConfig[severity];
  return (
    <Badge variant="outline" className={`text-[11px] ${config.className}`}>
      {config.label}
    </Badge>
  );
}

export function ActionStatusBadge({ status }: { status: ActionStatus }) {
  const config = actionStatusConfig[status];
  return (
    <Badge variant="outline" className={`text-[11px] ${config.className}`}>
      {config.label}
    </Badge>
  );
}
