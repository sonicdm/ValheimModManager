import { useEffect, useId, useRef, useState } from "react";

type MenuItem = {
  key: string;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  danger?: boolean;
  title?: string;
};

export default function RowMenu({
  items,
  disabled,
  label = "More actions",
}: {
  items: MenuItem[];
  disabled?: boolean;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

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

  if (!items.length) return null;

  return (
    <div className="relative inline-block" ref={rootRef}>
      <button
        type="button"
        className="btn-ghost min-w-[2rem] px-2 font-semibold tracking-widest"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={label}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
      >
        ···
      </button>
      {open && (
        <div
          id={menuId}
          role="menu"
          className="absolute right-0 z-30 mt-1 min-w-[11rem] rounded-lg border border-bark/15 bg-paper py-1 shadow-lg"
        >
          {items.map((item) => (
            <button
              key={item.key}
              type="button"
              role="menuitem"
              title={item.title}
              disabled={item.disabled || disabled}
              className={`block w-full px-3 py-2 text-left text-xs transition-colors hover:bg-mist disabled:opacity-50 ${
                item.danger ? "text-danger" : "text-bark"
              }`}
              onClick={() => {
                setOpen(false);
                item.onClick();
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
