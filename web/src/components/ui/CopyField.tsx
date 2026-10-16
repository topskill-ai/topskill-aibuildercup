import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { cn } from "@/lib/cn";

export interface CopyFieldProps {
  value: string;
  label?: string;
  /** Monospace value (URLs, ids, tokens). Default true. */
  mono?: boolean;
  className?: string;
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    } catch {
      return false;
    }
  }
}

/** Read-only value with a copy button (agent URLs, Agent Card URL, tokens). */
export function CopyField({ value, label, mono = true, className }: CopyFieldProps) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(t);
  }, [copied]);

  const onCopy = async () => {
    if (await copyText(value)) {
      setCopied(true);
      toast.success(label ? `${label} copied` : "Copied");
    } else {
      toast.error("Could not copy. Select the text and copy it manually.");
    }
  };

  return (
    <div className={cn("min-w-0", className)}>
      {label ? <div className="mb-1.5 text-sm font-medium text-text">{label}</div> : null}
      <div className="flex min-w-0 items-stretch rounded-control border border-border bg-sunken">
        <input
          readOnly
          value={value}
          aria-label={label ?? "Value"}
          onFocus={(e) => e.currentTarget.select()}
          className={cn(
            "min-w-0 flex-1 truncate bg-transparent px-3 py-2 text-sm text-text outline-none",
            mono && "font-mono text-[13px]",
          )}
        />
        <button
          type="button"
          onClick={onCopy}
          aria-label={copied ? "Copied" : `Copy ${label ?? "value"}`}
          className="flex shrink-0 items-center gap-1.5 border-l border-border px-3 text-sm font-medium text-muted hover:bg-mist hover:text-text"
        >
          {copied ? <Check className="h-4 w-4 text-success-text" /> : <Copy className="h-4 w-4" />}
          <span className="hidden sm:inline">{copied ? "Copied" : "Copy"}</span>
        </button>
      </div>
    </div>
  );
}
