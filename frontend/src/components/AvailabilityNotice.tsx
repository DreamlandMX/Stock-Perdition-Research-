import type { ReactNode } from "react";
import { AlertTriangle, CircleSlash2, Info } from "lucide-react";

export type AvailabilityNoticeKind = "awaiting_model" | "unavailable" | "error";

interface AvailabilityNoticeProps {
  action?: ReactNode;
  children?: ReactNode;
  description: ReactNode;
  kind: AvailabilityNoticeKind;
  title: string;
}

const ICONS = {
  awaiting_model: CircleSlash2,
  unavailable: Info,
  error: AlertTriangle,
} satisfies Record<AvailabilityNoticeKind, typeof AlertTriangle>;

export function AvailabilityNotice({
  action,
  children,
  description,
  kind,
  title,
}: AvailabilityNoticeProps) {
  const Icon = ICONS[kind];
  return (
    <section
      aria-label={title}
      className={`availability-notice availability-notice--${kind}`}
      data-state-kind={kind}
      role={kind === "error" ? "alert" : "status"}
    >
      <Icon aria-hidden="true" size={19} strokeWidth={1.6} />
      <div className="availability-notice__content">
        <strong>{title}</strong>
        <p>{description}</p>
        {children}
      </div>
      {action == null ? null : (
        <div className="availability-notice__action">{action}</div>
      )}
    </section>
  );
}
