import { PropsWithChildren } from "react";

export function SectionCard({
  title,
  subtitle,
  tone = "default",
  className = "",
  children
}: PropsWithChildren<{
  title: string;
  subtitle?: string;
  tone?: "default" | "intake" | "analysis" | "review" | "draft" | "publish";
  className?: string;
}>) {
  return (
    <section className={`section-card section-card--${tone} ${className}`.trim()}>
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
