import { useEffect, useRef, useState } from "react";

import {
  BUILT_IN_PROFILES,
  applyProfile,
  deleteProfile,
  saveProfile,
  useDockProfiles,
  useDockReady,
} from "./dockLayouts";

/**
 * The header's switch between named dock arrangements: built-ins
 * ("Screening", "Trading", "0DTE", "Standard") and the user's own, saved
 * from whatever the dock shows. A switch keeps the arrangement left behind
 * under its profile (see applyProfile), so a rearranged built-in shows as
 * edited; ↺ brings the built-in back.
 */
export function DockProfileMenu() {
  const { saved, active } = useDockProfiles();
  const ready = useDockReady();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const rootRef = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    if (!open) return;
    setName(active ?? "");
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, active]);

  const builtInNames = new Set(BUILT_IN_PROFILES.map((p) => p.name));
  const own = Object.keys(saved)
    .filter((n) => !builtInNames.has(n))
    .sort((a, b) => a.localeCompare(b));
  const trimmed = name.trim();
  const replaces = trimmed !== "" && (trimmed in saved || builtInNames.has(trimmed));

  const pick = (profile: string) => {
    if (applyProfile(profile)) setOpen(false);
  };

  return (
    <span className="dock-profile" ref={rootRef}>
      <button
        type="button"
        className="layout-reset-button"
        disabled={!ready}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        title="Named dock arrangements -- switch between them, or save the one on screen under a name"
      >
        Layout: {active ?? "custom"} ▾
      </button>
      {open && (
        <div className="dock-profile-menu" role="menu">
          {BUILT_IN_PROFILES.map((p) => (
            <div key={p.name} className="dock-profile-row">
              <button
                type="button"
                role="menuitem"
                aria-current={active === p.name}
                onClick={() => pick(p.name)}
                title={saved[p.name] ? `${p.title} -- your own saved version` : p.title}
              >
                ★ {p.name}
                {saved[p.name] ? " (edited)" : ""}
              </button>
              {saved[p.name] && (
                <button
                  type="button"
                  className="dock-profile-delete"
                  onClick={() => deleteProfile(p.name)}
                  title={`Forget your version of ${p.name} -- the built-in comes back`}
                  aria-label={`Restore the built-in ${p.name}`}
                >
                  ↺
                </button>
              )}
            </div>
          ))}
          {own.length > 0 && <div className="dock-profile-sep" />}
          {own.map((n) => (
            <div key={n} className="dock-profile-row">
              <button type="button" role="menuitem" aria-current={active === n} onClick={() => pick(n)}>
                {n}
              </button>
              <button
                type="button"
                className="dock-profile-delete"
                onClick={() => deleteProfile(n)}
                title={`Delete the layout "${n}"`}
                aria-label={`Delete ${n}`}
              >
                ×
              </button>
            </div>
          ))}
          <div className="dock-profile-sep" />
          <form
            className="dock-profile-save"
            onSubmit={(e) => {
              e.preventDefault();
              if (saveProfile(trimmed)) setOpen(false);
            }}
          >
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Name, e.g. Earnings week"
              maxLength={40}
              aria-label="Name to save the current layout under"
            />
            <button
              type="submit"
              className="layout-reset-button"
              disabled={!trimmed}
              title="Save what the dock shows now, floating windows included"
            >
              {replaces ? "Update" : "Save"}
            </button>
          </form>
        </div>
      )}
    </span>
  );
}
