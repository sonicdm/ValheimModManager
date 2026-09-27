import { useEffect, useId, useRef, useState } from "react";

export type CategoryInfo = { name: string; count: number };

type Props = {
  mode: "include" | "exclude";
  options: CategoryInfo[];
  selected: string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
};

function FilterIcon({ mode }: { mode: "include" | "exclude" }) {
  return (
    <svg
      viewBox="0 0 16 16"
      className="h-3.5 w-3.5 shrink-0 text-bark/55"
      aria-hidden
      fill="currentColor"
    >
      <path d="M1.5 2.5h13l-4.5 5.2v4.3L6 13.5V7.7L1.5 2.5z" />
      {mode === "exclude" && (
        <path
          d="M10.2 1.2l4.6 4.6-.9.9-4.6-4.6.9-.9zM14.8 1.2l.9.9-4.6 4.6-.9-.9 4.6-4.6z"
          fill="currentColor"
        />
      )}
    </svg>
  );
}

export default function CategoryMultiSelect({ mode, options, selected, onChange, disabled }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  const label = mode === "include" ? "Include categories" : "Exclude categories";

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function toggle(name: string) {
    if (selected.includes(name)) onChange(selected.filter((c) => c !== name));
    else onChange([...selected, name]);
  }

  const summary =
    selected.length === 0
      ? label
      : selected.length <= 2
        ? selected.join(", ")
        : `${selected.length} selected`;

  return (
    <div className="relative min-w-[12rem]" ref={rootRef}>
      <button
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full min-w-[12rem] items-center gap-2 rounded-md border border-bark/20 bg-paper px-3 py-2 text-left text-sm text-bark hover:border-bark/40 disabled:opacity-60"
      >
        <FilterIcon mode={mode} />
        <span className="min-w-0 flex-1 truncate">{summary}</span>
        <span className="text-bark/40" aria-hidden>
          ▾
        </span>
      </button>
      {open && (
        <div
          id={listId}
          role="listbox"
          aria-label={label}
          className="absolute z-20 mt-1 max-h-72 w-72 overflow-auto rounded-md border border-bark/15 bg-paper shadow-lg"
        >
          <div className="sticky top-0 flex items-center justify-between border-b border-bark/10 bg-paper px-3 py-2 text-xs text-bark/60">
            <span>{label}</span>
            {selected.length > 0 && (
              <button
                type="button"
                className="text-sea hover:underline"
                onClick={() => onChange([])}
              >
                Clear
              </button>
            )}
          </div>
          {options.length === 0 ? (
            <p className="px-3 py-3 text-xs text-bark/50">No categories loaded yet.</p>
          ) : (
            options.map((opt) => {
              const checked = selected.includes(opt.name);
              return (
                <label
                  key={opt.name}
                  className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm hover:bg-mist"
                >
                  <input
                    type="checkbox"
                    className="rounded border-bark/30"
                    checked={checked}
                    onChange={() => toggle(opt.name)}
                  />
                  <span className="min-w-0 flex-1 truncate">{opt.name}</span>
                  <span className="tabular-nums text-xs text-bark/45">{opt.count}</span>
                </label>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
