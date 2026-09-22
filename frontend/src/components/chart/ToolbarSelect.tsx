import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal } from "react-dom";

export interface ToolbarSelectOption<K extends string> {
  key: K;
  label: string;
  title?: string;
  /** A divider is drawn wherever this changes between neighbours. */
  group?: string;
}

interface Props<K extends string> {
  value: K;
  options: ToolbarSelectOption<K>[];
  onChange: (key: K) => void;
  /** Names the control for screen readers, e.g. "Chart timeframe". */
  ariaLabel: string;
  title?: string;
}

/**
 * A one-of-several choice collapsed into a button that shows the current
 * value, for toolbar rows that had grown too wide as a strip of buttons.
 * Portalled to <body> and positioned off the trigger for the same reason as
 * ChartWidget's Levels menu: a react-grid-layout widget is a transformed,
 * overflow-clipped containing block, so a menu positioned inside it gets cut
 * off at the widget's edge. Shares that menu's styles so the two read as one
 * family.
 */
export function ToolbarSelect<K extends string>({ value, options, onChange, ariaLabel, title }: Props<K>) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const selected = options.find((o) => o.key === value) ?? options[0];

  useEffect(() => {
    if (!open) return;
    const button = buttonRef.current;
    if (button) {
      const rect = button.getBoundingClientRect();
      setPos({ top: rect.bottom + 4, left: rect.left });
    }
    function handlePointerDown(e: MouseEvent) {
      const target = e.target as Node;
      if (buttonRef.current?.contains(target)) return;
      if (menuRef.current?.contains(target)) return;
      setOpen(false);
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  // Keyboard users land on the current choice, not the top of the list.
  useEffect(() => {
    if (!open || !pos) return;
    menuRef.current?.querySelector<HTMLButtonElement>('[aria-checked="true"]')?.focus();
  }, [open, pos]);

  function moveFocus(e: ReactKeyboardEvent<HTMLDivElement>) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>("[role=menuitemradio]") ?? []);
    const at = items.indexOf(document.activeElement as HTMLButtonElement);
    const step = e.key === "ArrowDown" ? 1 : -1;
    items[(at + step + items.length) % items.length]?.focus();
  }

  function choose(key: K) {
    setOpen(false);
    buttonRef.current?.focus();
    if (key !== value) onChange(key);
  }

  return (
    <div className="levels-dropdown">
      <button
        ref={buttonRef}
        type="button"
        className="timeframe-button toolbar-select-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`${ariaLabel}: ${selected.label}`}
        onClick={() => setOpen((v) => !v)}
        title={title ?? selected.title}
      >
        {selected.label} ▾
      </button>
      {open &&
        pos &&
        createPortal(
          <div
            className="levels-menu toolbar-select-menu"
            role="menu"
            aria-label={ariaLabel}
            ref={menuRef}
            style={{ top: pos.top, left: pos.left }}
            onKeyDown={moveFocus}
          >
            {options.map((opt, i) => (
              <div key={opt.key} className="toolbar-select-row">
                {i > 0 && opt.group !== options[i - 1].group && <div className="levels-menu-divider" />}
                <button
                  type="button"
                  role="menuitemradio"
                  aria-checked={opt.key === value}
                  className="levels-menu-item toolbar-select-item"
                  title={opt.title}
                  onClick={() => choose(opt.key)}
                >
                  <span className="toolbar-select-check" aria-hidden="true">
                    {opt.key === value ? "✓" : ""}
                  </span>
                  {opt.label}
                </button>
              </div>
            ))}
          </div>,
          document.body,
        )}
    </div>
  );
}
