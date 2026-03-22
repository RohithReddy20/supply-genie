"use client";

import { useState } from "react";
import { decideApproval } from "@/app/lib/api";
import type { ActionRun } from "@/app/lib/types";
import {
  ShieldCheck,
  CheckCircle2,
  XCircle,
  Clock,
  Truck,
} from "lucide-react";

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

const ACTION_DESCRIPTIONS: Record<string, string> = {
  email_customer:
    "Sends a revised delivery timeline directly to the customer. This is a customer-facing action — please verify the content before approving.",
  slack_notify:
    "Posts an alert to the ops Slack channel. Low risk — confirms the incident was received.",
  call_production:
    "Initiates an outbound call to the supplier's production line. Confirms updated ETA.",
  update_po:
    "Amends the purchase order document in the ERP. Updates version and logs notes.",
  call_contractor:
    "Calls the contractor agency to request a replacement worker. Connects directly.",
  update_labor:
    "Updates the labor planning system with the absence and adjusted roster.",
  notify_manager:
    "Sends a notification to the site manager via Slack or email.",
  escalate_ticket:
    "Creates a support ticket in the ticketing system for manual follow-up.",
};

export function ApprovalTaskCard({
  actionRun,
  incidentType,
  onResolved,
}: {
  actionRun: ActionRun;
  incidentType: "shipment_delay" | "worker_absence";
  onResolved: () => void;
}) {
  const [loading, setLoading] = useState<string | null>(null);
  const [reason, setReason] = useState("");

  const handleDecision = async (decision: "approved" | "rejected") => {
    if (!actionRun.approval) return;
    setLoading(decision);
    try {
      await decideApproval(
        actionRun.approval.id,
        decision,
        "ops@console",
        decision === "rejected" ? reason : "Approved via ops console"
      );
      onResolved();
    } finally {
      setLoading(null);
    }
  };

  const isCustomerFacing =
    actionRun.action_type === "email_customer" ||
    actionRun.action_type === "notify_manager";

  return (
    <div
      className={`
        rounded-lg border p-4
        ${
          isCustomerFacing
            ? "border-amber-300 bg-amber-50/50"
            : "border-olive/30 bg-[#5c6b55]/5"
        }
      `}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2.5">
          <div
            className={`
              w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5
              ${
                isCustomerFacing
                  ? "bg-amber-100 text-amber-600"
                  : "bg-[#5c6b55]/10 text-[#5c6b55]"
              }
            `}
          >
            <ShieldCheck className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h4 className="text-sm font-semibold text-foreground">
                {actionRun.action_type === "email_customer"
                  ? "Customer Email — Review Required"
                  : `Action: ${actionRun.action_type.replace(/_/g, " ")}`}
              </h4>
            </div>
            <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
              {ACTION_DESCRIPTIONS[actionRun.action_type] ??
                "This action is waiting for operator approval before it can proceed."}
            </p>
            <div className="flex items-center gap-3 mt-1.5">
              <span className="text-[10px] text-muted-foreground flex items-center gap-1">
                <Truck className="w-3 h-3" />
                {incidentType === "shipment_delay"
                  ? "Shipment Delay"
                  : "Worker Absence"}
              </span>
              <span className="text-[10px] text-muted-foreground flex items-center gap-1">
                <Clock className="w-3 h-3" />
                Requested {timeAgo(actionRun.approval?.requested_at ?? "")}
              </span>
            </div>
          </div>
        </div>

        {/* Decision buttons */}
        <div className="flex flex-col gap-1.5 flex-shrink-0">
          <button
            onClick={() => handleDecision("approved")}
            disabled={loading !== null}
            className={`
              flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold
              transition-colors disabled:opacity-50
              ${
                isCustomerFacing
                  ? "bg-amber-500 text-white hover:bg-amber-600"
                  : "bg-[#5c6b55] text-white hover:bg-[#4a5945]"
              }
            `}
          >
            {loading === "approved" ? (
              <div className="w-3 h-3 border border-white border-t-transparent rounded-full animate-spin" />
            ) : (
              <CheckCircle2 className="w-3 h-3" />
            )}
            Approve
          </button>
          <button
            onClick={() => handleDecision("rejected")}
            disabled={loading !== null}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border border-border bg-white text-foreground hover:bg-muted transition-colors disabled:opacity-50"
          >
            {loading === "rejected" ? (
              <div className="w-3 h-3 border border-muted-foreground border-t-transparent rounded-full animate-spin" />
            ) : (
              <XCircle className="w-3 h-3" />
            )}
            Reject
          </button>
        </div>
      </div>

      {/* Reject reason (shown after clicking reject) */}
      {loading === "rejected" && (
        <div className="mt-3 pt-3 border-t border-border">
          <label className="text-xs text-muted-foreground">
            Reason for rejection (optional):
          </label>
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g., Wrong contact email, timing not right..."
            className="mt-1 w-full px-2.5 py-1.5 text-xs border border-border rounded-md bg-card focus:outline-none focus:border-[#5c6b55]/40"
          />
          <button
            onClick={() => setLoading(null)}
            className="mt-2 px-3 py-1 text-xs border border-border rounded-md hover:bg-muted"
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}

export function ApprovalQueue({
  actions,
  incidentType,
  onResolved,
}: {
  actions: ActionRun[];
  incidentType: "shipment_delay" | "worker_absence";
  onResolved: () => void;
}) {
  const pending = actions.filter((a) => a.status === "needs_approval" && a.approval);

  if (pending.length === 0) return null;

  return (
    <div className="space-y-2">
      {pending.map((action) => (
        <ApprovalTaskCard
          key={action.id}
          actionRun={action}
          incidentType={incidentType}
          onResolved={onResolved}
        />
      ))}
    </div>
  );
}
