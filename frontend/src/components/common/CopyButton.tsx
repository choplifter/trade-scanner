import { useState } from "react";

interface CopyButtonProps {
  value: string;
  title?: string;
}

const COPIED_RESET_MS = 1200;

/** Small icon button that copies `value` to the clipboard, independent of
 * any click-to-select behavior on an ancestor element (stops propagation). */
export function CopyButton({ value, title }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const handleClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard
      .writeText(value)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), COPIED_RESET_MS);
      })
      .catch(() => {
        // Clipboard access can be denied by the browser -- not worth surfacing as an error.
      });
  };

  return (
    <button
      type="button"
      className="copy-symbol-button"
      aria-label={title ?? `Copy ${value} to clipboard`}
      title={title ?? `Copy "${value}" to clipboard`}
      onClick={handleClick}
    >
      {copied ? (
        "✓"
      ) : (
        <svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">
          <rect x="5" y="5" width="9" height="9" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
          <path
            d="M3.5 10.5h-1a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1h7a1 1 0 0 1 1 1v1"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.3"
          />
        </svg>
      )}
    </button>
  );
}
