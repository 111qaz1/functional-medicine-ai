import { PropsWithChildren } from "react";

export function SectionCard({
  title,
  subtitle,
  children
}: PropsWithChildren<{ title: string; subtitle?: string }>) {
  return (
    <section className="section-card">
      <div className="section-card__head">
        <div>
          <p className="section-card__eyebrow">{subtitle ?? "Internal workspace"}</p>
          <h2>{title}</h2>
        </div>
      </div>
      {children}
    </section>
  );
}

