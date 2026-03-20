"use client";

import type { ActionRun, ActionStatus } from "@/app/lib/types";
import { ActionStatusBadge } from "./status-badge";
import {
  MessageSquare,
  Phone,
  FileText,
  Mail,
  Users,
  AlertTriangle,
  HardHat,
  Wrench,
} from "lucide-react";

const ACTION_META: Record<
  string,
  { label: string; icon: React.ElementType; description: string }
> = {
  slack_notify: {
    label: "Slack notification",
    icon: MessageSquare,
    description: "Shipment status",
  },
  call_production: {
    label: "Call production",
    icon: Phone,
    description: "Confirm status",
  },
  call_contractor: {
    label: "Call contractors",
    icon: HardHat,
    description: "Find replacement",
  },
  update_po: {
    label: "Update PO documents",
    icon: FileText,
    description: "Update documentation",
  },
  update_labor: {
    label: "Update system",
    icon: Wrench,
    description: "Labor planning",
  },
  email_customer: {
    label: "Email customer",
    icon: Mail,
    description: "Share update",
  },
  notify_manager: {
    label: "Notify site manager",
    icon: Users,
    description: "Workforce update",
  },
  escalate_ticket: {
    label: "Escalate ticket",
    icon: AlertTriangle,
    description: "Create support ticket",
  },
};

function statusDotColor(status: ActionStatus): string {
  switch (status) {
    case "completed":
      return "bg-[#5c6b55]";
    case "in_progress":
    case "queued":
      return "bg-blue-500 pulse-olive";
    case "failed":
      return "bg-red-500";
    case "needs_approval":
      return "bg-amber-500";
    case "skipped":
      return "bg-gray-300";
    default:
      return "bg-gray-300";
  }
}

function formatTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

export function ActionTimeline({
  actions,
  onApprove,
  onReject,
}: {
  actions: ActionRun[];
  onApprove?: (approvalId: string) => void;
  onReject?: (approvalId: string) => void;
}) {
  const sorted = [...actions].sort((a, b) => a.sequence - b.sequence);

  return (
    <div className="space-y-0">
      {sorted.map((action, idx) => {
        const meta = ACTION_META[action.action_type] ?? {
          label: action.action_type,
          icon: AlertTriangle,
          description: "",
        };
        const Icon = meta.icon;
        const isLast = idx === sorted.length - 1;

        return (
          <div key={action.id} className="relative flex items-start gap-4 pb-6">
            {/* Connector line */}
            {!isLast && (
              <div className="absolute left-[15px] top-[36px] bottom-0 w-[2px] bg-border" />
            )}

            {/* Status dot */}
            <div className="relative z-10 flex-shrink-0 mt-1">
              <div
                className={`w-[32px] h-[32px] rounded-full flex items-center justify-center ${statusDotColor(action.status)}`}
              >
                <Icon className="w-4 h-4 text-white" strokeWidth={2} />
              </div>
            </div>

            {/* Card */}
            <div className="flex-1 bg-card border border-border rounded-lg px-4 py-3 shadow-sm">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-sm text-foreground">
                    {meta.label}
                  </span>
                  <span className="text-muted-foreground text-sm">
                    · {meta.description}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  {action.completed_at && (
                    <span className="text-xs text-muted-foreground">
                      {formatTime(action.completed_at)}
                    </span>
                  )}
                  <span className="text-xs text-muted-foreground font-mono">
                    {action.sequence}
                  </span>
                </div>
              </div>

              <div className="flex items-center gap-2 mt-1.5">
                <ActionStatusBadge status={action.status} />
                {action.retry_count > 0 && (
                  <span className="text-xs text-muted-foreground">
                    {action.retry_count} retries
                  </span>
                )}
                {action.error_message && (
                  <span className="text-xs text-red-600 truncate max-w-[200px]">
                    {action.error_message}
                  </span>
                )}
              </div>

              {/* Approval controls */}
              {action.status === "needs_approval" && action.approval && (
                <div className="flex items-center gap-2 mt-3 pt-3 border-t border-border">
                  <span className="text-xs text-muted-foreground">
                    Requires operator approval
                  </span>
                  <div className="ml-auto flex gap-2">
                    <button
                      onClick={() => onApprove?.(action.approval!.id)}
                      className="px-3 py-1 text-xs font-medium bg-[#5c6b55] text-white rounded-md hover:bg-[#4a5945] transition-colors"
                    >
                      Approve
                    </button>
                    <button
                      onClick={() => onReject?.(action.approval!.id)}
                      className="px-3 py-1 text-xs font-medium bg-white text-foreground border border-border rounded-md hover:bg-muted transition-colors"
                    >
                      Reject
                    </button>
                  </div>
                </div>
              )}

              {/* Approved/rejected info */}
              {action.approval &&
                action.approval.status !== "pending" && (
                  <div className="mt-2 text-xs text-muted-foreground">
                    {action.approval.status === "approved" ? "✓ Approved" : "✗ Rejected"}
                    {action.approval.decided_by && (
                      <> by {action.approval.decided_by}</>
                    )}
                    {action.approval.reason && (
                      <> — {action.approval.reason}</>
                    )}
                  </div>
                )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
