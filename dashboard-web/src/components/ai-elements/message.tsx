import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

type MessageRole = "user" | "assistant" | "system";

interface MessageProps {
  role: MessageRole;
  children: ReactNode;
  className?: string;
}

/**
 * Message bubble container. Aligns user right / assistant left / system
 * centered, matching the agentara visual reference. Styling uses the
 * shadcn-style design tokens so dark/light mode flips cleanly.
 */
export function Message({ role, children, className }: MessageProps) {
  const alignment =
    role === "user"
      ? "self-end"
      : role === "system"
        ? "self-center"
        : "self-start";
  return (
    <div className={cn("flex w-full", role === "user" ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[80%] rounded-lg px-4 py-2 text-sm",
          role === "user" && "bg-primary text-primary-foreground",
          role === "assistant" && "bg-muted",
          role === "system" && "bg-accent/40 italic text-muted-foreground text-xs",
          alignment,
          className,
        )}
      >
        {children}
      </div>
    </div>
  );
}

export function MessageContent({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn("whitespace-pre-wrap break-words leading-relaxed", className)}
    >
      {children}
    </div>
  );
}

export function MessageMeta({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mt-1 text-[10px] uppercase tracking-wider opacity-60", className)}>
      {children}
    </div>
  );
}
