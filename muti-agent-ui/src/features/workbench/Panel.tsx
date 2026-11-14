import type { ReactNode } from 'react';

export function Panel({
  title,
  extra,
  children,
  className,
}: {
  title: string;
  extra?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className ?? ''}`}>
      <header className="panel__head">
        <h3 className="panel__title">{title}</h3>
        {extra && <div className="panel__extra">{extra}</div>}
      </header>
      <div className="panel__body">{children}</div>
    </section>
  );
}
