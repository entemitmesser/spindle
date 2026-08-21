import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";

interface LangListInputProps {
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
}

/** Comma/space-separated ISO 639 language code editor (`deu, eng` style —
 * matches how these lists render in RuntimeConfig's defaults). */
export function LangListInput({ value, onChange, placeholder }: LangListInputProps) {
  const [draft, setDraft] = useState("");

  function commit() {
    const codes = draft
      .split(/[\s,]+/)
      .map((c) => c.trim().toLowerCase())
      .filter(Boolean);
    if (codes.length > 0) {
      onChange([...value, ...codes.filter((c) => !value.includes(c))]);
    }
    setDraft("");
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {value.map((code) => (
          <Badge key={code} variant="muted" className="gap-1">
            {code}
            <button
              type="button"
              onClick={() => onChange(value.filter((c) => c !== code))}
              aria-label={`Remove ${code}`}
              className="hover:text-foreground"
            >
              ×
            </button>
          </Badge>
        ))}
      </div>
      <Input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            commit();
          }
        }}
        placeholder={placeholder ?? "e.g. deu, eng"}
      />
    </div>
  );
}
