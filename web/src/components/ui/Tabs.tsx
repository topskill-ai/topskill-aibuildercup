import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface TabItem<V extends string = string> {
  value: V;
  label: ReactNode;
  /** Small count/indicator after the label. */
  badge?: ReactNode;
  disabled?: boolean;
}

export interface TabsProps<V extends string> {
  items: TabItem<V>[];
  value: V;
  onValueChange: (value: V) => void;
  className?: string;
  /** Used to link tabs and panels: panel id = `${idBase}-panel-${value}`. */
  idBase?: string;
  "aria-label"?: string;
}

/** Underline tab bar; scrolls horizontally inside itself on narrow screens. Arrow keys move focus. */
export function Tabs<V extends string>({ items, value, onValueChange, className, idBase, ...aria }: TabsProps<V>) {
  const auto = useId();
  const base = idBase ?? auto;
  const refs = useRef<Array<HTMLButtonElement | null>>([]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const enabled = items.map((it, i) => (it.disabled ? -1 : i)).filter((i) => i >= 0);
    const current = items.findIndex((it) => it.value === value);
    const pos = enabled.indexOf(current);
    let next = pos;
    if (e.key === "ArrowLeft") next = (pos - 1 + enabled.length) % enabled.length;
    if (e.key === "ArrowRight") next = (pos + 1) % enabled.length;
    if (e.key === "Home") next = 0;
    if (e.key === "End") next = enabled.length - 1;
    const idx = enabled[next];
    onValueChange(items[idx].value);
    refs.current[idx]?.focus();
  };

  return (
    <div className={cn("-mx-1 overflow-x-auto border-b border-border", className)}>
      <div role="tablist" aria-label={aria["aria-label"]} className="flex min-w-max gap-1 px-1" onKeyDown={onKeyDown}>
        {items.map((item, i) => {
          const selected = item.value === value;
          return (
            <button
              key={item.value}
              ref={(el) => {
                refs.current[i] = el;
              }}
              id={`${base}-tab-${item.value}`}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={`${base}-panel-${item.value}`}
              tabIndex={selected ? 0 : -1}
              disabled={item.disabled}
              onClick={() => onValueChange(item.value)}
              className={cn(
                "relative -mb-px inline-flex h-10 items-center gap-1.5 border-b-2 px-3 text-sm font-medium transition-colors duration-150",
                selected ? "border-primary text-text" : "border-transparent text-muted hover:text-text",
                "disabled:opacity-50",
              )}
            >
              {item.label}
              {item.badge != null ? (
                <span className="rounded-chip bg-mist px-1.5 text-xs leading-5 text-muted">{item.badge}</span>
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function TabPanel({
  idBase,
  value,
  children,
  className,
}: {
  idBase: string;
  value: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      role="tabpanel"
      id={`${idBase}-panel-${value}`}
      aria-labelledby={`${idBase}-tab-${value}`}
      className={cn("pt-5", className)}
    >
      {children}
    </div>
  );
}
