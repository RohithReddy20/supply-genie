"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { sendChatMessage } from "@/app/lib/api";
import type { ChatMessage, ProposedAction } from "@/app/lib/types";
import {
  Send,
  Plus,
  Type,
  Smile,
  AtSign,
  Video,
  Mic,
  PenLine,
  ShieldCheck,
} from "lucide-react";

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

function ProposedActionCard({
  action,
}: {
  action: ProposedAction;
  onApprove?: (action: ProposedAction) => void;
  approving?: boolean;
}) {
  return (
    <div className="flex items-center gap-3 w-full px-3 py-2.5 bg-card border border-amber-200 rounded-lg mt-2 text-left">
      <div className="w-7 h-7 rounded-full bg-amber-100 flex items-center justify-center flex-shrink-0">
        <ShieldCheck className="w-3.5 h-3.5 text-amber-600" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-semibold text-foreground">
          {action.label}
        </div>
        <div className="text-xs text-muted-foreground mt-0.5">
          Requires approval — review in the action timeline
        </div>
      </div>
    </div>
  );
}

export function ChatPanel({
  incidentId,
  onActionExecuted,
}: {
  incidentId: string;
  onActionExecuted?: () => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const prevIncidentId = useRef(incidentId);

  // Reset messages when incident changes
  useEffect(() => {
    if (prevIncidentId.current !== incidentId) {
      setMessages([]);
      prevIncidentId.current = incidentId;
    }
  }, [incidentId]);

  // Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;

    const userMsg: ChatMessage = {
      role: "user",
      content: text,
      timestamp: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setSending(true);

    try {
      const history = messages.map((m) => ({
        role: m.role,
        content: m.content,
      }));

      const res = await sendChatMessage(incidentId, text, history);

      const assistantMsg: ChatMessage = {
        role: "assistant",
        content: res.reply,
        timestamp: new Date().toISOString(),
        proposed_actions:
          res.proposed_actions.length > 0 ? res.proposed_actions : undefined,
      };

      setMessages((prev) => [...prev, assistantMsg]);
      onActionExecuted?.(); // Refresh timeline — agent may have executed actions
    } catch {
      const errorMsg: ChatMessage = {
        role: "assistant",
        content: "Sorry, I encountered an error. Please try again.",
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  }, [input, sending, messages, incidentId, onActionExecuted]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Decorative toolbar icons (non-functional, matching HappyRobot's UI)
  const toolbarIcons = [Plus, Type, Smile, AtSign, Video, Mic, PenLine];

  return (
    <div className="flex flex-col h-full bg-card">
      {/* Messages area */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center py-6">
            <div className="w-9 h-9 rounded-md bg-[#5c6b55] flex items-center justify-center mb-3">
              <span className="text-sm font-bold text-white">H</span>
            </div>
            <p className="text-sm text-muted-foreground max-w-[280px] leading-relaxed">
              Ask about this incident, request status updates, or issue commands
              like &quot;call production&quot; or &quot;email the customer&quot;.
            </p>
          </div>
        )}

        {messages.map((msg, idx) => {
          const isBot = msg.role === "assistant";
          return (
            <div key={idx} className="flex gap-2.5">
              {/* Avatar */}
              {isBot ? (
                <div className="w-8 h-8 rounded-md bg-[#5c6b55] flex items-center justify-center flex-shrink-0 mt-0.5">
                  <span className="text-xs font-bold text-white">H</span>
                </div>
              ) : (
                <div className="w-8 h-8 rounded-full bg-[#e07a5f] flex items-center justify-center flex-shrink-0 mt-0.5">
                  <span className="text-xs font-bold text-white">J</span>
                </div>
              )}

              {/* Name + time + message */}
              <div className="flex-1 min-w-0">
                <div className="flex items-baseline gap-2">
                  <span className="text-sm font-semibold text-foreground">
                    {isBot ? "Happyrobot" : "Jordan"}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {formatTime(msg.timestamp)}
                  </span>
                </div>
                <div className="text-sm text-foreground mt-0.5 leading-relaxed whitespace-pre-wrap">
                  {msg.content}
                </div>

                {/* Approval-gated action cards — inline below message */}
                {msg.proposed_actions?.map((action) => (
                  <ProposedActionCard
                    key={action.action_type}
                    action={action}
                    onApprove={() => {}}
                    approving={false}
                  />
                ))}
              </div>
            </div>
          );
        })}

        {/* Typing indicator */}
        {sending && (
          <div className="flex gap-2.5">
            <div className="w-8 h-8 rounded-md bg-[#5c6b55] flex items-center justify-center flex-shrink-0">
              <span className="text-xs font-bold text-white">H</span>
            </div>
            <div className="pt-2.5">
              <div className="flex gap-1">
                <div className="w-1.5 h-1.5 rounded-full bg-muted-foreground/40 animate-bounce" style={{ animationDelay: "0ms" }} />
                <div className="w-1.5 h-1.5 rounded-full bg-muted-foreground/40 animate-bounce" style={{ animationDelay: "150ms" }} />
                <div className="w-1.5 h-1.5 rounded-full bg-muted-foreground/40 animate-bounce" style={{ animationDelay: "300ms" }} />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Input bar — matches HappyRobot's "Message Happy Robot" input */}
      <div className="px-4 pb-3 pt-0">
        <div className="border border-border rounded-xl bg-card overflow-hidden focus-within:border-[#5c6b55]/40 transition-colors">
          {/* Text input */}
          <div className="px-4 py-2.5">
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Message Happy Robot"
              disabled={sending}
              className="w-full bg-transparent text-sm placeholder:text-muted-foreground/50 focus:outline-none disabled:opacity-50"
            />
          </div>

          {/* Toolbar row */}
          <div className="flex items-center justify-between px-3 py-1.5 border-t border-border/50">
            <div className="flex items-center gap-0.5">
              {toolbarIcons.map((Icon, i) => (
                <button
                  key={i}
                  type="button"
                  tabIndex={-1}
                  className="p-1.5 rounded hover:bg-muted transition-colors text-muted-foreground/50 hover:text-muted-foreground"
                >
                  <Icon className="w-4 h-4" />
                </button>
              ))}
            </div>
            <button
              onClick={handleSend}
              disabled={!input.trim() || sending}
              className="p-1.5 rounded-md text-muted-foreground/40 hover:text-[#5c6b55] disabled:hover:text-muted-foreground/40 transition-colors disabled:opacity-40"
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
