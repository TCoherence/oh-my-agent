import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Minimal AI-Elements-style ``Conversation`` shell. The Vercel
 * ai-elements package ships richer features (auto-scroll button, virtual
 * lists). We reproduce just the structural primitives — the chat page
 * doesn't need the rest for the MVP, and avoiding the dependency keeps
 * the install lean.
 */
export function Conversation({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "flex flex-col gap-4 overflow-y-auto px-4 py-6",
        className,
      )}
      {...props}
    />
  );
}

export function ConversationContent({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("flex flex-col gap-4", className)} {...props} />
  );
}

export function ConversationEmptyState({
  title,
  description,
}: {
  title: string;
  description?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center text-muted-foreground">
      <p className="text-sm font-medium text-foreground">{title}</p>
      {description ? <p className="mt-2 text-xs">{description}</p> : null}
    </div>
  );
}
