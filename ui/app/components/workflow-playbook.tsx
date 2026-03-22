"use client";

import type { ActionRun, ActionType } from "@/app/lib/types";
import {
  MessageSquare,
  Phone,
  FileText,
  Mail,
  Users,
  AlertTriangle,
  HardHat,
  Wrench,
  Lock,
} from "lucide-react";

const ACTION_META: Record<
  string,
  { label: string; icon: React.ElementType; description: string; requiresApproval?: boolean }
> = {
  slack_notify: {
    label: "Notify ops channel",
    icon: MessageSquare,
    description: "Send Slack alert about the incident to the ops channel",
  },
  call_production: {
    label: "Call supplier",
    icon: Phone,
    description: "Call supplier production line for updated ETA",
  },
  call_contractor: {
    label: "Call agency",
    icon: HardHat,
    description: "Call contractor agency for replacement worker",
  },
  update_po: {
    label: "Amend PO",
    icon: FileText,
    description: "Amend purchase order in ERP/TMS system",
  },
  update_labor: {
    label: "Update roster",
    icon: Wrench,
    description: "Update labor planning system with shift changes",
  },
  email_customer: {
    label: "Email customer",
    icon: Mail,
    description: "Email customer with revised delivery timeline",
    requiresApproval: true,
  },
  notify_manager: {
    label: "Alert manager",
    icon: Users,
    description: "Notify site manager via Slack or email",
  },
  escalate_ticket: {
    label: "Escalate",
    icon: AlertTriangle,
    description: "Create support ticket for manual follow-up",
  },
};

function StepState({ status }: { status: string }) {
  const map: Record<string, string> = {
    completed: "text-[#5c6b55]",
    in_progress: "text-blue-600",
    queued: "text-blue-600",
    failed: "text-red-600",
    needs_approval: "text-amber-600",
    skipped: "text-gray-400",
    pending: "text-gray-400",
  };
  const labelMap: Record<string, string> = {
    completed: "Done",
    in_progress: "Running",
    queued: "Running",
    failed: "Failed",
    needs_approval: "Awaiting You",
    skipped: "Skipped",
    pending: "Pending",
  };
  return (
    <span className={`text-[9px] font-semibold uppercase tracking-widest ${map[status] ?? map.pending}`}>
      {labelMap[status] ?? "Pending"}
    </span>
  );
}

function Connector({ done }: { done: boolean }) {
  return (
    <div className="flex items-center justify-center w-10 flex-shrink-0">
      <div className={`h-[2px] w-full transition-colors ${done ? "bg-[#5c6b55]" : "bg-border"}`} />
    </div>
  );
}

function PlaybookStep({
  meta,
  status,
  isFirst,
  isLast,
  currentStepIndex,
  stepIndex,
}: {
  meta: { label: string; icon: React.ElementType; description: string; requiresApproval?: boolean };
  status: string;
  isFirst: boolean;
  isLast: boolean;
  currentStepIndex: number;
  stepIndex: number;
}) {
  const isCurrent = stepIndex === currentStepIndex;
  const isPast = stepIndex < currentStepIndex;
  const isFuture = stepIndex > currentStepIndex;

  const nodeClasses: Record<string, string> = {
    completed: "bg-[#5c6b55]/10 border-2 border-[#5c6b55]",
    in_progress: "bg-blue-50 border-2 border-blue-400 animate-pulse",
    queued: "bg-blue-50 border-2 border-blue-200",
    failed: "bg-red-50 border-2 border-red-400",
    needs_approval: "bg-amber-50 border-2 border-amber-400",
    skipped: "bg-muted border-2 border-border",
    pending: isCurrent ? "bg-muted border-2 border-[#5c6b55]/40" : "bg-muted border-2 border-border",
  };

  return (
    <div className="flex items-start flex-1 min-w-0">
      {!isFirst && <Connector done={isPast} />}

      <div className="flex flex-col items-center gap-1.5 flex-shrink-0">
        <div className={`relative w-11 h-11 rounded-full flex items-center justify-center transition-all ${nodeClasses[status] ?? nodeClasses.pending}`}>
          <meta.icon
            className={`w-4 h-4 ${isFuture ? "text-muted-foreground/40" : "text-muted-foreground"}`}
          />
          {/* Approval gate lock icon */}
          {meta.requiresApproval && (
            <div className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-amber-100 border border-amber-300 flex items-center justify-center">
              <Lock className="w-2 h-2 text-amber-600" />
            </div>
          )}
        </div>
        <p className={`text-[10px] font-medium leading-tight text-center max-w-[80px] ${isFuture ? "text-muted-foreground/60" : "text-foreground"}`}>
          {meta.label}
        </p>
        <StepState status={status} />
      </div>

      {!isLast && <Connector done={isPast || status === "completed"} />}
    </div>
  );
}

