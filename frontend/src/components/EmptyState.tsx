import { SearchX } from "lucide-react";

export interface EmptyStateProps {
  description: string;
  title: string;
}

export function EmptyState({ description, title }: EmptyStateProps) {
  return (
    <section aria-label={title} className="empty-state" role="status">
      <SearchX aria-hidden="true" size={20} strokeWidth={1.5} />
      <div>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
    </section>
  );
}