const WORKFLOW_STEPS: Record<string, ActionType[]> = {
  shipment_delay: [
    "slack_notify",
    "call_production",
    "update_po",
    "email_customer",
  ],
  worker_absence: [
    "update_labor",
    "call_contractor",
    "notify_manager",
  ],
};

export function WorkflowPlaybook({
  actions,
  incidentType,
}: {
  actions: ActionRun[];
  incidentType: "shipment_delay" | "worker_absence";
}) {
  const stepTypes = WORKFLOW_STEPS[incidentType] ?? [];
  const runByType = new Map(actions.map((a) => [a.action_type, a]));

  const completedCount = actions.filter((a) => a.status === "completed").length;
  const maxIndex = Math.max(
    ...actions
      .filter((a) => a.status !== "pending" && a.status !== "skipped")
      .map((a) => stepTypes.indexOf(a.action_type)),
    -1
  );
  const currentStepIndex = Math.max(0, maxIndex + 1);
  const hasBlocking = actions.some((a) => a.status === "needs_approval");
  const hasFailed = actions.some((a) => a.status === "failed");

  const playbook_label = incidentType === "shipment_delay"
    ? "Shipment Delay Playbook"
    : "Worker Absence Playbook";

  return (
    <div className="px-5 py-4 border-b border-border">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <div className="w-5 h-5 rounded bg-[#5c6b55] flex items-center justify-center">
            <span className="text-[9px] font-bold text-white">W</span>
          </div>
          <div>
            <h3 className="text-xs font-semibold text-foreground">
              {playbook_label}
            </h3>
            <p className="text-[10px] text-muted-foreground">
              {completedCount}/{stepTypes.length} steps complete
              {hasBlocking && (
                <span className="ml-2 text-amber-600 font-medium">
                  · Approval required
                </span>
              )}
              {hasFailed && (
                <span className="ml-2 text-red-600 font-medium">
                  · Action failed
                </span>
              )}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <div className="w-32 h-1.5 bg-muted rounded-full overflow-hidden">
            <div
              className="h-full bg-[#5c6b55] rounded-full transition-all duration-500"
              style={{ width: `${stepTypes.length > 0 ? (completedCount / stepTypes.length) * 100 : 0}%` }}
            />
          </div>
          <span className="text-[10px] text-muted-foreground tabular-nums">
            {stepTypes.length > 0 ? Math.round((completedCount / stepTypes.length) * 100) : 0}%
          </span>
        </div>
      </div>

      <div className="flex items-start overflow-x-auto pb-1">
        {stepTypes.map((actionType, idx) => {
          const run = runByType.get(actionType);
          const meta = ACTION_META[actionType];
          const status = run?.status ?? "pending";
          return (
            <PlaybookStep
              key={actionType}
              meta={meta}
              status={status}
              isFirst={idx === 0}
              isLast={idx === stepTypes.length - 1}
              currentStepIndex={currentStepIndex}
              stepIndex={idx}
            />
          );
        })}
      </div>
    </div>
  );
}
